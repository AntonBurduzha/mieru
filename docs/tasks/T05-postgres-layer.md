---
id: T05
title: Postgres layer
mode: human
status: done
features: []
depends: [T01]
branch: t05-postgres-layer
---

# T05 Postgres layer

## Goal
The backend gets its Postgres layer, which T07 (ingest) and T16 (auth) build on:
- the tables `organizations`, `sites` and `salts`, created by Alembic;
- async SQLAlchemy models;
- a test database that rolls back after each test, and factories;
- a command that creates an organization and a site.

Production gets the tables on the next deploy.

## Context to read
- `docs/architecture/data-model.md`, "Postgres": the tables, and why `sites.id` is an integer
- `docs/architecture/privacy.md`, "Salt lifecycle": what a `salts` row is
- `docs/architecture/deployment.md`, "Deploy pipeline": migrations run before any restart; expand, migrate, contract
- `docs/conventions.md`:
  - section 2: layering, the clock, errors;
  - section 3: the `mieru_test` name;
  - section 5: the data rules (domains, `rate_limit_per_min` 6000);
  - sections 6.3 and 6.4: rollback per test, factories.
- The patterns to follow: `apps/backend/src/mieru/db/session.py` (`make_engine`), and `apps/backend/tests/integration/conftest.py` (the `settings` fixture switches Redis to database 15)

## Scope
- **In:**
  - the models and the first migration for the three tables;
  - `make_sessionmaker`;
  - the integration fixtures: create and migrate `mieru_test` once per run, and give each test a session whose work is rolled back;
  - the factories;
  - the repositories, the sites service and the `mieru.tools.create_site` command;
  - Alembic in the image and in `ops/deploy.sh`.
- **Out:**
  - `users`, `memberships`, `auth_sessions` and the API's session dependency: T16;
  - `get_site_by_domain` and the site cache: T07;
  - creating salts (`ensure_salts`): T07. Salt rotation: T10;
  - the Sites API and the full domain rules: T17;
  - rollback across a migration: an open question in the roadmap, to settle before T16 adds the second migration.
- **Files allowed to change** (under `apps/backend/` unless the path starts with `ops/` or `docs/`):
  - `src/mieru/models/{__init__,base,organization,site,salt}.py`
  - `src/mieru/db/session.py`
  - `src/mieru/repositories/{__init__,organizations,sites}.py`
  - `src/mieru/services/{__init__,sites}.py`
  - `src/mieru/tools/{__init__,create_site}.py`
  - `alembic.ini`, `migrations/{env.py,script.py.mako}`, `migrations/versions/0001_organizations_sites_salts.py`
  - `pyproject.toml` (the pytest `pythonpath` only), `Dockerfile`, `.dockerignore` (repo root), `ops/deploy.sh`
  - `tests/factories.py` and `tests/integration/conftest.py`
  - `tests/integration/db/{test_migrations,test_isolation}.py`, `tests/integration/services/test_sites.py`, `tests/integration/tools/test_create_site.py`, `tests/unit/services/test_sites.py`
  - Docs, written by Claude in run 2:
    - `docs/architecture/data-model.md`;
    - `docs/conventions.md` (sections 1, 5 and 6.3);
    - `docs/workflow.md` (setup) and `README.md` (run it locally);
    - `ops/README.md` (migrations in the deploy; creating a site on the server).

## Interfaces
`Base(DeclarativeBase)` carries a naming convention for constraints.

```text
organizations  id uuid PK DEFAULT gen_random_uuid() · name text NOT NULL · created_at timestamptz NOT NULL
sites          id int PK GENERATED ALWAYS AS IDENTITY (the ClickHouse site_id)
               organization_id uuid NOT NULL, FK organizations ON DELETE CASCADE, indexed
               domain text NOT NULL UNIQUE · timezone text NOT NULL (IANA name)
               status text NOT NULL DEFAULT 'active', CHECK in ('active', 'over_quota', 'blocked')
               rate_limit_per_min int NOT NULL DEFAULT 6000 · created_at timestamptz NOT NULL
salts          day date PK · salt bytea NOT NULL, CHECK octet_length(salt) = 16
```

