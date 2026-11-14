---
id: T06
title: ClickHouse schema
mode: human
status: build
features: []
depends: [T01]
branch: t06-clickhouse-schema
---

# T06 ClickHouse schema

## Goal
ClickHouse gets its schema, which T08 (writer) and T18 (stats) build on:
- the `events` table, created by `python -m mieru.tools.ch_migrate` from plain SQL files;
- a test database `mieru_test`, created fresh on every test run;
- a `site_id` per test, so tests never see each other's rows.

Production gets the table on the next deploy.

## Context to read
- `docs/architecture/data-model.md`, "ClickHouse (events)": the sketch, the sort key, why session attributes sit on every row
- `docs/architecture/write-path.md`, "Delivery guarantees": why `events` keeps a dedup window
- `docs/architecture/decisions.md` rows 1, 7 and 16
- `docs/architecture/deployment.md`, "Deploy pipeline": migrations before any restart; expand, migrate, contract
- `docs/conventions.md`:
  - section 1: `apps/backend/clickhouse/`;
  - section 3: the data names;
  - section 5: the ClickHouse migration names and `ch_migrate`;
  - section 6.3: ClickHouse isolation.
- The patterns to follow: `apps/backend/src/mieru/core/clickhouse.py` (`ClickHouse`), `apps/backend/src/mieru/tools/create_site.py` (argparse, exit codes), `apps/backend/tests/integration/conftest.py` (`settings`, `test_database`), `apps/backend/tests/integration/db/test_isolation.py`

## Scope
- **In:**
  - the SQL files `0000_schema_migrations.sql` and `0001_events.sql`;
  - the runner `mieru.tools.ch_migrate`;
  - the integration fixtures: `mieru_test` in ClickHouse, the `ch` client, the `site_id` counter;
  - an event factory;
  - the SQL files in the image, and the runner in `ops/deploy.sh`.
- **Out:**
  - `sessions` and `sessions_mv` (0002, 0003), and the insert dedup token: T14;
  - the writer's inserter: T08;
  - the read-only user `mieru_reader`: T18;
  - the seed tool: T11;
  - rollback across a migration: still the roadmap's open question.
- **Files allowed to change** (under `apps/backend/` unless the path starts with `ops/` or `docs/`):
  - `clickhouse/0000_schema_migrations.sql`, `clickhouse/0001_events.sql`
  - `src/mieru/tools/ch_migrate.py`
  - `Dockerfile`, `.dockerignore` (repo root), `ops/deploy.sh`
  - `tests/integration/conftest.py`, `tests/factories.py`
  - `tests/unit/tools/test_ch_migrate.py`, `tests/integration/tools/test_ch_migrate.py`, `tests/integration/clickhouse/{test_events_schema,test_isolation}.py`
  - Docs, written by Claude in run 2: `docs/architecture/data-model.md`, `docs/architecture/decisions.md` (row 37), `docs/conventions.md` (sections 1, 5 and 6.3), `docs/workflow.md` (setup), `README.md` (run it locally), `ops/README.md` (migrations in the deploy)

## Interfaces
```sql
-- 0000_schema_migrations.sql
CREATE TABLE IF NOT EXISTS schema_migrations
(version UInt32, name String, checksum String, applied_at DateTime('UTC'))
ENGINE = MergeTree ORDER BY version

-- 0001_events.sql: the data-model sketch, with final types
CREATE TABLE IF NOT EXISTS events
(
    site_id UInt32, timestamp DateTime('UTC') CODEC(Delta, ZSTD),
    name LowCardinality(String), visitor_id UInt64, session_id UInt64, session_start DateTime('UTC'),
    hostname LowCardinality(String), pathname String CODEC(ZSTD(3)),
    source LowCardinality(String), referrer String CODEC(ZSTD(3)),
    utm_source LowCardinality(String), utm_medium LowCardinality(String), utm_campaign LowCardinality(String),
    country LowCardinality(FixedString(2)), device LowCardinality(String),
    browser LowCardinality(String), os LowCardinality(String),
    props Map(LowCardinality(String), String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (site_id, toDate(timestamp), name, visitor_id, timestamp)
SETTINGS non_replicated_deduplication_window = 1000
```

