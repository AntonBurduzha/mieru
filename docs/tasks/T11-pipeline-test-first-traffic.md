---
id: T11
title: Pipeline test and first real traffic
mode: agent
status: done
features: [T1, T2, T3, T4]
depends: [T04, T08, T09, T10]
branch: t11-pipeline-test-first-traffic
---

# T11 Pipeline test and first real traffic

## Goal
A page view from the real tracker on one of your own sites reaches production ClickHouse. To get there, ingest serves the built tracker at `/js/p.js`, gzipped, from the image. `writer` and `worker` run in the production stack, and every deploy restarts them. The pipeline test runs on every PR and proves the whole path: payload, ingest, stream, writer, ClickHouse.

## Context to read
- `docs/conventions.md` sections 1, 3 (URLs), 5 (the Tracker row), 6.2, 6.3, 6.5 (the pipeline test), 6.7, and 7.5 (headers: `/js/p.js`; containers)
- `docs/architecture/deployment.md`: "Containers", "Deploy pipeline", the MVP stack line; `docs/architecture/write-path.md`: "Delivery guarantees", the writer's stop
- `docs/architecture/tracker.md`, and `GET /js/p.js` in `docs/architecture/api.md`
- `docs/architecture/decisions.md` rows 19 (Caddy), 26 (explicit `environment:`), 36 (failed deploy), 39 (no Postgres, no start)
- The T08 spec, Notes: the writer's stop grace period must be longer than one insert (`send_receive_timeout` is 30 s)
- Patterns, under `apps/backend/`:
  - `src/mieru/ingest/app.py:29` (`lifespan`), `src/mieru/ingest/routes.py`, `src/mieru/core/errors.py:38` (`NotFound`);
  - `tests/integration/conftest.py` (`serve`, `redis_db`, `db_engine`), `tests/integration/writer/test_consumer.py:34` (`cycle`).
- Ops patterns: `ops/deploy.sh` (`restart`, `wait_ready`), and the SHA-pinned actions in `.github/workflows/ci.yml`.

## Scope
- **In:**
  - ingest serves the tracker. It reads `static/p.js` once at startup and keeps a gzipped copy in memory. A missing file gives a 404 on `/js/p.js`, and ingest still collects events;
  - the image builds the tracker in its own Node stage, so the 1 KB budget fails an image build too;
  - `writer` and `worker` in `compose.prod.yaml`. `deploy.sh` restarts them first, before ingest and api;
  - the pipeline test, run by CI on every PR, and a `pytest: e2e` config in `.vscode/launch.json`;
  - the steps to install the script on a site, in `ops/README.md`.