`created_at` defaults to `mieru.core.clock.now()` on the Python side.

```python
# mieru.db.session
def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]  # expire_on_commit=False
# mieru.repositories.organizations
async def insert_organization(session: AsyncSession, name: str) -> Organization
async def get_organization(session: AsyncSession, org_id: UUID) -> Organization | None
# mieru.repositories.sites
async def insert_site(session: AsyncSession, org_id: UUID, domain: str, timezone: str) -> Site
# mieru.services.sites
def normalize_domain(domain: str) -> str  # strip, lowercase, drop one leading "www."
async def create_site(session: AsyncSession, org_id: UUID, domain: str, timezone: str) -> Site
# raises ValidationFailed (unknown timezone), NotFound (no such org), Conflict (domain taken)
```

```text
uv run python -m mieru.tools.create_site --domain <d> [--timezone <tz>] (--org-name <name> | --org-id <uuid>)
  --timezone defaults to UTC
  stdout: site_id=<int> organization_id=<uuid> domain=<normalized>
  exit 0: created
  exit 1: conflict, not_found or validation_failed; the message goes to stderr, nothing is written
  exit 2: bad arguments
```

- The migration's revision id is `0001`.
- `migrations/env.py` takes the URL from the `sqlalchemy.url` option when the caller sets it (the tests do), and otherwise from `Settings.database_url`.
- `ops/deploy.sh` `migrate()` runs `alembic upgrade head` in a one-off `api` container of the new image (`MIERU_TAG=$SHA`, `run --rm --no-deps`). It runs before `set_tag` and before any restart, so a failed migration stops the deploy.

## Acceptance criteria
1. On an empty database, the migration creates the three tables as in Interfaces, and `downgrade base` removes them. Alembic's check finds no difference between the models and the migration. (auto: `uv run --directory apps/backend pytest tests/integration/db/test_migrations.py`)
2. If `mieru_test` is missing, the test run creates and migrates it. (auto: `docker compose exec -T postgres dropdb -U mieru --if-exists mieru_test && uv run --directory apps/backend pytest tests/integration/db`)
3. Rows that one test writes are gone in the next test. (auto: `uv run --directory apps/backend pytest tests/integration/db/test_isolation.py`)
4. `create_site` behaves as follows (auto: `uv run --directory apps/backend pytest tests/integration/services/test_sites.py tests/unit/services/test_sites.py`):
   - it stores the normalized domain, with `status` `active` and `rate_limit_per_min` 6000;
   - it raises `Conflict` for a taken domain, also when the new one differs only by case or a leading `www.`;
   - it raises `ValidationFailed` for an unknown timezone, and `NotFound` for an unknown organization;
   - two sites get different integer ids.
5. The tool prints and exits as in Interfaces, and writes nothing on an error. (auto: `uv run --directory apps/backend pytest tests/integration/tools/test_create_site.py`)
6. The image ships the migrations. (auto: `docker build -q -f apps/backend/Dockerfile -t mieru-app:t05 . && docker run --rm mieru-app:t05 alembic heads | grep -q 0001`)
7. The deploy's migrate command works on the production stack. (human: in the local rehearsal from `README.md`, run `docker compose -f compose.prod.yaml --env-file ops/rehearsal.env run --rm --no-deps api alembic upgrade head`; then `\dt` in that stack's Postgres lists the three tables)

## Tests to write
- `tests/integration/db/test_migrations.py`: criterion 1
- `tests/integration/db/test_isolation.py`: criteria 2 and 3
- `tests/integration/services/test_sites.py`: criterion 4
- `tests/unit/services/test_sites.py`: criterion 4. `normalize_domain` turns `" WWW.Example.COM "` into `example.com`, and `www.www.a.com` into `www.a.com`
- `tests/integration/tools/test_create_site.py`: criterion 5