```python
# mieru.tools.ch_migrate
class MigrationError(AppError)  # code = "migration_failed"
@dataclass(frozen=True)
class Migration:  # version: int; name: str ("0001_events"); sql: str; checksum: str (sha256 hex of the file)
def load_migrations(directory: Path) -> list[Migration]
# files NNNN_<slug>.sql, sorted by version; MigrationError on any other *.sql name or a repeated version
def pending(migrations: list[Migration], applied: dict[int, str]) -> list[Migration]
# applied maps version to checksum; MigrationError when an applied file's checksum differs
async def migrate(client: AsyncClient, directory: Path) -> list[Migration]  # the ones it applied
def main(argv: Sequence[str] | None = None) -> int
```

- `migrate` reads the files through `asyncio.to_thread` and runs 0000 first (it's idempotent). Then it reads `schema_migrations`, and for each pending file runs its one statement and inserts its row, with `applied_at` from `mieru.core.clock.now()`. It stops at the first failure.
- Every statement uses `IF NOT EXISTS`. ClickHouse DDL has no transactions, so a crash between a statement and its row must be safe to run again.
- A version recorded in the database with no file (a rollback to an older image) is a warning on stderr, not an error.

```text
uv run python -m mieru.tools.ch_migrate [--dir clickhouse]   # --dir is relative to the working directory, like alembic.ini
  stdout: "applied 0001_events", one line per file; or "up to date (0001_events)"
  stderr: "warning: the database has 0002_sessions, which is not in clickhouse/"
  exit 0: applied, or nothing to apply
  exit 1: migration_failed (a statement failed, an applied file changed, a bad file name); stderr names the file
  exit 2: bad arguments
```

Test fixtures (`tests/integration/conftest.py`):
- `settings` also switches `clickhouse_url` to the database `mieru_test`.
- `ch_test_database` (session, autouse) returns the `datetime` it started at. On the developer's database it runs `DROP DATABASE IF EXISTS mieru_test SYNC` and `CREATE DATABASE mieru_test` (a client can't connect to a database that doesn't exist), then `migrate` on `mieru_test`.
- `ch` (session): an `AsyncClient` for `mieru_test`, from `mieru.core.clickhouse.ClickHouse`.
- `site_id` (per test): the next value of `itertools.count(100_000)`.
- `tests/factories.py`: `make_event(site_id, **overrides) -> dict[str, object]` (a valid page view) and `async def insert_events(client, events)`.

`ops/deploy.sh` `migrate()`: after Alembic, `run --rm --no-deps -T api python -m mieru.tools.ch_migrate || die "ClickHouse migrations failed; nothing was restarted"`. The image copies `apps/backend/clickhouse` to `/app/clickhouse`.

## Acceptance criteria
1. On an empty database, the runner applies 0000 and 0001 in order and records both. `events` has the columns, types, partition key, sort key and dedup window in Interfaces. (auto: `uv run --directory apps/backend pytest tests/integration/tools/test_ch_migrate.py tests/integration/clickhouse/test_events_schema.py`)
2. A second run applies nothing, prints `up to date (0001_events)` and exits 0. (auto: `uv run --directory apps/backend pytest tests/integration/tools/test_ch_migrate.py -k up_to_date`)
3. A failing statement exits 1 and names the file on stderr. The files before it stay recorded and the failed one isn't; a rerun after the fix goes on from it. (auto: `uv run --directory apps/backend pytest tests/integration/tools/test_ch_migrate.py -k fails`)
4. An applied file that changed: exit 1, and nothing applied. A bad file name or a repeated version: exit 1. A recorded version with no file: a warning, and exit 0. (auto: `uv run --directory apps/backend pytest tests/unit/tools/test_ch_migrate.py tests/integration/tools/test_ch_migrate.py`)
5. Each test run drops, recreates and migrates `mieru_test`: every row in its `schema_migrations` was written after `ch_test_database` started, one row per file. (auto: `uv run --directory apps/backend pytest tests/integration/clickhouse/test_isolation.py -k fresh`)
6. Each test gets a `site_id` from 100,000 up, never repeated in a run, and rows one test inserts are not counted by the next. (auto: `uv run --directory apps/backend pytest tests/integration/clickhouse/test_isolation.py`)
7. The image ships the SQL files. (auto: `docker build -q -f apps/backend/Dockerfile -t mieru-app:t06 . && docker run --rm mieru-app:t06 ls clickhouse | grep -q 0001_events.sql`)
8. The deploy's ClickHouse step works on the production stack. (human: in the local rehearsal from `README.md`, run `docker compose -f compose.prod.yaml --env-file ops/rehearsal.env run --rm --no-deps api python -m mieru.tools.ch_migrate` twice: `applied ...` lines, then `up to date (0001_events)`. `SHOW TABLES FROM mieru` in that stack's ClickHouse lists `events` and `schema_migrations`)

