---
id: T05
title: Postgres layer
mode: human
status: build
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
Written in run 2, from the code on disk.

## Notes
- The test database is created by a fixture (`CREATE DATABASE mieru_test` when it's missing), not by a compose init script. An init script runs only on a fresh volume, and CI would need it too.
- `organizations` has only the MVP columns. The plan and Stripe columns come with A3 and A4.
- `sites.status` is `text` with a CHECK, not a Postgres enum, because a CHECK is easier to change in a later migration.
- The factories are plain async functions. Tests import them as `tests.factories`, through pytest's `pythonpath = ["."]`.
- Added after gate 1: `.dockerignore`. It lets only listed paths into the image build, so `alembic.ini` and `migrations/` must be added there for criterion 6.

## Review