## Proof of done
Criteria 6 and 7, beyond the standard checks.

## Test it yourself
1. `cd apps/backend && uv run alembic upgrade head` prints "Running upgrade  -> 0001".
2. `docker compose exec postgres psql -U mieru -d mieru -c '\d sites'` shows the columns, the unique domain and the status check.
3. `uv run python -m mieru.tools.create_site --org-name Me --domain WWW.Example.com --timezone Europe/Kyiv` prints `site_id=1 … domain=example.com`. Run it again: exit code 1 and `conflict`.
4. `uv run pytest` is green, new tests included.

After the merge (not part of DONE):
- the deploy log shows the migrations step;
- `\dt` on the server lists the tables;
- your own sites are created on the server with the steps in `ops/README.md`.

## Where to debug
Paths are under `apps/backend/` unless they start with `ops/`. How to use the debugger: `docs/workflow.md`, "Debugging".

| What it decides | Where | Reach it with | Look at |
|---|---|---|---|
| A taken domain becomes `Conflict`, and the session survives | `src/mieru/services/sites.py:32` (`create_site`), the `except` on line 34 | the Cursor config `pytest: -k` with `taken_domain` | `exc.orig` is asyncpg's `UniqueViolationError` on `uq_sites_domain`. After line 35, `session.in_transaction()` is still `True`: only the SAVEPOINT was rolled back. A `PendingRollbackError` in the next call means the insert ran outside `begin_nested()` |
| Which inputs are refused before any write | `src/mieru/services/sites.py:26` (the timezone) and line 28 (the organization) | `pytest: -k` with `unknown_timezone` or `unknown_organization` | `len(_timezones())`: about 598 on a Mac and 486 in the image, which has no legacy aliases such as `Europe/Kiev`. A zone that passes locally and fails on the server shows up here |
| The tool's transaction: one commit, or nothing | `src/mieru/tools/create_site.py:45` (`run`), the rollback on line 47 | `pytest: -k` with `exits_1`, or `uv run python -m mieru.tools.create_site --org-name X --domain example.com` twice | On an error after `insert_organization` (line 41), the new organization must be gone after line 47. The `Late` organization count in the test is 0 |
| The test database: created once, migrated once | `tests/integration/conftest.py:69` (`test_database`), `CREATE DATABASE` on line 78, the upgrade on line 81 | criterion 2's command, or `pytest: current file` on `tests/integration/db/test_isolation.py` | `exists` is `None` on a fresh run. "asyncio.run() cannot be called from a running event loop" means the upgrade ran without `asyncio.to_thread` |
| Each test's rollback | `tests/integration/conftest.py:96` (`db_session`), the rollback on line 105 | `pytest: current file` on `test_isolation.py`, with a breakpoint on line 105 | `transaction.is_active` is `True` before line 105, even after the test's `commit()`, because of `join_transaction_mode="create_savepoint"` on line 99 |
| Which database a migration runs on, and when the deploy stops | `migrations/env.py:20` (`database_url`) and `ops/deploy.sh:101` (`migrate`) | `uv run alembic upgrade head` from `apps/backend`; on the server, the deploy log | Locally `sqlalchemy.url` is unset, so the URL is `DATABASE_URL`; the tests set it to `mieru_test`. A lock held for over 10 s fails with `LockNotAvailableError` (line 55), and the deploy logs `Postgres migrations failed; nothing was restarted` |

