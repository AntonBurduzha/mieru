---
id: T07
title: Ingest core
mode: human
status: build
features: [I1]
depends: [T05]
branch: t07-ingest-core
---

# T07 Ingest core

## Goal
`POST /api/event` works: a well-formed event for a registered, active site gets `202` and lands in the Redis stream `events` with its daily visitor id, ready for the writer (T08). An event for an unknown domain gets `202` with `X-Mieru-Dropped: unknown_site` and goes nowhere. The client IP is used for the hash and never leaves the request.

## Context to read
- `docs/architecture/write-path.md`, "What `ingest` does per request": steps 1-3, 5, 7-9 (bots, country and the rate limit come later)
- `docs/architecture/privacy.md`: the visitor id recipe, the salt lifecycle, sessions across midnight
- `docs/architecture/api.md`: the ingest body
- `docs/conventions.md` sections 2 (pipelines), 5 (stream entry, drop reasons), 6.3 (fixtures), 7.2 (ingest edge)
- Patterns: `mieru/ingest/app.py` (lifespan with `AsyncExitStack`), `mieru/services/sites.py` (service over repositories), `tests/integration/conftest.py` (`serve`, `db_session`)

## Scope
- **In:**
  - body read with an 8 KB cap and validated by a Pydantic model; anything malformed or over a limit is `400 invalid_event`;
  - an in-process site cache and salt cache, loaded at startup and refreshed from Postgres every 30 s by a background task;
  - `ensure_salts`: today's and tomorrow's salts are created when missing, so ingest works before the worker (T10) exists;
  - the visitor id (BLAKE2b, keyed by the day's salt) and yesterday's id;
  - drops for `unknown_site`, `blocked`, `over_quota`, `no_salt` and `redis_unavailable`, each a `202` with the header;
  - `XADD` of the stream entry from conventions section 5.
- **Out:**
  - bots, spam referrers, the rate limit, backpressure, the fallback queue and drop counters: T12;
  - country (`cc` is `ZZ` until T13);
  - deleting old salts and creating tomorrow's at 23:00: T10;
  - invalidation through `sites:version`: T17;
  - reading the stream: T08.
- **Files allowed to change:**
  - `apps/backend/src/mieru/ingest/`: `app.py`, `routes.py`, and the new `pipeline.py`, `visitor.py`, `site_cache.py`, `salts.py`, `stream.py`, `refresh.py`
  - `apps/backend/src/mieru/core/errors.py` (`InvalidEvent`)
  - `apps/backend/src/mieru/repositories/sites.py`, and the new `repositories/salts.py`, `services/salts.py`
  - `apps/backend/pyproject.toml`, `apps/backend/uv.lock` (dev dependency `hypothesis`)
  - `compose.prod.yaml` (ingest depends on a healthy Postgres)
  - Tests: `apps/backend/tests/unit/ingest/test_{pipeline,visitor,stream,site_cache}.py`, `apps/backend/tests/integration/ingest/{conftest,test_event,test_refresh}.py`, `apps/backend/tests/integration/services/test_salts.py`, `apps/backend/tests/factories.py`
  - Docs (Claude, in run 2): `docs/architecture/{write-path,privacy,api,data-model,decisions}.md`, `docs/conventions.md`

## Interfaces
- **Endpoint:** `POST /api/event`, any content type (the tracker sends `text/plain`). `202` with an empty body; `X-Mieru-Dropped: <reason>` when dropped; `400` `{"error": {"code": "invalid_event", "message": "Malformed event", "details": null}}`, never echoing the input. CORS stays in Caddy (`Access-Control-Allow-Origin: *`).
- **Body** (`pipeline.IngestEvent`, extra keys ignored): `n` 1-120 chars; `d` 1-253; `u` 1-2,000 and starting `http://` or `https://`; `r` optional, up to 2,000; `p` optional `dict[str, str]`, up to 30 entries, keys 1-64, values up to 2,000. Prop values are strings: the tracker converts numbers. The whole body is at most `MAX_BODY_BYTES = 8192`.
- **`mieru.ingest.pipeline`** (pure, no I/O):
  - `DropReason = Literal["unknown_site", "blocked", "over_quota", "no_salt", "bot", "spam_referrer", "rate_limited", "backpressure", "redis_unavailable"]`
  - `parse_event(body: bytes) -> IngestEvent`, raising `InvalidEvent`
  - `process(event, *, sites: SiteCache, salts: SaltCache, ip: str, user_agent: str, now: datetime) -> Accepted | Dropped`. `Accepted(entry: StreamEntry)` and `Dropped(reason: DropReason)` are frozen dataclasses. Order: site lookup, status, today's salt.
- **`mieru.ingest.visitor.visitor_id(salt: bytes, domain: str, ip: str, user_agent: str) -> int`:** `blake2b(f"{domain}|{ip}|{user_agent}".encode(), key=salt, digest_size=8)`, read as a big-endian unsigned integer. `domain` is the site's stored domain.
- **`mieru.ingest.site_cache`:** `CachedSite(id: int, org_id: UUID, domain: str, status: str)` (frozen dataclass); `SiteCache.get(domain) -> CachedSite | None` normalizes with `normalize_domain`; `SiteCache.replace(sites: Iterable[CachedSite])` swaps the whole dict.
- **`mieru.ingest.salts`:** `SaltCache.get(day: date) -> bytes | None`; `SaltCache.replace(salts: Mapping[date, bytes])`.
- **`mieru.ingest.refresh`:** `refresh_once(session, sites, salts, *, today: date) -> None` (runs `ensure_salts`, commits, loads every site); `refresh_forever(sessionmaker, sites, salts, *, interval_s: float = 30)` logs `cache_refresh_failed` with `error_type` and keeps going.
- **Startup:** the lifespan opens a Postgres engine and runs `refresh_once` before serving. If Postgres can't be reached, startup fails and Docker restarts the container. `/health/ready` stays Redis only. Shutdown cancels the refresh task, then disposes the engine.
- **`mieru.ingest.stream`:**
  - `STREAM = "events"`;
  - `StreamEntry` (Pydantic, frozen) with `v=1`, `site_id`, `org_id`, `ts` (ms), `n`, `u`, `r` (`""` when absent), `ua`, `vid`, `pvid` (`None` when yesterday's salt is missing), `cc` (`"ZZ"`), `p`;
  - `to_fields() -> dict[str, str]`: every value a string, `pvid` `""` when `None`, `p` compact JSON;
  - `StreamEntry.from_fields(fields: Mapping[str, str])`: the inverse, for the writer;
  - `async append(redis, entry) -> str`: plain `XADD`, no `MAXLEN` (trimming would lose unprocessed entries).
- **Client IP:** `X-Real-IP`, else the TCP peer (`request.client.host`, local runs without Caddy), else `""`. `X-Forwarded-For` and `Forwarded` are never read. The user agent is the `User-Agent` header, or `""`.
- **Repositories:**
  - `list_all_sites(session) -> Sequence[Site]`: system-wide, for the ingest cache only, the one exception to the scoping rule;
  - `get_salts(session, days: Sequence[date]) -> dict[date, bytes]`;
  - `insert_salt(session, day, salt) -> None`: `INSERT ... ON CONFLICT (day) DO NOTHING`.
- **Service:** `services/salts.ensure_salts(session, today) -> dict[date, bytes]` inserts 16 bytes from `secrets.token_bytes` for today and tomorrow, then returns the salts that exist for yesterday, today and tomorrow. Two replicas racing end with the same salts.
- **Error:** `core.errors.InvalidEvent(AppError)`, `code = "invalid_event"`, `status_code = 400`.

## Acceptance criteria
1. Given an active site, a valid page view gets `202` with no `X-Mieru-Dropped`, and the stream holds one entry. The entry's fields equal the request, the site's id and org id, `ts` at the frozen clock, `cc=ZZ` and `v=1`. (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_event.py -k accepted`)
2. An event for an unregistered domain gets `202` with `X-Mieru-Dropped: unknown_site`, and the stream stays empty. `WWW.Example.COM` finds the site `example.com`. (auto: `uv run --directory apps/backend pytest tests/unit/ingest tests/integration/ingest -k unknown_site`)
3. A `blocked` or `over_quota` site gets `202` with that reason, and nothing is appended. (auto: `uv run --directory apps/backend pytest tests/unit/ingest/test_pipeline.py -k status`)
4. Each limit at its maximum is accepted, and one over is `400 invalid_event` with nothing appended: body bytes, `n`, `u`, `r`, prop count, key and value length. So are a non-http URL, a missing `n`, `d` or `u`, and non-JSON. (auto: `uv run --directory apps/backend pytest tests/unit/ingest/test_pipeline.py tests/integration/ingest/test_event.py -k invalid`)
5. Any byte string either parses or raises `InvalidEvent`, never anything else (hypothesis). (auto: `uv run --directory apps/backend pytest tests/unit/ingest/test_pipeline.py -k any_body`)
6. The visitor id follows the recipe in Interfaces, fits in 64 bits, and changes when the salt, domain, IP or user agent changes (hypothesis). (auto: `uv run --directory apps/backend pytest tests/unit/ingest/test_visitor.py`)
7. The IP comes from `X-Real-IP`, and a different `X-Forwarded-For` doesn't change `vid`. No field of the stream entry contains `203.0.113.77`. (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_event.py -k ip`)
8. Around midnight UTC:
   - an event at 00:00:00 has a new `vid`, and its `pvid` equals the `vid` of the same visitor at 23:59:59;
   - `pvid` is empty when yesterday's salt is missing;
   - a missing salt for today drops the event as `no_salt`.

   (auto: `uv run --directory apps/backend pytest tests/unit/ingest/test_pipeline.py -k salt`)
9. `refresh_once` loads the sites and creates today's and tomorrow's salts. A second call keeps the same salts, and a site added between the calls appears after the second. (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_refresh.py tests/integration/services/test_salts.py`)
10. When Redis can't be reached, a valid event gets `202` with `X-Mieru-Dropped: redis_unavailable`. (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_event.py -k redis_unavailable`)
11. The ingest app doesn't start when Postgres can't be reached. (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_refresh.py -k startup`)
12. A real `curl` to a local ingest puts an entry in local Redis, and an unknown domain gets the header. (human: "Test it yourself")

## Tests to write
- `tests/unit/ingest/test_pipeline.py`: limits (4), `test_any_body_*` (5), `test_unknown_site_*`, `test_status_*` (2, 3), `test_*salt*` with `time-machine` (8)
- `tests/unit/ingest/test_visitor.py`: the recipe and the property tests (6)
- `tests/unit/ingest/test_stream.py`: `to_fields` and `from_fields` round trip, `pvid` `""`, string values only
- `tests/unit/ingest/test_site_cache.py`: normalized lookup, `replace` drops removed sites
- `tests/integration/ingest/test_event.py`: `test_accepted_*`, `test_unknown_site_*`, `test_invalid_*`, `test_*ip*`, `test_redis_unavailable` (1, 2, 4, 7, 10). The fixture in `conftest.py` creates sites in `db_session` and runs `refresh_once` on the app's caches, because the app's own engine can't see the rolled-back rows (conventions 6.3).
- `tests/integration/ingest/test_refresh.py` (9, 11), `tests/integration/services/test_salts.py` (9)

## Proof of done
Nothing beyond the standard checks.

## Test it yourself
1. `docker compose up -d`, then from `apps/backend`: `uv run python -m mieru.tools.create_site --domain example.com --org-name Demo`. It prints `site_id=...`.
2. Start ingest: `uv run uvicorn --factory mieru.ingest.app:create_app --reload --port 8001`.
3. `curl -i -X POST localhost:8001/api/event -H 'Content-Type: text/plain' -d '{"n":"pageview","d":"example.com","u":"https://example.com/pricing"}'`: `202`, no `x-mieru-dropped`.
4. `docker compose exec redis redis-cli XRANGE events - +`: one entry with `vid`, `cc` `ZZ`, and no `127.0.0.1` anywhere.
5. The same `curl` with `"d":"nope.test"`: `202` with `x-mieru-dropped: unknown_site`, and still one entry. Clean up with `docker compose exec redis redis-cli DEL events`.

## Where to debug
Written before the developer's test.

## Notes
- The PR is at the top of the size guide (about 450 lines of code plus tests). Splitting it would leave a half-built endpoint on `main`.
- The existing readiness test stays as it is: startup fails without Postgres instead of reporting a cache state.

## Review