## Tests to write
- `tests/unit/tools/test_ch_migrate.py`: criterion 4. The order, bad names, a repeated version, a changed checksum
- `tests/integration/tools/test_ch_migrate.py`: criteria 1-4, on a scratch database `mieru_test_migrations` with SQL files in `tmp_path`
- `tests/integration/clickhouse/test_events_schema.py`: criterion 1, from `system.columns` and `system.tables`
- `tests/integration/clickhouse/test_isolation.py`: criteria 5 and 6

## Proof of done
Criteria 7 and 8, beyond the standard checks.

## Test it yourself
1. `cd apps/backend && uv run python -m mieru.tools.ch_migrate` prints `applied 0000_schema_migrations` and `applied 0001_events`. Run it again: `up to date (0001_events)`.
2. `docker compose exec clickhouse clickhouse-client -u mieru --password mieru -q "SHOW CREATE TABLE mieru.events"` shows the columns, the partition and the sort key.
3. `... -q "SELECT version, name, applied_at FROM mieru.schema_migrations ORDER BY version"` lists both files.
4. `uv run pytest` is green, new tests included.

After the merge (not part of DONE): the deploy log shows the ClickHouse step, and `SHOW TABLES FROM mieru` on the server lists `events`.

## Where to debug
Written in run 2, from the developer's code.

## Notes
- **A runner of our own, with plain SQL files**, not a library (clickhouse-migrations, golang-migrate) or Alembic with a ClickHouse dialect: about 80 lines, no new dependency, and the SQL is exactly what ClickHouse runs. Goes into `decisions.md` as row 37.
- **The test database is dropped and recreated on every run**, not "migrated once and truncated" as `conventions.md` 6.3 says now. With `non_replicated_deduplication_window`, ClickHouse may drop a block identical to one it stored before, and TRUNCATE is not sure to clear that memory. Tests with a frozen clock and a counter that restarts at 100,000 insert identical blocks run after run. A fresh database also runs the migrations from zero every time. Section 6.3 is updated in run 2.
- **A recorded version with no file is a warning**, unlike Alembic's `Can't locate revision`. A rollback to an image from before a ClickHouse migration then still deploys, because the schema only expands. An applied file that changed is always an error.
- **`--dir` is relative to the working directory** (`apps/backend` locally, `/app` in the image), like `alembic.ini`. The alternative, SQL files inside the package read with `importlib.resources`, would move them out of `apps/backend/clickhouse/`, where the conventions put them.
- **`DateTime('UTC')`**, not plain `DateTime`, so the server's timezone never changes how a timestamp is read. The enrichment columns exist from the start; until T13 the writer fills them with `''` and `ZZ`.
- Changed after gate 1: criterion 5 first dropped `mieru_test` by hand in its command. `.claude/hooks/protect.sh` blocks any command with `DROP DATABASE`, so the verifier couldn't run it. Now a test checks that the rows in `schema_migrations` were written during this run.

## Review