## Notes
- The test database is created by a fixture (`CREATE DATABASE mieru_test` when it's missing), not by a compose init script. An init script runs only on a fresh volume, and CI would need it too.
- `organizations` has only the MVP columns. The plan and Stripe columns come with A3 and A4.
- `sites.status` is `text` with a CHECK, not a Postgres enum, because a CHECK is easier to change in a later migration.
- The factories are plain async functions. Tests import them as `tests.factories`, through pytest's `pythonpath = ["."]`.
- Added after gate 1: `.dockerignore`. It lets only listed paths into the image build, so `alembic.ini` and `migrations/` must be added there for criterion 6.

## Review
**Reviewer, 2026-11-14:** 0 must-fix, 5 should-fix, 7 nits. The reviewer also ran `alembic upgrade head --sql` inside the image: `env.py` loads the models and the settings there, and the SQL matches Interfaces.
1. [should-fix, fixed] `src/mieru/models/base.py:15`: a second, unused `MetaData` at module level. A table declared on it would have escaped autogenerate and `alembic check` without an error. Removed.
2. [should-fix, fixed] `src/mieru/models/salt.py:10`: the docstring said a salt belongs to a site and signs events. It now says one row per UTC day, shared by every site, keying that day's visitor hash.
3. [should-fix, fixed] `tests/integration/db/test_migrations.py`: criterion 1's test compared only column names, and `command.check` can't catch a mistake made in both the model and the migration. Added `test_the_keys_and_defaults_are_as_in_the_spec`: the identity, the server defaults, `ON DELETE CASCADE` and the two indexes. It fails when `ondelete` is removed from the migration (`None != 'CASCADE'`).
4. [should-fix, fixed] `migrations/env.py:50`: no `lock_timeout`, so a later `ALTER` stuck behind an open transaction would queue every query on the table and hold the deploy lock. The engine now sets `lock_timeout` to 10 s.
5. [should-fix, fixed] `ops/README.md`, section 10: the rollback text held only for target commits from T05 on. Corrected.
6. [nit, left] `src/mieru/services/sites.py:15` and `src/mieru/tools/create_site.py:64`: `normalize_domain` and `main` have no docstring (conventions section 4).
7. [nit, left] `tests/integration/conftest.py:1`: the module docstring was deleted, not updated.
8. [nit, left] (design) `src/mieru/tools/create_site.py:41`: the tool calls the repository `insert_organization` directly, past the services layer, while `services/__init__.py` says tools call services. A `create_organization` service fits T16's signup.
9. [nit, left] (design) `src/mieru/services/sites.py:21`: `available_timezones()` has 598 zones on a Mac and 486 in the image (no legacy aliases such as `Europe/Kiev`). For T17: the `tzdata` package, or a fixed list of canonical names.
10. [nit, left] `ops/README.md`, section 6: on a first start the apps start before the tables exist. Harmless until T07's ingest reads `sites` at startup; then start `postgres` first, migrate, and start the rest.
11. [nit, left] `tests/integration/tools/test_create_site.py`: `run_once` and `main` are never run by a test.
12. [nit, left] `src/mieru/models/site.py:23`: `ondelete="cascade"` is lowercase, the migration's is `"CASCADE"`. Same to Postgres.

**Verifier, 2026-11-14: DONE** (before the fixes). 1 pass (exit 0, 3 passed) · 2 pass (exit 0, `mieru_test` dropped and re-created at `0001`) · 3 pass · 4 pass (11 passed) · 5 pass (8 passed; the real entrypoint exits 2 on a missing owner) · 6 pass (`0001 (head)`) · 7 human: confirmed in run 2 · Standard checks exit 0 (86 passed) · Scope clean · Test integrity clean.

**Verifier, 2026-11-14: DONE** (after the fixes). 1 pass (4 passed, the new keys-and-defaults test included) · 2 pass (7 passed; `mieru_test` re-created at `0001`) · 3 pass · 4 pass (11 passed) · 5 pass (8 passed) · 6 pass (rebuilt; `0001 (head)`, the image's `env.py` has `lock_timeout`) · 7 human: confirmed in run 2 · Standard checks exit 0 (87 passed) · Debug map matches the files · Scope clean · Test integrity clean.

**Done, 2026-11-14:** the developer confirmed criterion 7 (the migrate command in the production rehearsal) and the "Test it yourself" steps by running `/task T05`. Final verdict DONE.
