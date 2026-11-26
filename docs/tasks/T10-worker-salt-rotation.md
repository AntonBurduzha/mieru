---
id: T10
title: Worker and salt rotation
mode: agent
status: done
features: []
depends: [T05]
branch: t10-worker-salt-rotation
---

# T10 Worker and salt rotation

## Goal
`python -m mieru.worker` keeps only three salts: yesterday's, today's and tomorrow's. It runs at startup and then at every full UTC hour. Each run creates tomorrow's salt if it's missing and deletes every salt dated before yesterday. So the salt for day N is gone at 00:00 UTC on day N+2, 48 hours after it came into use, and no visitor id from that day can be recomputed.

## Context to read
- `docs/architecture/privacy.md`: "Salt lifecycle"
- `docs/architecture/data-model.md` item 8 (`salts`); `docs/architecture/decisions.md` rows 13 (no task queue), 39 (no Postgres, no start) and 40 (ingest creates salts too)
- `docs/conventions.md` sections 2 (layers, time), 3 (the worker command), 5 (`rotate_salts`), 6.3 (fixtures), 6.4 (time-machine), 6.6 (poll, don't sleep), 7.6 (guarantee 3)
- Patterns:
  - `src/mieru/ingest/refresh.py`: `refresh_once` and `refresh_forever`, with a session and a timeout per run, and a failure that is logged and survived;
  - `src/mieru/writer/__main__.py`: the signals, the `AsyncExitStack`, `startup_complete` and `shutdown_complete`;
  - `src/mieru/services/salts.py`: `ensure_salts`;
  - `tests/integration/ingest/test_refresh.py`: a flaky session factory and `capture_logs`;
  - `tests/integration/writer/test_main.py`: the subprocess test.

## Scope
- **In:**
  - `services.salts.rotate_salts`. `repositories.salts` gains `delete_salts_before`, and `insert_salt` now says whether it stored the row;
  - the worker loop:
    - one rotation at startup; if Postgres is unreachable, the worker exits 1 and Docker restarts it;
    - then one rotation at every full UTC hour. A failed run logs `job_failed` and waits for the next hour;
    - SIGTERM and SIGINT end the wait at once, and the worker exits 0;
  - `SessionFactory` moves from `ingest/refresh.py` to `db/session.py`, so ingest and the worker share it;
  - a `worker` config in `.vscode/launch.json`.
- **Out:**
  - the worker in `compose.prod.yaml`, with its memory limit: T11;
  - the privacy test's check that the salt from two days ago is gone: the privacy test (`tests/e2e/test_privacy.py`, conventions 7.6), in T26;
  - job metrics (`mieru.worker.job.failures`), other jobs and a task queue: after the MVP (decision 13);
  - any change to how ingest creates salts (decision 40).
- **Files allowed to change:**
  - `apps/backend/src/mieru/worker/`: new `__init__.py`, `__main__.py`, `rotation.py`
  - `apps/backend/src/mieru/services/salts.py`, `apps/backend/src/mieru/repositories/salts.py`
  - `apps/backend/src/mieru/db/session.py` (gains `SessionFactory`), `apps/backend/src/mieru/ingest/refresh.py` (imports it from there)
  - `.vscode/launch.json` (the `worker` config)
  - Tests: `apps/backend/tests/unit/worker/test_{rotation,main}.py`, `apps/backend/tests/integration/worker/test_{rotation,main}.py`, `apps/backend/tests/integration/services/test_salts.py` (new tests only)
  - Docs: `docs/architecture/{privacy,data-model,decisions}.md`, `docs/conventions.md`, `docs/workflow.md` (section 10), `CLAUDE.md` (Commands)

## Interfaces
- **`mieru.repositories.salts`:**
  - `insert_salt(session, day, salt) -> bool`: the same `ON CONFLICT (day) DO NOTHING`, plus `RETURNING day`. It returns True when this call stored the row, and False when the day already had one;
  - `delete_salts_before(session, day: date) -> int`: `DELETE FROM salts WHERE day < :day`, returning the number of rows deleted.
- **`mieru.services.salts`:**
  - `SaltRotation` (frozen dataclass): `created: bool`, `deleted: int`;
  - `rotate_salts(session, today: date) -> SaltRotation` runs `insert_salt` for `today + 1 day` with `SALT_BYTES` random bytes, then `delete_salts_before(today - 1 day)`. Like `ensure_salts`, it doesn't commit. It never changes the salt of yesterday, today or any later day.
- **`mieru.db.session.SessionFactory`** `= Callable[[], AbstractAsyncContextManager[AsyncSession]]`.
- **`mieru.worker.rotation`:**
  - `next_hour(now: datetime) -> datetime` (pure): the first full hour strictly after `now`, so a `now` exactly on the hour gives the next hour. It keeps `now`'s tzinfo;
  - `JOB_TIMEOUT_S = 10.0`;
  - `async rotate_once(sessions: SessionFactory) -> SaltRotation` runs `rotate_salts(session, clock.today())` in a new session under `asyncio.timeout(JOB_TIMEOUT_S)`. Then it commits and logs `salts_rotated` (`today` as `YYYY-MM-DD`, `created`, `deleted`). A failure raises;
  - `async rotate_forever(sessions: SessionFactory, *, stop: asyncio.Event) -> None`. Until `stop` is set, it waits until `next_hour(clock.now())`, then runs `rotate_once`. A `stop` cuts the wait short, and the loop returns without running. A failure logs `job_failed` (`task="rotate_salts"`, `error_type`) at warning level, and the loop waits for the next hour.
- **`mieru.worker.__main__`:**
  - `main()` reads the settings, calls `configure_logging(service="worker", ...)`, then `asyncio.run(run(settings))`, all under `if __name__ == "__main__"`;
  - `run` makes SIGTERM and SIGINT set `stop`, and opens the engine (`make_engine`) in an `AsyncExitStack`;
  - it runs `rotate_once` outside any `try`, so an unreachable Postgres exits 1;
  - then it logs `startup_complete` (`env`, `version`), runs `rotate_forever`, and logs `shutdown_complete`.
- **Logs** never hold a salt's bytes.
- **Settings:** none new.

## Acceptance criteria
1. Rotating:
   - with salts for D-3 to D+1, `rotate_salts(session, D)` leaves D-1, D and D+1 with their bytes unchanged, and returns `created=False, deleted=2`;
   - with no salts, it stores a 16-byte salt for D+1 and returns `created=True, deleted=0`;
   - a salt for D+5 is kept;
   - a second call returns `created=False, deleted=0` and changes nothing.

   (auto: `uv run --directory apps/backend pytest tests/integration/services/test_salts.py -k rotate`)
2. `insert_salt` returns True when it stores the salt. It returns False when the day already has one, and that salt's bytes stay the same. (auto: `uv run --directory apps/backend pytest tests/integration/services/test_salts.py -k insert`)
3. `next_hour`:
   - 22:59:59 gives 23:00 the same day;
   - 23:00:00 gives 00:00 the next day, and so does 23:59:59.999999;
   - 31 December 23:30 gives 1 January 00:00;
   - for any aware UTC datetime (hypothesis), the result is aware UTC, on the hour, and more than 0 but at most 1 hour after `now`.

   (auto: `uv run --directory apps/backend pytest tests/unit/worker/test_rotation.py`)
4. At the full hour:
   - setup: the clock at D 23:59:58 UTC (time-machine, ticking), and salts for D-2 to D+1;
   - `rotate_forever` runs one rotation at 00:00, after which the salts are D, D+1 and D+2;
   - it logs one `salts_rotated` with `today` D+1 and `deleted` 2, and no log line holds a salt's bytes;
   - setting `stop` ends it within 1 s.

   (auto: `uv run --directory apps/backend pytest tests/integration/worker/test_rotation.py -k full_hour`)
5. When the session factory raises at the 00:00 run, `rotate_forever` logs `job_failed` with `task` `rotate_salts` and the error type. It keeps running, and ends cleanly on `stop`. (auto: `uv run --directory apps/backend pytest tests/integration/worker/test_rotation.py -k failed`)
6. Run on the test databases, `python -m mieru.worker`:
   - deletes a committed salt from before yesterday at startup, and logs `startup_complete`;
   - on SIGTERM logs `shutdown_complete` and exits 0 within 3 s;
   - with Postgres unreachable, exits 1 without `startup_complete`.

   (auto: `uv run --directory apps/backend pytest tests/integration/worker/test_main.py`)
7. Importing `mieru.worker.__main__` reads no settings and configures no logging. (auto: `uv run --directory apps/backend pytest tests/unit/worker/test_main.py`)
8. Run against the developer's own database, the worker deletes an old salt at startup and keeps the current ones. (human: "Test it yourself")

## Tests to write
- `tests/unit/worker/test_rotation.py`: the `next_hour` cases, and the hypothesis property with `@settings(derandomize=True)` (3)
- `tests/unit/worker/test_main.py`: the import check with `import_in_clean_process` (7)
- `tests/integration/services/test_salts.py`: new tests for `rotate_salts` and the return value of `insert_salt` (1, 2). They use `date(2001, 3, 10)`, which is earlier than every real salt. A rotation deletes everything before yesterday, so with a later day `deleted` would also count the real salts that the ingest app's startup committed to `mieru_test`. The existing `ensure_salts` tests stay as they are.
- `tests/integration/worker/test_rotation.py` (4, 5), marked slow:
  - `time_machine.travel(datetime(2001, 3, 10, 23, 59, 58, tzinfo=UTC), tick=True)`;
  - a session factory that yields `db_session`, and fails once for criterion 5;
  - `capture_logs`, with `eventually` polling the captured logs, never the session, while the loop runs. Then `stop.set()` and await the task;
  - asyncio sleeps on the monotonic clock, which time-machine doesn't move, so the wait takes about 2 real seconds.
- `tests/integration/worker/test_main.py` (6), marked slow, following the subprocess pattern of `tests/integration/writer/test_main.py`:
  - the old salt, for `date(2000, 1, 1)`, is committed through a session on `db_engine`;
  - each poll reads it back in a new session;
  - the worker's own startup run deletes it, so the test leaves nothing behind.

## Proof of done
Nothing beyond the standard checks.

## Test it yourself
1. `docker compose up -d`, then add an old salt: `docker compose exec postgres psql -U mieru -c "INSERT INTO salts (day, salt) VALUES ('2000-01-01', decode(md5('old'), 'hex'))"` prints `INSERT 0 1`.
2. From `apps/backend`, run `uv run python -m mieru.worker`. You should see `salts_rotated` with today's date and `deleted=1` or more (any earlier days go too), then `startup_complete`.
3. In a second pane, `docker compose exec postgres psql -U mieru -c "SELECT day FROM salts ORDER BY day"` shows no day before yesterday, and shows tomorrow.
4. Leave it running past a full hour: one more `salts_rotated`, with `created=False deleted=0`.
5. Press Ctrl+C: `shutdown_complete`, and `echo $?` prints `0`. With Postgres stopped (`docker compose stop postgres`), the worker exits 1 with a connection error.

## Where to debug
Paths are under `apps/backend/`. How to use the debugger: `docs/workflow.md`, "Debugging".

| What it decides | Where | Reach it with | Look at |
|---|---|---|---|
| Which salts go | `src/mieru/services/salts.py:38` (`delete_salts_before(today - ONE_DAY)` in `rotate_salts`), the insert on line 37 | the Cursor config `worker` (its startup run) after step 1 of "Test it yourself"; or `pytest: -k` with `rotate_keeps_yesterday` | `today` is the UTC date. Everything before `today - 1 day` goes, so `deleted` counts every older row, the 2000-01-01 one included. Yesterday, today and later days are never touched. `created` is False when ingest already made tomorrow's |
| Waiting for the hour | `src/mieru/worker/rotation.py:42` (`sleep_until`, the loop condition), called from `rotate_forever` on line 55 | `pytest: -k` with `full_hour` | `left` starts near 2 s (the clock is moved to 23:59:58). After each wake the wall clock is read again, so an early wake sleeps the rest and never runs on the old day. A `stop` ends the loop at once, and line 57 returns |
| A failed run | `src/mieru/worker/rotation.py:60` (the `except` in `rotate_forever`) | `pytest: -k` with `failed_run` | `exc` is `ConnectionRefusedError`; only its type is logged. The loop goes back to line 55 and waits for the next hour, not a retry at once |
| One run and its commit | `src/mieru/worker/rotation.py:26` (`rotate_once`: the timeout, the session and the commit on line 28) | `worker`, or `pytest: -k` with `deletes_an_old_salt` | `today` is read once, before the session opens. Without the commit, another session would still see the old salt. A query that hangs raises `TimeoutError` after 10 s |
| Start and stop | `src/mieru/worker/__main__.py:28` (the startup `rotate_once`, outside any `try`); the signal handlers on line 21 | `worker`, then Ctrl+C; or `pytest: -k` with `no_postgres` | With Postgres down, line 28 raises, the engine is disposed and the process exits 1 before `startup_complete`. A signal only sets `stop`, so a run in progress still commits |

## Notes
- The worker runs every hour, not once at 23:00 (the developer's choice, at the spec). A 23:00-only job would delete the salt for day N at 23:00 on day N+2, keeping it up to 71 hours instead of 48. Each run is two small statements.
- A failed hourly run is retried at the next hour, so a Postgres outage at midnight keeps the old salt up to one hour longer.
- `next_hour` is computed from `clock.now()` after every run, never as the last target plus one hour. asyncio sleeps on the monotonic clock, which can wake a little before the wall clock reaches the hour. So `sleep_until` checks `clock.now()` after each sleep and sleeps whatever is left, and a run never sees the old day. Criterion 4 counts exactly one run, so it depends on this.
- Build progress: the `test-writer` wrote the tests (all failing on the missing code), then the code was implemented and the standard checks passed (229 tests). Once `mieru.worker` existed, ruff reordered the imports in the two new `test_rotation.py` files; nothing else in the tests changed.
- `SessionFactory` moved to `mieru.db.session`, so the worker doesn't import from `mieru.ingest`. Both statements in `repositories.salts` use `RETURNING`, because `AsyncSession.execute` is typed as returning `Result`, which has no `rowcount`.
- The ingest app still creates today's and tomorrow's salts on every refresh (decision 40). So the worker's insert is usually a no-op, and the deletes are the part only the worker does.

## Review
**Reviewer, 2026-11-26:** 0 must-fix, 1 should-fix, 4 nits.
1. [should-fix, fixed] `docs/conventions.md` 6.3: it still said every salt test uses `date(2031, 3, 10)`. Rotation tests must use a day before every real salt, and the worker's subprocess test deletes committed past salts in `mieru_test`. The paragraph now says both.
2. [nit, left] `apps/backend/src/mieru/worker/rotation.py:44`: a wall-clock jump forward during the wait (an NTP step, a suspended VM) delays the run by up to the time left in that wait. `timeout=min(left, 60.0)` would re-read the clock every minute.
3. [nit, left] `apps/backend/tests/unit/worker/test_rotation.py`: no deterministic test proves the wall-clock recheck in `sleep_until`. One would freeze the clock just before the hour, check the wait isn't over, then `move_to` the hour.
4. [nit, left] `docs/architecture/privacy.md:24`: the first diagram node says "by 23:00". Ingest and the hourly worker create tomorrow's salt from the start of the day.
5. [nit, left] `apps/backend/src/mieru/worker/__main__.py:28` (design): a startup run that crosses a full hour makes `rotate_forever` skip that hour. That can keep the old salt one more hour, the same bound as a failed run.

**Verifier, 2026-11-26: NEED_HUMAN.** 1-7 pass (exit 0) · 8 human: waiting for the developer · ruff, the format check, mypy and `pytest` (229 passed) exit 0 · Scope clean · Test integrity clean (only the imports of the two new `test_rotation.py` files were reordered by ruff).
Re-run for the finish, after the debug map and Review were written: DONE. 1-7 pass (exit 0) · 8 human: confirmed in run 2 · `pytest` (229 passed), ruff and mypy exit 0 · Scope and test integrity clean · every "Where to debug" line checked (one fixed: the `return` is on line 57).

**Done, 2026-11-26:** the developer ran "Test it yourself" and confirmed it with `/task T10`. Final verdict DONE.
