---
id: T01
title: Config and startup hardening
mode: agent
status: test
features: []
depends: []
branch: t01-config-startup-hardening
---

# T01 Config and startup hardening

## Goal
Settings and startup are safe by default before anything is deployed. A missing `ENV` can't leak error details or insecure cookies. Secrets never print. The api starts and reports a ClickHouse outage instead of crash-looping. CI catches what reviews currently have to.

## Context to read
- `docs/conventions.md` sections 2 (time, errors), 3 (settings), 6.2-6.3 (test layers, real databases), 7.5 (secrets) and 7.7, row "Config and startup"
- `docs/architecture.md` section 11 (deploys wait for `/health/ready`)
- `apps/backend/src/mieru/core/config.py`, `api/app.py:17-54`, `ingest/app.py`, `core/clickhouse.py`, `core/logging.py:35` (the local renderer)
- Test patterns: `tests/integration/api/test_health.py:23` (a dependency down), `tests/unit/core/test_logging.py` (capturing logs)

## Scope
- **In:** the settings defaults and validation, secret DSNs, the `.env` lookup, app factories, startup without ClickHouse, local tracebacks, lint and type rules, pre-commit, CI, and the docs that show the start commands.
- **Out:** production compose and nginx (T02), a coverage floor (added once services exist), removing settings that are unused so far.
- **Files allowed to change:**
  - `apps/backend/src/mieru/core/{config,clickhouse,redis,logging}.py`
  - `apps/backend/src/mieru/db/session.py`
  - `apps/backend/src/mieru/api/app.py`, `apps/backend/src/mieru/api/routes/health.py`
  - `apps/backend/src/mieru/ingest/app.py`
  - `apps/backend/tests/**`: new tests. Existing tests only to construct settings with the new types and to fix typing; no assertion changes.
  - `apps/backend/pyproject.toml`, `apps/backend/Dockerfile`, `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `.env.example`
  - `CLAUDE.md`, `README.md`, `docs/workflow.md`, `docs/conventions.md`: the start commands and the secrets wording only

## Interfaces
- `Settings.env: Literal["local", "test", "prod"]`: required, no default.
- `Settings.session_cookie_secure: bool = True`; `.env.example` sets `false` for local HTTP.
- `Settings.session_ttl_days: int = Field(default=30, ge=1)`.
- `Settings.database_url: Secret[PostgresDsn]`, `redis_url: Secret[RedisDsn]`, `clickhouse_url: Secret[AnyHttpUrl]` (`pydantic.Secret`). Callers use `.get_secret_value()`.
- A validator rejects `writer_test_crash_after_insert=True` when `env == "prod"`.
- `mieru.core.config.find_env_file(start: Path) -> Path | None`: the `.env` next to the nearest ancestor of `start` that holds `.env.example`; `None` when there is none (as in the image). `Settings` reads only that file.
- `mieru.api.app.create_app` and `mieru.ingest.app.create_app` keep their signatures. The module-level `app = create_app()` lines go away; uvicorn runs `--factory mieru.api.app:create_app`.
- Internal error details are shown only when `env == "local"`. The OpenAPI JSON and docs are served only when `env != "prod"`.
- `mieru.core.clickhouse.ClickHouse`: `async def client() -> AsyncClient` connects on first use and tries again on the next use after a failure; `async def close()`. `app.state.clickhouse` holds one, and `/health/ready` pings through it.
- Both lifespans open clients with `contextlib.AsyncExitStack`, so a failure closes whatever was already opened.

## Acceptance criteria
1. Without `ENV`, `Settings` raises a `ValidationError` naming `env`. (auto: `uv run --directory apps/backend pytest tests/unit/core/test_config.py -k env_required`)
2. Defaults are safe: cookies `Secure`; error details hidden unless `ENV=local`; no docs or OpenAPI in `ENV=prod`. (auto: `uv run --directory apps/backend pytest tests/unit -k "cookie_secure or internal_errors or docs"`)
3. `session_ttl_days` accepts 1 and rejects 0. (auto: `uv run --directory apps/backend pytest tests/unit/core/test_config.py -k ttl`)
4. The crash-test flag is refused in prod. (auto: `uv run --directory apps/backend pytest tests/unit/core/test_config.py -k crash_flag`)
5. `repr`, `str` and `model_dump()` of `Settings` never contain a DSN password; `.get_secret_value()` returns the full URL. (auto: `uv run --directory apps/backend pytest tests/unit/core/test_config.py -k secret`)
6. `.env` is found from any working directory inside the repo, and never outside it. (auto: `uv run --directory apps/backend pytest tests/unit/core/test_config.py -k env_file`)
7. Local console tracebacks show no local variables. (auto: `uv run --directory apps/backend pytest tests/unit/core/test_logging.py -k locals`)
8. Importing `mieru.api.app` or `mieru.ingest.app` reads no settings and configures no logging. (auto: `uv run --directory apps/backend pytest tests/unit/api/test_app.py tests/unit/ingest/test_app.py -k import`)
9. With ClickHouse unreachable, the api starts. `/health/ready` returns 503 with `"clickhouse": "error"` and the others `ok`, and turns `ok` once ClickHouse answers, without a restart. (auto: `uv run --directory apps/backend pytest tests/integration/api/test_health.py tests/unit/core/test_clickhouse.py -k clickhouse`)
10. A failure while opening clients closes the ones already opened. (auto: `uv run --directory apps/backend pytest tests/unit/api/test_app.py -k closes`)
11. `datetime.now()`, `utcnow()` and `date.today()` are lint errors outside `mieru.core.clock`, and naive datetimes are flagged (`DTZ`). (auto: `test "$(printf 'from datetime import datetime\nx = datetime.now()\n' | uv run --directory apps/backend ruff check --stdin-filename src/mieru/probe.py - | grep -cE 'TID251|DTZ005')" -eq 2`)
12. mypy strict passes on `src` and `tests`. The duplicate `test_health` module name is solved in the config, not by renaming. (auto: `uv run --directory apps/backend mypy src tests`)
13. pre-commit runs `ruff check --fix` before `ruff format`, and mypy covers tests. (auto: `pre-commit run --all-files`)
14. No doc or Dockerfile still starts the old module-level apps. (auto: `! grep -rnE --exclude-dir=learn "mieru\.(api|ingest)\.app:app" CLAUDE.md README.md docs apps/backend/Dockerfile`)
15. CI sets `ENV=test`, runs mypy on tests, and prints a coverage report. It builds the image on every PR and pushes only from `main`, after an import smoke test of the built image. (human: on this task's PR, the `backend` log ends with a coverage table, and the `image` job ran without a push step)

## Tests to write
- `tests/unit/core/test_config.py`: `test_env_required`, `test_cookie_secure_by_default`, `test_ttl_accepts_one_day`, `test_ttl_rejects_zero`, `test_crash_flag_refused_in_prod`, `test_secret_dsns_not_printed`, `test_env_file_found_from_any_cwd`, `test_no_env_file_outside_repo` (1, 2, 3, 4, 5, 6)
- `tests/unit/api/test_app.py`: `test_internal_errors_hidden_unless_local`, `test_no_docs_in_prod`, `test_import_builds_nothing`, `test_failed_startup_closes_opened_clients` (2, 8, 10)
- `tests/unit/ingest/test_app.py`: `test_import_builds_nothing` (8)
- `tests/unit/core/test_logging.py`: `test_local_tracebacks_hide_locals` (7)
- `tests/unit/core/test_clickhouse.py`: `test_clickhouse_client_retries_after_failure`, with a fake connect (9)
- `tests/integration/api/test_health.py`: `test_starts_and_not_ready_when_clickhouse_is_down` (9)

## Proof of done
From the repo root, with `docker compose up -d`:

```bash
uv run --directory apps/backend ruff check . && uv run --directory apps/backend ruff format --check .
uv run --directory apps/backend mypy src tests
uv run --directory apps/backend pytest --cov=mieru --cov-report=term-missing
pre-commit run --all-files
docker build -f apps/backend/Dockerfile -t mieru-app:t01 . && docker run --rm mieru-app:t01 python -c "import mieru.api.app, mieru.ingest.app"
```

Expected: every command exits 0. pytest shows the new tests passing, no new skips, and a `TOTAL` coverage line. The image import prints nothing.

## Test it yourself
1. `docker compose stop clickhouse`, then from `apps/backend`: `uv run uvicorn --factory mieru.api.app:create_app --port 8000`. It starts.
2. `curl -s localhost:8000/health/ready` gives `{"postgres":"ok","redis":"ok","clickhouse":"error"}` (status 503).
3. `docker compose start clickhouse`, wait ~10 s, and curl again: all `ok`, with no restart.
4. `uv run python -c "from mieru.core.config import get_settings; print(get_settings())"` shows `database_url=Secret('**********')`.
5. `localhost:8000/api/v1/docs` opens locally.

## Notes
- This reverses one decision from the FastAPI skeleton: the api no longer refuses to start without ClickHouse (approved in the roadmap).
- Size: about 350-400 changed lines, a little over the ~300 guideline for agent tasks. To shrink it, move criteria 13 and 15 (pre-commit and CI) into their own task.

## Review

### Verifier (2026-11-12, after the review fixes): NEED_HUMAN

Every `auto:` criterion and every proof-of-done command passed; criterion 15 waits for a CI run on the PR.

| # | Evidence |
|---|---|
| 1 | `-k env_required`: 1 passed |
| 2 | `-k "cookie_secure or internal_errors or docs"`: 7 passed |
| 3 | `-k ttl`: 2 passed |
| 4 | `-k crash_flag`: 1 passed |
| 5 | `-k secret`: 4 passed (includes `test_validation_errors_hide_secrets`) |
| 6 | `-k env_file`: 4 passed |
| 7 | `-k locals`: 1 passed |
| 8 | `-k import`: 2 passed |
| 9 | ClickHouse-down integration test and the retry unit test: 2 passed |
| 10 | `-k closes`: 1 passed |
| 11 | the ruff probe reports TID251 and DTZ005 (count 2) |
| 12 | `mypy src tests`: no issues in 29 source files |
| 13 | `pre-commit run --all-files`: ruff check, ruff format, mypy passed, in that order |
| 14 | the grep finds no `mieru.(api\|ingest).app:app` |
| 15 | human: not yet confirmed; `ci.yml` matches on reading |

Proof of done: ruff and format clean; mypy clean; pytest 59 passed, no skips, `TOTAL 341 10 97%`; pre-commit exit 0; the image builds and imports both apps with no output. Scope and test integrity: clean.

### Reviewer findings

1. **Must-fix:** a failed `Settings` validation echoed its input, DSN passwords included, into the error that uvicorn prints. Fixed in `581776a` (`hide_input_in_errors=True`, plus `test_validation_errors_hide_secrets`). `exc.errors()` still holds the input by pydantic's design; future error reporting must call `errors(include_input=False)`.
2. **Should-fix:** the `publish` job pushed a second build that was never smoke-tested. Fixed in `9261978`: `publish` loads its build, imports both apps from it, then pushes that image.
3. *Optional:* `ClickHouse.close()` doesn't take the lock, so a connect still in flight at shutdown could leave a client open.
4. *Optional:* no test covers the lock (two concurrent first calls connect once).
5. *Optional:* `CLAUDE.md`, `docs/workflow.md` and the `/task` skill files still say `mypy src`, while CI runs `mypy src tests`. This is outside this spec's allowed edits.
6. *Optional:* `actions/checkout@v7` is tag-pinned and keeps the job token in `.git/config`; pin it by SHA and set `persist-credentials: false`.