- **Out:**
  - the seed tool: T18, whose stats tests are its first user (the developer's choice, at the spec);
  - the rest of conventions 6.5: country, browser and source come with T13, one session with T14, the stats API with T18. The privacy test is T26;
  - ETag and `304`, brotli, a CDN for the script;
  - measured memory limits and the stream's age in health: T25. Health endpoints for `writer` and `worker`, and a second writer.
- **Files allowed to change:**
  - `apps/backend/src/mieru/ingest/`: new `tracker.py`, `app.py`, `routes.py`
  - `apps/backend/Dockerfile`, `.dockerignore`, `compose.prod.yaml`, `ops/deploy.sh`
  - `.github/workflows/ci.yml` (the e2e step and the two smoke steps), `.vscode/launch.json` (`pytest: e2e`)
  - Tests: `apps/backend/tests/unit/ingest/test_tracker.py`, `apps/backend/tests/integration/ingest/test_script.py`, `apps/backend/tests/e2e/{conftest,test_pipeline}.py`
  - Docs:
    - `docs/architecture/{deployment,tracker,api,decisions}.md` (decisions: rows 44-46);
    - `docs/conventions.md` (sections 1, 5, 6.2, 6.5, 6.7) and `docs/workflow.md` (section 10);
    - `ops/README.md` (sections 6, 7 and 12), `README.md` (the status line, the rehearsal) and `CLAUDE.md` (Commands).

## Interfaces
- **`mieru.ingest.tracker`:**
  - `TRACKER_PATH = Path(__file__).parent / "static" / "p.js"` (where `build.mjs` puts its copy), and `CACHE_CONTROL = "public, max-age=86400"`;
  - `Tracker` is a frozen dataclass with `body: bytes` and `gzipped: bytes`. `gzipped` is `gzip.compress(body, compresslevel=9, mtime=0)`, so the same file always gives the same bytes;
  - `load_tracker(path: Path = TRACKER_PATH) -> Tracker | None` returns None when the file doesn't exist;
  - `accepts_gzip(accept_encoding: str) -> bool` is pure. It's true when the header lists `gzip` or `*` with q > 0, compared case-insensitively. `gzip;q=0` is false, a q that doesn't parse counts as 0, and it never raises.
- **`mieru.ingest.app`:**
  - `create_app(settings: Settings | None = None, *, tracker_path: Path = TRACKER_PATH)`;
  - the lifespan sets `app.state.tracker = await asyncio.to_thread(load_tracker, tracker_path)`. When that's None, it logs `tracker_missing` (warning, `path`) and starts anyway.
- **`GET` and `HEAD /js/p.js`** (`api_route` in `routes.py`):
  - `200` with `Content-Type: text/javascript; charset=utf-8`, `Cache-Control: public, max-age=86400` and `Vary: Accept-Encoding`, and no `Set-Cookie`;
  - when `accepts_gzip`, the body is `gzipped` with `Content-Encoding: gzip`. Otherwise it's `body`, with no `Content-Encoding`;
  - without a script, `NotFound`: `404 not_found`;
  - `Access-Control-Allow-Origin: *` stays Caddy's, for the whole ingest host.
- **Dockerfile:**
  - first stage `FROM node:24-slim AS tracker`. It runs `npm install --global pnpm@12.11.2` with WORKDIR `/repo/apps/tracker`. It copies `package.json` and `pnpm-lock.yaml`, then runs `pnpm install --frozen-lockfile --store-dir /pnpm-store` on a BuildKit cache mount. It copies `build.mjs` and `src/`, then runs `node build.mjs`;
  - the build stage adds `COPY --from=tracker /repo/apps/tracker/dist/p.js ./src/mieru/ingest/static/p.js` right after `COPY apps/backend/src ./src`. uv_build puts every file under the module root into the wheel, so the script lands in site-packages.
- **`.dockerignore`** gains `!apps/tracker/package.json`, `!apps/tracker/pnpm-lock.yaml`, `!apps/tracker/build.mjs` and `!apps/tracker/src/`.
- **CI:** the `backend` job gains `uv run pytest -m e2e` after the pytest step. The smoke steps of the `image` and `publish` jobs also run `python -c "from mieru.ingest.tracker import load_tracker; t = load_tracker(); assert t is not None and len(t.gzipped) <= 1024"`.
- **`compose.prod.yaml`:**
  - `writer`: `<<: *app`, `command: ["python", "-m", "mieru.writer"]`, `networks: [internal]`, depends on a healthy `redis` and `clickhouse`, `stop_grace_period: 45s`, `mem_limit: 256m`;
  - `worker`: `<<: *app`, `command: ["python", "-m", "mieru.worker"]`, `networks: [internal]`, depends on a healthy `postgres`, `stop_grace_period: 15s`, `mem_limit: 192m`;
  - neither has a healthcheck: there is no port to check. The `x-app` comment names all five app services.
- **`ops/deploy.sh`:** `readonly BACKGROUND_SERVICES=(writer worker)` are restarted before `APP_SERVICES`, and the header comment names the new order. `restart_background <svc>` works like this:
  - it runs `compose up -d --no-deps --force-recreate --wait --wait-timeout 60`, then the image-tag check from `restart`;
  - then, for up to `READY_SECONDS`, it waits for `compose logs <svc>` to show `startup_complete`. Otherwise it dies with `<svc> did not log startup_complete after 30s; see: docker compose -f compose.prod.yaml logs <svc>`.
- **`tests/e2e/conftest.py`:**
  - `from tests.integration.conftest import *  # noqa: F403` reuses `settings`, the autouse `test_database` and `ch_test_database`, `db_engine`, `redis_db` and `serve`. A shared `tests/conftest.py` would hand the unit tests the autouse database fixtures;
  - `live_site` makes an organization and a site with `make_organization` and `make_site` (domain `e2e-<12 hex>.example.com`) in a session on `db_engine`, and commits. So the ingest app's own startup loads the site. At teardown it deletes both.

## Acceptance criteria
Every command runs from the repo root, with `docker compose up -d`.

1. `accepts_gzip` gives the expected answer for:
   - `gzip`, `GZIP`, `deflate, gzip;q=0.5` and `*` (true);
   - `""`, `identity`, `br`, `gzip;q=0`, `gzip;q=abc` and `*;q=0` (false);
   - and it never raises (hypothesis).

   `load_tracker` gives `gzipped` that decompresses to the file's bytes, the same bytes on a second load, and None for a missing file. (auto: `uv run --directory apps/backend pytest tests/unit/ingest/test_tracker.py`)
2. With a script file, `GET /js/p.js`:
   - with `Accept-Encoding: gzip, br`: `200`, `Content-Encoding: gzip`, and the raw body (`iter_raw`) decompresses to the file;
   - with `identity`: the file's bytes, and no `Content-Encoding`;
   - both carry the `Content-Type`, `Cache-Control` and `Vary` from Interfaces, and no `Set-Cookie`. `HEAD` gives `200` with the same headers.

   (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_script.py -k served`)
3. With no script file, ingest logs `tracker_missing` at startup, `/js/p.js` gives `404` with code `not_found`, and an event for a known site still gets `202` with no `X-Mieru-Dropped`. (auto: `uv run --directory apps/backend pytest tests/integration/ingest/test_script.py -k missing`)
4. **T1-T3, end to end** (`tests/e2e/test_pipeline.py`).
   - Setup: `live_site`; the real ingest app, served with `serve`; three posts with the bodies the tracker sends, as `text/plain`, a Chrome user agent and `X-Real-IP: 203.0.113.10`:
     - a page view of `https://<domain>/` with `r` `https://news.example/`;
     - an SPA page view of `https://<domain>/pricing?tab=2` with the same `r`;
     - `signup` on `/pricing` with `p` `{"plan":"pro","seats":"3"}`.
   - Each post gets `202` with no `X-Mieru-Dropped`. One `run_once(redis_db, ClickHouse(settings), batch_max=100, flush_ms=200, stop=asyncio.Event())` returns 3.
   - The site's rows, by `(name, pathname)`: `pageview /` and `pageview /pricing` with props `{}`, and `signup /pricing` with `{"plan": "pro", "seats": "3"}`.
   - All three have the site's domain as `hostname`, `https://news.example/` as `referrer` and `ZZ` as `country`. They share one non-zero `visitor_id`, and their timestamps fall between the test's start (to the second) and its end.
   - `XLEN events` is 0 afterwards.

   (auto: `uv run --directory apps/backend pytest -m e2e tests/e2e/test_pipeline.py`)
5. CI: the workflows pass actionlint. (auto: `docker run --rm -v "$PWD:/repo:ro" -w /repo rhysd/actionlint:1.7.7 -color`; human: on this task's PR, the `backend` log shows `tests/e2e/test_pipeline.py` passed, and `image` is green)
6. The image builds the tracker and holds it, identical to a local build. (auto: `pnpm --dir apps/tracker build && docker build -q -f apps/backend/Dockerfile -t mieru-app:t11 . && docker run --rm mieru-app:t11 python -c "from mieru.ingest.tracker import load_tracker; t = load_tracker(); assert t is not None and len(t.gzipped) <= 1024" && docker run --rm mieru-app:t11 python -c "import sys; from mieru.ingest.tracker import TRACKER_PATH; sys.stdout.buffer.write(TRACKER_PATH.read_bytes())" | cmp - apps/tracker/dist/p.js`)
7. In the prod stack, `writer` and `worker` run their modules on the `internal` network only, with their stop grace periods. (auto: `docker compose -f compose.prod.yaml --env-file ops/rehearsal.env config --format json | jq -e '.services as $s | $s.writer.command == ["python","-m","mieru.writer"] and $s.worker.command == ["python","-m","mieru.worker"] and ($s.writer.networks | keys) == ["internal"] and ($s.worker.networks | keys) == ["internal"] and $s.writer.stop_grace_period == "45s" and $s.worker.stop_grace_period == "15s"'`)
8. The deploy script passes shellcheck. (auto: `docker run --rm -v "$PWD:/mnt:ro" -w /mnt koalaman/shellcheck:v0.11.0 ops/deploy.sh`)
9. The prod stack, rehearsed on the Mac, carries an event from ingest to ClickHouse and serves the script gzipped through Caddy. (human: "Test it yourself", step 4)

## Tests to write
- `tests/unit/ingest/test_tracker.py`: the `accepts_gzip` table and a hypothesis test with `@settings(derandomize=True)`, plus `load_tracker` on `tmp_path` (1)
- `tests/integration/ingest/test_script.py`: the module overrides `ingest_app` with `create_app(settings, tracker_path=tmp_path / "p.js")`, so `client` and `refresh` from the ingest conftest serve that app. One set of tests writes the file first; the missing set doesn't. In the missing case, `capture_logs` around the startup, and one event for a `make_site` site after `refresh()` (2, 3)
- `tests/e2e/conftest.py` and `tests/e2e/test_pipeline.py` (`pytestmark = pytest.mark.e2e`), as in Interfaces and criterion 4. It reads ClickHouse with a client from `ClickHouse(settings)` and `{site_id:UInt32}`, and `country` comes back as `b"ZZ"` (4)

## Proof of done
Nothing beyond the standard checks: criteria 5-8 are `auto:` commands. The verifier also runs the T1-T4 feature check, `pnpm --dir apps/tracker test && uv run --directory apps/backend pytest -m e2e tests/e2e/test_pipeline.py`.

## Test it yourself
1. `pnpm --dir apps/tracker build`, then start ingest from `apps/backend`:
   - `curl -s -D - -o /dev/null -H 'Accept-Encoding: gzip' localhost:8001/js/p.js` shows `content-encoding: gzip`, `cache-control: public, max-age=86400` and `vary: accept-encoding`;
   - `curl -s localhost:8001/js/p.js | cmp - ../tracker/dist/p.js` prints nothing.
2. `uv run pytest -m e2e` from `apps/backend`: `1 passed`.
3. Delete `src/mieru/ingest/static/p.js` and restart ingest: `tracker_missing` in its log, and `curl -s localhost:8001/js/p.js` gives `{"error":{"code":"not_found",...}}`. Then build the tracker again.
4. The prod stack on the Mac, from the repo root, with the commands of the `README.md` rehearsal (updated in this task):
   - build the image; start the databases; migrate; create the site `example.com` in a one-off `api` container, so ingest's startup loads it; then `up -d --wait`;
   - `prod logs writer worker` shows `startup_complete` for both;
   - the `/js/p.js` curl shows `content-encoding: gzip` and `access-control-allow-origin: *`;
   - the event POST answers `202` with no `x-mieru-dropped`;
   - within 5 s, the ClickHouse count is one higher than before;
   - then `prod down`.

After the merge (not part of DONE; it confirms M1):
- the deploy log restarts `writer` and `worker` before `ingest-1`;
- `docker compose -f compose.prod.yaml ps` on the server lists both on the new tag;
- `curl -sI https://in.$MIERU_DOMAIN/js/p.js` shows the headers above;
- with the snippet on one of your own sites (`ops/README.md`, section 12), a page view shows up in `mieru.events` within 35 s.

Deploy stage 2 runs the target commit's own `deploy.sh` and `compose.prod.yaml`, so the new steps can't run on the server before the merge. If they fail, the deploy stops before ingest restarts, and the old ingest keeps serving.

## Where to debug
Paths are under `apps/backend/` unless they start with `ops/`. How to use the debugger: `docs/workflow.md`, "Debugging".

| What it decides | Where | Reach it with | Look at |
|---|---|---|---|
| Which bytes a client gets | `src/mieru/ingest/routes.py:74` (`accepts_gzip` in `script`), the `None` check on line 69 | the Cursor config `ingest` after `pnpm --dir apps/tracker build`, then `curl -s -D - -o /dev/null -H 'Accept-Encoding: gzip' localhost:8001/js/p.js`; or `pytest: -k` with `served` | `request.headers["accept-encoding"]`, and `body`: `tracker.gzipped` (about 500 B) or `tracker.body` (786 B). A wrong choice shows as gibberish in a browser that can't unzip it, or as the full size where gzip was offered. `tracker` is None only when no script was built: a 404 |
| gzip or not, by its q | `src/mieru/ingest/tracker.py:29` (`accepts_gzip`: gzip's own q wins over `*`'s), `quality` on line 49 | `pytest: -k` with `accepted` or `refused` | `gzip_q` and `any_q`. For `gzip;q=0, *` they are 0.0 and 1.0, and the 0.0 decides. A q that doesn't parse returns 0.0, so a malformed header gets the plain file |
| The script at startup | `src/mieru/ingest/app.py:51` (`load_tracker` through `asyncio.to_thread`), the warning on line 54 | `ingest` with and without `src/mieru/ingest/static/p.js`; or `pytest: -k` with `missing` | `app.state.tracker_path` is the package's `static/p.js` unless a test passed its own. `None` logs `tracker_missing`, and the startup goes on. In the image the file is under `site-packages`, put into the package by `Dockerfile:29` |
| The pipeline end to end | `tests/e2e/test_pipeline.py:59` (the real ingest app, whose startup loads `live_site`), the writer cycle on line 66 | `pytest: e2e` | `responses`: 202s with no `x-mieru-dropped`. An `unknown_site` there means the site wasn't committed before the app started. `stored` is 3, and the rows share one `visitor_id` |
| A deploy's readiness check for writer and worker | `ops/deploy.sh:138` (`wait_started`, the log check), called from `restart_background` on line 144 and the loop on line 174 | only on the server, during a deploy (`ssh deploy@<ip> /srv/mieru/ops/deploy.sh <sha>`). The script needs `/srv/mieru` | the new container's `compose logs` text. With no `startup_complete` within 30 s, the deploy stops before `ingest-1` and names the logs command. A writer that exits at once fails earlier, at `up --wait` on line 148 |

## Notes
- The seed tool moved to T18 (the developer's choice, at the spec). The roadmap rows of T11 and T18 changed with it.
- The pipeline test is T11's part of conventions 6.5. T13 adds country, browser and source, T14 the session, and T18 the stats API. Section 6.5 says so.
- Restart order: consumers before producers. A new writer reads both the old and the new stream entry format, so a format change never reaches an old writer, which would drop the new entries as invalid (decision 46).
- The PR comes to about 450 lines, mostly tests and docs, which is over the 300-line guide for agent PRs.
- Build progress: ops done first, while the test-writer ran. That covers the Dockerfile's `tracker` stage, `.dockerignore`, `writer` and `worker` in `compose.prod.yaml`, `deploy.sh`, the CI steps and `launch.json`. Criteria 7 and 8 and actionlint exit 0, and the image's `p.js` is identical to the local build. The test-writer's unit and integration tests failed on the missing module and the missing `tracker_path`. The e2e test passed before any code, because it needs none: the test-writer broke the pipeline four ways, and each break failed it. Then came `mieru.ingest.tracker`, the lifespan and the route. 251 tests pass, plus 1 e2e and the 26 tracker tests, and criterion 6 exits 0. Docs updated.

## Review
**Reviewer, 2026-11-26:** 0 must-fix, 1 should-fix, 5 nits.
1. [should-fix, fixed] `README.md`, the rehearsal, and this spec's step 4. `prod down` came before the event steps, and a site made in the running stack reaches ingest's cache only at the next 30 s refresh, so the POST was dropped as `unknown_site` with the header hidden. Now the site is created in a one-off `api` container before `up -d --wait`, `prod down` comes last, and the POST uses `-i`.
2. [nit, left] `apps/backend/tests/e2e/conftest.py:15`: in a session that runs integration and e2e tests together, the star-imported session fixtures run once per directory, so `mieru_test` in ClickHouse is dropped and migrated twice. Harmless today, because e2e runs first and holds no client; worth a line in the docstring.
3. [nit, left] `apps/backend/tests/integration/ingest/test_script.py:119`: the 404 test would pass with the route gone, because Starlette's own 404 maps to `not_found` too. Checking the message (`The tracking script is not built`) would pin it to the route.
4. [nit, left] `apps/backend/src/mieru/ingest/tracker.py:40`, `routes.py:74`: `x-gzip` isn't treated as `gzip` (RFC 9110 8.4.1.3), and only the first `Accept-Encoding` field line is read. Both fall back to the plain file.
5. [nit, left] `README.md:8`: "The dashboard and accounts come next" skips M2 (T12-T15): pipeline hardening comes first.
6. [nit, left] `ops/README.md` section 10: a rollback to a commit before T11 runs that commit's compose file, which has no `writer` or `worker`. Both keep running on the T11 image as compose orphans, so the consumer stays newer, the safe direction.

**Verifier, 2026-11-26: NEED_HUMAN.** 1-4 pass (exit 0) · 5 auto: actionlint exit 0, human: the PR's `backend` and `image` checks, after the push · 6, 7, 8 pass (exit 0; `dist/p.js: 786 B, 500 B gzipped`, `cmp` identical) · 9 human: the Mac rehearsal · `pytest` (251 passed), ruff, format and mypy exit 0, tracker 26 passed · T1-T4 feature check exit 0 · Scope clean · Test integrity clean.

**Verifier, 2026-11-26 (run 2, after the developer's edits to `tracker.py`, `routes.py` and `Dockerfile`): DONE.** 1-4 pass (unit 15, `served` 4, `missing` 3, e2e 1 passed) · 5 auto: actionlint exit 0; the human half is checked on the draft PR · 6, 7, 8 pass (exit 0; `cmp` identical) · 9 confirmed by the developer · `pytest` 251 passed, ruff, format (96 files) and mypy (93 files) exit 0, tracker 26 passed · T1-T4 feature check exit 0 · Scope and test integrity clean · debug map lines checked.

**Done, 2026-11-26:** the developer ran "Test it yourself", including the Mac rehearsal, and confirmed it with `/task T11`. Final verdict DONE. Criterion 5's CI half (the `backend` log shows `tests/e2e/test_pipeline.py` passed, and `image` is green) is checked on the draft PR before it's marked ready, as for T09. M1 is confirmed by the after-merge steps.
