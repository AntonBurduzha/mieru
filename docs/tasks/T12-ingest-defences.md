---
id: T12
title: Ingest defences
mode: human
status: build
features: [I2]
depends: [T11]
branch: t12-ingest-defences
---

# T12 Ingest defences

## Goal
Ingest drops bots, spam referrers, over-limit traffic and events past the stream's high-water mark, each with `202` and its reason. While Redis is unreachable, events wait in memory instead of being lost. Every drop is counted by reason and logged once a minute, so a site owner's numbers hold real visitors only and the operator can see what was turned away.

## Context to read
- `docs/architecture/write-path.md`: "What `ingest` does per request" (steps 4, 8 and 9), "Buffering and backpressure"
- `docs/architecture/data-model.md`: "Redis keys" (`rl:{site_id}:{minute}`); `docs/architecture/decisions.md` rows 8 (counted drops), 23 (two Redis calls), 38 (site cache)
- `docs/conventions.md` sections 2, 3 (drop reasons), 5 (the stream entry, `rate_limit_per_min` 6000), 6.3 (Redis fixtures, `eventually`), 6.6, 7.2
- Patterns, under `apps/backend/`:
  - `src/mieru/ingest/refresh.py` (`refresh_forever`: a loop that survives failures), `src/mieru/ingest/app.py` (`lifespan`, `stop`);
  - `tests/integration/ingest/test_event.py` (the Redis-down client swap, `capture_logs`), `tests/integration/conftest.py` (`serve`, `eventually`).

## Scope
- **In:** the bot and spam-referrer filters, in memory; the per-site rate limit; backpressure; the fallback queue while Redis is down, drained in the background and at shutdown; drop counters and their log line.
- **Out:** the country (T13); `over_quota` from usage, and the OpenTelemetry metric `mieru.ingest.dropped` (after the MVP); the stream's age in `/health/ready` (T25); the deploy drill (T15); a sliding-window or per-IP limit (the IP is never kept); refreshing the spam list automatically.
- **Changed on purpose:** `tests/integration/ingest/test_event.py::test_redis_unavailable_still_answers_202` expects an outage to drop the event. That was T07's stopgap ("until T12" in `write-path.md`). It is replaced by criterion 6 (the developer's choice, at the spec).
- **Files allowed to change:**
  - `apps/backend/src/mieru/ingest/`: new `bots.py`, `referrer_spam.txt`, `delivery.py`, `counters.py`; changed `pipeline.py`, `stream.py`, `site_cache.py`, `refresh.py`, `routes.py`, `app.py`
  - Tests: new `apps/backend/tests/unit/ingest/test_{bots,delivery,counters}.py` and `apps/backend/tests/integration/ingest/test_defences.py`; new tests only in `tests/unit/ingest/test_{pipeline,stream}.py` and `tests/integration/ingest/test_refresh.py`; the one replaced test in `tests/integration/ingest/test_event.py`; `tests/integration/ingest/conftest.py`, `tests/factories.py`
  - Docs: `docs/architecture/{write-path,data-model,decisions,observability}.md`, `docs/conventions.md` (sections 1, 3, 5, 6.3), `ops/README.md`

