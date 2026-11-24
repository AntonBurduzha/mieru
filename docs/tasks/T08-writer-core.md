---
id: T08
title: Writer core
mode: human
status: done
features: []
depends: [T06, T07]
branch: t08-writer-core
---

# T08 Writer core

## Goal
`python -m mieru.writer` moves events from the Redis stream `events` into the ClickHouse `events` table. It reads as consumer `writer-1` of group `writers`, inserts batches of up to 5,000 entries or whatever arrived within 1 s, and acknowledges and deletes entries only after ClickHouse confirms the insert. On SIGTERM it stores and acknowledges the batch it holds, then exits 0.

## Context to read
- `docs/architecture/write-path.md`: "What `writer` does per batch" (steps 1, 5, 6; steps 2-4 come with T13 and T14), "Buffering and backpressure", "Delivery guarantees"
- `docs/architecture/data-model.md`: "ClickHouse (events)", and `events` in "Redis keys"
- `docs/architecture/decisions.md` rows 2 (Redis Stream) and 7 (at-least-once plus a dedup token)
- `docs/conventions.md` sections 2 (pipelines), 3 (the writer command; the stream, group and consumer names), 5 (stream entry), 6.2 (the contract test), 6.3 (fixtures), 6.6 (poll, don't sleep)
- Patterns: `mieru/ingest/app.py` (`AsyncExitStack`, `stop`), `mieru/ingest/refresh.py` (a loop that logs a failure and goes on), `mieru/ingest/stream.py` (`StreamEntry.from_fields`), `tests/factories.py` (`EVENT_COLUMNS`), `tests/integration/conftest.py` (`ch`, `site_id`), `tests/unit/conftest.py` (`import_in_clean_process`)

## Scope
- **In:**
  - the group `writers`, created at id `0` with `MKSTREAM` when missing, so entries ingest wrote before the writer's first start are read. A `NOGROUP` error (the stream was deleted) creates it again;
  - each cycle reads the consumer's own pending entries first. When it has none, it collects new entries until it holds `WRITER_BATCH_MAX` or `WRITER_FLUSH_MS` has passed since the first one arrived;
  - a pure step from a stream entry to an `events` row;
  - one `INSERT` per batch, with `insert_deduplication_token` built from the batch's first and last stream ids;
  - after the insert, `XACK` and `XDEL` of every entry in the batch, in one `MULTI`. A failed cycle acknowledges nothing, logs `batch_failed`, and is retried with backoff from 1 s up to 30 s;
  - an entry that doesn't parse is acknowledged, deleted and logged as `entry_invalid`, so it can't block the stream;
  - on SIGTERM or SIGINT: stop collecting, store and acknowledge what was read, close the clients, log `shutdown_complete`, exit 0;
  - a `writer` config in `.vscode/launch.json`.
- **Out:**
  - user agent, source and UTM enrichment: T13. Until then `browser`, `os`, `device`, `source` and `utm_*` are `''`;
  - sessions, the use of `pvid`, the `sessions` table and the writer crash test: T14. Until then `session_id` is `0` and `session_start` is the event's time;
  - the writer in `compose.prod.yaml`, its memory limit and stop grace period: T11;
  - the stream's length and age in a health endpoint: T25;
  - usage counters: after the MVP;
  - `XAUTOCLAIM`: the MVP runs one writer.
- **Files allowed to change:**
  - `apps/backend/src/mieru/writer/`: new `__init__.py`, `__main__.py`, `consumer.py`, `batch.py`, `enrich.py`, `inserter.py`
  - `.vscode/launch.json` (the `writer` config)
  - Tests: `apps/backend/tests/unit/writer/test_{enrich,batch,main}.py`, `apps/backend/tests/unit/contracts/test_stream_entry.py`, `apps/backend/tests/integration/writer/test_{consumer,inserter,main}.py`, `apps/backend/tests/integration/conftest.py` (gains `redis_db` and `eventually`), `apps/backend/tests/integration/ingest/conftest.py` (`redis_db` moves out), `apps/backend/tests/factories.py` (`make_entry`), `apps/backend/tests/integration/clickhouse/test_events_schema.py` (a comment that gives the token to T14)
  - Docs (Claude, in run 2): `docs/architecture/{write-path,data-model,decisions}.md`, `docs/conventions.md`, `docs/workflow.md` (section 10), `CLAUDE.md` (Commands)

## Interfaces
- **`mieru.writer.enrich`** (pure):
  - `EventRow` (Pydantic, frozen): the 18 columns of `events`, in table order;
  - `to_row(entry: StreamEntry) -> EventRow`:
    - `timestamp` is `ts` (ms) as an aware UTC `datetime`; ClickHouse keeps whole seconds;
    - `hostname` is the URL's host, lowercased, without the port;
    - `pathname` is the URL's path without the query or fragment, and `/` when empty. A URL that `urlsplit` refuses gives `''` and `/`;
    - `name`, `visitor_id`, `referrer`, `country` and `props` are `n`, `vid`, `r`, `cc` and `p`;
    - `session_id` is `0`, `session_start` equals `timestamp`, and the enrichment columns are `''`;
    - no field holds `ua` or `org_id`.
- **`mieru.writer.batch`** (pure):
  - `RawEntry = tuple[str, Mapping[str, str]]`, a stream id and its fields;
  - `Batch` (frozen dataclass) with `ids` (every entry read, in stream order), `rows` and `invalid` (an `(id, error type)` pair for each entry that didn't parse), and a `token` property, `f"{ids[0]}:{ids[-1]}"`;
  - `build_batch(entries: Sequence[RawEntry]) -> Batch`: `StreamEntry.from_fields`, then `to_row`. Any exception puts the id in `invalid`;
  - `backoff_s(failures: int) -> float`: 1, 2, 4 ... up to 30.
- **`mieru.writer.inserter`:** `COLUMNS = tuple(EventRow.model_fields)`. `async insert_rows(client: AsyncClient, rows: Sequence[EventRow], *, token: str) -> None` runs one `INSERT INTO events` with `settings={"insert_deduplication_token": token}`.
- **`mieru.writer.consumer`:**
  - `GROUP = "writers"`, `CONSUMER = "writer-1"`;
  - `MAX_BLOCK_MS = 1000`: no blocking read waits longer. That keeps it under the Redis client's 2 s socket timeout, and a stop is seen within a second;
  - `async ensure_group(redis) -> None`: `XGROUP CREATE events writers 0 MKSTREAM`. `BUSYGROUP` isn't an error; `group_created` is logged when it creates the group;
  - `async read_batch(redis, *, batch_max: int, flush_ms: int, stop: asyncio.Event) -> list[RawEntry]`. It returns the consumer's pending entries (id `0`) as they are, never topped up, so a retried batch is the same batch with the same token. Otherwise it collects new entries (`>`) until `batch_max`, until `flush_ms` after the first, or until `stop` is set. It returns `[]` when nothing arrives within one blocking read;
  - `async acknowledge(redis, ids: Sequence[str]) -> None`: `XACK` and `XDEL` in one `MULTI`;
  - `async run_once(redis, ch: ClickHouse, *, batch_max: int, flush_ms: int, stop: asyncio.Event) -> int`: reads, builds, inserts (skipped when there are no rows), acknowledges, and logs `batch_inserted` (`rows`, `invalid`, `ms`) and one `entry_invalid` (`entry_id`, `error_type`) per invalid id. It returns the number of rows. On `NOGROUP` it creates the group and reads again. A failed insert raises before anything is acknowledged;
  - `async run_forever(redis, ch, *, batch_max, flush_ms, stop) -> None`: runs `run_once` until `stop` is set. A failure logs `batch_failed` (`error_type`, `retry_in_s`) and waits `backoff_s`, cut short by `stop`.
- **`mieru.writer.__main__`:** `main()` reads the settings, calls `configure_logging(service="writer", ...)` and `asyncio.run(run(settings))`, all under `if __name__ == "__main__"`. `run` opens Redis (`core.redis.connect`) and `ClickHouse` in an `AsyncExitStack`, then runs `ensure_group`: Redis unreachable means exit 1, and Docker restarts the writer. It logs `startup_complete` (`batch_max`, `flush_ms`), sets SIGTERM and SIGINT to set `stop`, runs `run_forever`, then logs `shutdown_complete`.
- **Logs** never hold a field value of an entry (`ua`, `u`, `r`, `p`).
- **Settings:** the existing `WRITER_BATCH_MAX` (5000) and `WRITER_FLUSH_MS` (1000). Nothing new.

## Acceptance criteria
1. Given entries added with `stream.append`, one `run_once` stores one row per entry with the values from Interfaces and returns the count. After it, `XPENDING` and `XLEN` are 0, so the raw user agent has left Redis. (auto: `uv run --directory apps/backend pytest tests/integration/writer/test_consumer.py -k stored`)
2. Batches by size: with `batch_max=3`, `flush_ms=5000` and 5 entries waiting, a cycle stores 3 rows in under 1 s, and the next cycle stores the other 2. (auto: `uv run --directory apps/backend pytest tests/integration/writer/test_consumer.py -k size`)
3. Batches by time: with one entry and `flush_ms=300`, a cycle stores it, taking at least 0.3 s and under 1.5 s. On an empty stream a cycle returns 0 within 1.5 s. (auto: `uv run --directory apps/backend pytest tests/integration/writer/test_consumer.py -k "by_time or empty"`)
4. Acknowledgement only after the insert: with ClickHouse unreachable, a cycle raises, nothing is stored, and every entry is still pending and still in the stream. The next cycle with ClickHouse back stores each of them once. (auto: `uv run --directory apps/backend pytest tests/integration/writer/test_consumer.py -k failed_insert`)
5. Entries added before the group exists are read. After `DEL events` and a new append, the next cycle creates the group again and stores the new entry. (auto: `uv run --directory apps/backend pytest tests/integration/writer/test_consumer.py -k "before_group or deleted"`)
6. The same rows inserted twice under one token are stored once; under two different tokens they are stored twice. (auto: `uv run --directory apps/backend pytest tests/integration/writer/test_inserter.py -k token`)
7. An entry that doesn't parse is acknowledged, deleted, and logged once as `entry_invalid` with its id, and the valid entries of its batch are stored. No log line holds a field value of any entry. (auto: `uv run --directory apps/backend pytest tests/integration/writer/test_consumer.py -k invalid`)
8. `to_row` maps every field as Interfaces says. It never raises for a URL that passed ingest's validation (hypothesis), and its row holds neither `ua` nor `org_id`. Contract: an entry made by ingest's real `process`, sent through `to_fields` and `build_batch`, gives the expected row. `backoff_s` gives 1, 2, 4 ... 30. (auto: `uv run --directory apps/backend pytest tests/unit/writer tests/unit/contracts`)
9. `inserter.COLUMNS` equals the columns of the ClickHouse `events` table, in order. (auto: `uv run --directory apps/backend pytest tests/integration/writer/test_inserter.py -k columns`)
10. Sent SIGTERM while it holds a partly collected batch, `python -m mieru.writer` stores and acknowledges that batch, logs `shutdown_complete` and exits 0 within 3 s. An idle writer does the same. With Redis unreachable it exits 1 instead of starting. (auto: `uv run --directory apps/backend pytest tests/integration/writer/test_main.py`)
11. Importing `mieru.writer.__main__` reads no settings and configures no logging. (auto: `uv run --directory apps/backend pytest tests/unit/writer/test_main.py`)
12. A page view sent to a local ingest reaches local ClickHouse through a running writer, and a ClickHouse outage delays it without losing it. (human: "Test it yourself")

## Tests to write
- `tests/unit/writer/test_enrich.py`: the mapping (host, path, timestamp, props, placeholders), no `ua`, and the hypothesis test with `@settings(derandomize=True)` (8)
- `tests/unit/writer/test_batch.py`: valid and invalid entries (a missing field, bad JSON in `p`), the token, `backoff_s` (7, 8)
- `tests/unit/writer/test_main.py`: the import check with `import_in_clean_process` (11)
- `tests/unit/contracts/test_stream_entry.py`: from ingest's `process` to the writer's row (8)
- `tests/integration/writer/test_consumer.py`: criteria 1-5 and 7. Rows are counted by the test's `site_id`. The unreachable ClickHouse is a `ClickHouse` whose `clickhouse_url` points at port 1.
- `tests/integration/writer/test_inserter.py`: criteria 6 and 9
- `tests/integration/writer/test_main.py`: criterion 10. It runs the module in a subprocess, with the test settings (`ENV=test`, Redis database 15, `mieru_test`) and `WRITER_FLUSH_MS=10000` in its environment, and uses `eventually` to wait until the entries are pending before it sends the signal.
- Shared fixtures: `redis_db` moves from `tests/integration/ingest/conftest.py` to `tests/integration/conftest.py`, which also gains `async eventually(check, *, timeout_s=5)` (ruff's ASYNC109 refuses a parameter named `timeout`). `tests/factories.py` gains `make_entry(site_id, **overrides) -> StreamEntry`.

## Proof of done
Nothing beyond the standard checks.

## Test it yourself
1. `docker compose up -d`. Then, from `apps/backend`, `uv run python -m mieru.tools.ch_migrate` prints `up to date`. You need a site `example.com` from T07; if it's gone, recreate it with `create_site`.
2. Start ingest (`uv run uvicorn --factory mieru.ingest.app:create_app --reload --port 8001`) and, in a second pane, `uv run python -m mieru.writer`: `startup_complete`.
3. Send the T07 curl for `https://example.com/pricing`. About a second later the writer logs `batch_inserted rows=1`. Then `docker compose exec clickhouse clickhouse-client --user mieru --password mieru -q "SELECT name, hostname, pathname, country FROM mieru.events ORDER BY timestamp DESC LIMIT 1"` shows `pageview example.com /pricing ZZ`, and `docker compose exec redis redis-cli XLEN events` shows `0`.
4. `docker compose stop clickhouse`, then the same curl: `batch_failed` lines with `retry_in_s` 1, 2, 4, and `redis-cli XPENDING events writers` shows 1 pending entry. After `docker compose start clickhouse`, `batch_inserted rows=1` appears within 30 s, and the row count has gone up by exactly one.
5. Press Ctrl+C in the writer pane: `shutdown_complete`, and `echo $?` prints `0`.

## Where to debug
Paths are under `apps/backend/`. How to use the debugger: `docs/workflow.md`, "Debugging".

| What it decides | Where | Reach it with | Look at |
|---|---|---|---|
| Insert first, acknowledge after | `src/mieru/writer/consumer.py:93` (`insert_rows` in `run_once`), the `acknowledge` on line 95 | the Cursor config `writer` and the curl from "Test it yourself"; or `pytest: -k` with `failed_insert` | `batch.token` is `<first id>:<last id>`, and `batch.ids` holds the invalid ids too. With ClickHouse down, line 93 raises and line 95 never runs: `XPENDING events writers` still counts the entries |
| A retry or a new batch | `src/mieru/writer/consumer.py:40` (`read_batch`, the pending read), the collect loop on line 47 | `writer` with `docker compose stop clickhouse`, one curl, then `start`; or `pytest: -k` with `by_size` or `by_time` | `pending` is non-empty only after a failed cycle, and it comes back untopped, so the token repeats. In the loop, `left_ms` falls to 0 after `flush_ms`; `stop.is_set()` ends it early on SIGTERM |
| Failure and backoff | `src/mieru/writer/consumer.py:115` (`run_forever`, the `except`), the pause on line 121 | `writer` with ClickHouse stopped | `failures` and `delay` go 1, 2, 4 ... 30 and reset after a good cycle. Ctrl+C during the pause wakes `stop.wait()` and the loop ends |
| Host and path from the URL | `src/mieru/writer/enrich.py:65` (`split_url`), called from `to_row` on line 40 | `pytest: -k` with `host_and_path` or `any_url_that_ingest_accepts` | `parsed.hostname` drops the port and lowercases only up to a `%`, hence the `.lower()`. A `ValueError` from `urlsplit` gives `''` and `/` |
| Stop and startup | `src/mieru/writer/__main__.py:22` (the signal handlers), `ensure_group` on line 30 | `writer`, then Ctrl+C; or `pytest: -k` with `sigterm` or `no_redis` | A signal only sets `stop`; the batch in hand is still stored. With Redis down, line 30 raises and the process exits 1 before `startup_complete` |

## Notes
- The dedup token moved here from T14 (the developer's choice, at the spec). T14 keeps the crash test that proves it end to end.
- Claude wrote the tests and the shared fixtures before the build (human mode, from 2026-11-22); the developer writes the code that turns them green.
- One writer in the MVP, so every pending entry belongs to `writer-1`. A second writer would need `XAUTOCLAIM`.
- `session_id = 0` marks rows written before T14. The `sessions` view that T14 adds sees only later inserts.
- An invalid entry is dropped, not moved to a dead-letter stream, because a dead-letter copy would keep the raw user agent in Redis (conventions 7.6, guarantee 4).
- Found while preparing the build: `urlsplit(...).hostname` lowercases only up to a `%`, so `to_row` lowercases again (hypothesis found `http://%A`); the driver reads `FixedString(2)` back as `bytes`; redis-py types the RESP3 reply of `XREADGROUP` as a dict but returns the list shape, hence a `cast`. Without a token, ClickHouse already drops an identical block, so two different batches with identical rows lose one (criterion 6 shows it).
- The developer's comment on `stream_high_water` in `core/config.py` was outside the allowed files, and it described the writer lagging rather than ingest dropping. Reverted at the developer's choice.
- For T11: give the writer's container a stop grace period longer than one insert (the client's `send_receive_timeout` is 30 s). A SIGKILL in the middle of an insert loses nothing: the batch stays pending, and the token makes the retry a no-op.

## Review
**Reviewer, 2026-11-24:** 0 must-fix, 0 should-fix, 6 nits.
1. [nit, left] `apps/backend/src/mieru/writer/enrich.py:69`: `if parsed:` is always true (a `SplitResult` is a 5-tuple), so `split_url` reads as if it could fall off the end.
2. [nit, left] `apps/backend/src/mieru/writer/__main__.py:30`: an unreachable Redis exits 1 through a plain traceback, not a `startup_failed` log line.
3. [nit, left] `apps/backend/tests/integration/writer/test_consumer.py:185`: the no-field-values check looks only for the user agent, not `u` or `r`.
4. [nit, fixed] `docs/architecture/data-model.md`: `ZZ` comes from ingest's `cc`, not from the writer.
5. [nit, fixed] `docs/conventions.md` section 2: the layering diagram gains `writer/batch`.
6. [nit, fixed] the spec: "Where to debug" written.
Also left: the typo "enties" at `apps/backend/src/mieru/writer/batch.py:23`, and `count_rows` copied in three writer integration tests.

**Verifier, 2026-11-24: DONE.** 1-11 pass (exit 0) · 12 human: confirmed in run 2 · ruff, mypy and `pytest` (213 passed) exit 0 · Scope clean (the out-of-scope comment in `core/config.py` was reverted at the developer's choice) · Test integrity clean. Re-run after the doc fixes: DONE again, and every "Where to debug" line checked.

**Done, 2026-11-24:** the developer ran "Test it yourself" and confirmed it with `/task T08`. Final verdict DONE.