## Interfaces
- **`mieru.ingest.bots`** (pure):
  - `is_bot(user_agent: str) -> bool`: True when empty or only spaces, else a case-insensitive search of `BOT_PATTERN`, whose tokens are `(?<!cu)bot` (a Cubot phone isn't a bot), `crawl`, `spider`, `slurp`, `headless`, `phantomjs`, `lighthouse`, `facebookexternalhit`, `curl/`, `wget/`, `python-requests`, `python-urllib`, `python-httpx`, `aiohttp`, `go-http-client`, `okhttp`, `java/`, `node-fetch`, `axios/`, `libwww-perl`, `scrapy`;
  - `SPAM_LIST_PATH = Path(__file__).parent / "referrer_spam.txt"`: Matomo's `spammers.txt` (public domain) at commit `6cd0b655c740da5bb39d98cd1bea88e2e0660e80`, with a first `#` line naming the source and the commit;
  - `load_spam_domains(path: Path) -> frozenset[str]` skips blank and `#` lines and lowercases; `spam_domains() -> frozenset[str]` is `functools.cache` over the shipped file;
  - `is_spam_referrer(referrer: str | None, domains: frozenset[str]) -> bool`: the `urlsplit` hostname without one leading `www.`, matched itself or by any parent domain (`a.b.spam.com` matches `spam.com`). None, `""` or a URL that doesn't parse is False.
- **`mieru.ingest.pipeline`:** `process` keeps its signature. The order is `unknown_site`, `blocked`, `over_quota`, `bot`, `spam_referrer`, `no_salt`. `Accepted` gains `rate_limit_per_min: int`, the site's.
- **`CachedSite`** gains `rate_limit_per_min: int = DEFAULT_RATE_LIMIT_PER_MIN` (`mieru.models.site`); `refresh_once` fills it from the column.
- **`mieru.ingest.stream`:** `rate_key(site_id, now) -> str` is `rl:{site_id}:{epoch minute}` (`rl:7:29910960` at 2026-11-14 12:00 UTC); `count_event(redis, site_id, now) -> int` runs `INCR` and `EXPIRE <key> 120 NX` in one pipeline without `MULTI` and returns the count; `length(redis) -> int` is `XLEN events`; `append_many(redis, entries)` sends their `XADD`s in one `MULTI`.
- **`mieru.ingest.delivery`:**
  - `FallbackQueue(maxsize)`: FIFO; `put(entry) -> bool` is False when full (size 0 refuses all); `oldest(n)`, `remove_oldest(n)`, `len()`;
  - `Delivery(redis, *, high_water, fallback_size)` with public `redis`, `queue`, `stream_length` (0 until the first check);
  - `send(accepted, *, now) -> DropReason | None`, in order: `stream_length > high_water` is `backpressure`, with no Redis call; a non-empty queue means an outage, so the event joins it without touching Redis (full: `redis_unavailable`); else `count_event`, over the site's limit is `rate_limited`, otherwise `append`. A `RedisError` there logs `redis_unavailable` (`error_type`) and the event joins the queue. A full queue logs `fallback_full` (`size`) once per outage;
  - `check_stream()`: one `XLEN`; a `RedisError` keeps the last value;
  - `drain() -> int`: the oldest entries, 500 per `append_many`, removed only after Redis confirms; logs `fallback_drained` (`events`) when the queue empties; a `RedisError` keeps the queue and is not raised;
  - `run(*, interval_s=1.0)`: `check_stream`, then `drain`, every second until cancelled;
  - `flush(*, timeout_s=5.0) -> int`: a last `drain` at shutdown; what is left is logged as `fallback_lost` (`events`).
- **`mieru.ingest.counters`:** `Counters` (`accepted: int`, `dropped: Counter[DropReason]`, `take()` returns both and resets); `report(counters)` logs `events_counted` (`accepted`, `dropped`: the non-zero reasons) and logs nothing when all are zero; `report_forever(counters, *, interval_s=60.0)`.
- **Routes and app:** `event()` reads `clock.now()` once, runs `process` then `delivery.send`, and counts every outcome; a queued event gets a plain `202`. The lifespan builds `state.delivery` (from `STREAM_HIGH_WATER`, `INGEST_FALLBACK_QUEUE`; both exist) and `state.counters`, warms `spam_domains()` (`startup_complete` gains `spam_domains`), and starts `delivery.run()` and `report_forever`. At shutdown it stops both, runs `flush()`, then a last `report()`, before Redis closes.
- **Logs** never hold an IP, a user agent or a referrer. No new settings.

## Acceptance criteria
1. Bot user agents are dropped as `bot`: empty, Googlebot, bingbot, HeadlessChrome, curl, python-requests. Chrome, Firefox, Safari and Edge (desktop and mobile), a Cubot phone and `MieruTest/1.0` pass. (auto: `uv run --directory apps/backend pytest tests/unit/ingest -k bot`)
2. A referrer on the list, a subdomain of one, or its `www.` form is dropped as `spam_referrer`; other referrers, none and an unparseable one pass. The shipped list loads over 2,000 domains. (auto: `uv run --directory apps/backend pytest tests/unit/ingest -k spam`)
3. Through the app, a bot or spam event gets `202` with its reason and leaves no `rl:*` key and an empty stream. An unknown domain with a bot user agent is `unknown_site`. (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_defences.py -k "bot or spam"`)
4. With a site at `rate_limit_per_min=3` and the clock frozen, the 4th event in a minute is `rate_limited` and the stream holds 3; `rl:{site_id}:{minute}` has a TTL in (0, 120]; in the next minute the site is accepted again. (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_defences.py -k rate_limited`)
5. After `check_stream` sees the stream over `STREAM_HIGH_WATER`, an event is `backpressure`, with nothing added and no `rl:*` key; once a check sees it below, events are accepted again. (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_defences.py -k backpressure`)
6. With Redis unreachable, an event gets `202` with no header and is held; once Redis answers, `drain()` writes the held events in post order and logs `fallback_drained`; the background task does this by itself within 5 s; leaving the lifespan writes what is held; with a full queue an event is `redis_unavailable`, and `fallback_full` is logged once. (auto: `uv run --directory apps/backend pytest tests/integration/ingest -k "fallback or redis_unavailable"`)
7. Each drop counts under its reason and each kept or held event as accepted. `report` logs `events_counted` with only the non-zero counts and resets; a second call logs nothing; the line holds no IP or user agent. (auto: `uv run --directory apps/backend pytest tests/unit/ingest tests/integration/ingest -k counted`)
8. `refresh_once` puts each site's `rate_limit_per_min` in the cache. (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_refresh.py -k rate_limit`)
9. Against the local app: a bot `curl` is dropped and a browser one kept; with Redis stopped and started again, the held event reaches the stream; an `events_counted` line appears within a minute. (human: "Test it yourself")

## Tests to write
Names that prove a drop contain `drop`, for the feature check (`-k drop`).
- `tests/unit/ingest/test_bots.py` (1, 2): `is_bot` cases (parametrized), `is_spam_referrer` cases, `load_spam_domains` on a `tmp_path` file, the shipped list's size
- `tests/unit/ingest/test_pipeline.py`, new tests (1-3): `bot` and `spam_referrer` drops, the order (unknown site before bot, bot before `no_salt`), `Accepted.rate_limit_per_min`
- `tests/unit/ingest/test_stream.py`, new test: `rate_key`
- `tests/unit/ingest/test_delivery.py`: `FallbackQueue` FIFO order, the bound, size 0
- `tests/unit/ingest/test_counters.py` (7): `take` resets, `report` lines via `capture_logs`
- `tests/integration/ingest/test_defences.py` (3-7), Redis database 15: bot, spam, rate limit (`time_machine` frozen, then moved a minute), backpressure (`stream_high_water` small via `settings.model_copy`, real `XADD`s, `check_stream()` called directly), fallback (swap `state.delivery.redis` for a client on port 1; `drain()`; the background run with `eventually`; the lifespan exit through `serve`), counters through the app
- `tests/integration/ingest/test_event.py`: `test_redis_unavailable_still_answers_202` replaced by its criterion 6 form
- `tests/integration/ingest/test_refresh.py`, new test (8)

## Proof of done
- `uv run --directory apps/backend pytest -m e2e`: the pipeline test still passes with the defences on.
- `cd apps/backend && uv build --wheel && unzip -l dist/*.whl | grep referrer_spam.txt`: the list ships in the package.

## Test it yourself
1. `docker compose up -d`, then the `ingest` command from `CLAUDE.md` with `--no-proxy-headers`; a site of yours exists (`mieru.tools.create_site`). `startup_complete` shows `spam_domains` over 2,000.
2. `curl -si -X POST localhost:8001/api/event -d '{"n":"pageview","d":"<domain>","u":"https://<domain>/"}'`: `202` with `x-mieru-dropped: bot` (curl's own user agent). With `-A 'Mozilla/5.0 Firefox/140.0'`: `202` and no header; `docker compose exec redis redis-cli XLEN events` goes up by one.
3. The browser request with `"r":"https://0-0.fr/"` in the body: `x-mieru-dropped: spam_referrer`.
4. `docker compose stop redis`, post the browser request: `202`, no header, and a `redis_unavailable` log line. `docker compose start redis`: within a few seconds `fallback_drained events=1`, and `XLEN` is up by one.
5. Within a minute: an `events_counted` line with `accepted` and `dropped` holding `bot` and `spam_referrer`.

## Where to debug
Written before the developer's test.

## Notes
- Drop counts stay in memory and are logged every 60 s (the developer's choice, at the spec): no Redis write per drop, and the counts survive a Redis outage. The cost: two replicas are summed by hand, and a crash loses up to a minute of counts. OpenTelemetry replaces the line after the MVP.
- While the queue holds anything, new events join it without trying Redis, so no request waits on a dead Redis (up to the 2 s socket timeout), and the stream keeps post order. Held events skip the rate limit. A reply lost after `EXEC` can write a batch twice; the writer's dedup token doesn't cover that, because the stream ids differ.
- The rate limit is a fixed window per UTC minute, shared by every ingest replica through Redis. At a window edge a site can get up to twice its limit in a minute.

## Review
