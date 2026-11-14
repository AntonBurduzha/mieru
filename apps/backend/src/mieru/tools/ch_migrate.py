"""
uv run python -m mieru.tools.ch_migrate [--dir clickhouse]

Applies the ClickHouse migrations, clickhouse/NNNN_<slug>.sql, in order, and records
each one in the schema_migrations table.
"""

import argparse
import asyncio
import hashlib
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from clickhouse_connect.driver.asyncclient import AsyncClient
from clickhouse_connect.driver.exceptions import DatabaseError

from mieru.core import clock
from mieru.core.clickhouse import ClickHouse
from mieru.core.config import Settings, get_settings
from mieru.core.errors import AppError

FILE_NAME = re.compile(r"(?P<version>\d{4})_[a-z0-9_]+\.sql")
TABLE = "schema_migrations"
COLUMNS = ("version", "name", "checksum", "applied_at")


class MigrationError(AppError):
  """A migration file is wrong or its statement failed; the message names the file."""

  code = "migration_failed"


@dataclass(frozen=True)
class Migration:
  """One SQL file holding one statement."""

  version: int
  name: str  # the file name without .sql, e.g. "0001_events"
  sql: str
  checksum: str  # sha256 of the file's bytes, in hex


def load_migrations(directory: Path) -> list[Migration]:
  """Read every *.sql file in directory, sorted by version.
  Refuses a file whose name isn't NNNN_<slug>.sql and two files with one version.
  """
  if not directory.is_dir():
    raise MigrationError(f"{directory} is not a directory")
  found: dict[int, Migration] = {}
  for path in directory.glob("*.sql"):
    match = FILE_NAME.fullmatch(path.name)
    if match is None:
      raise MigrationError(f"{path.name}: the name must be NNNN_<slug>.sql")
    version = int(match["version"])
    if version in found:
      raise MigrationError(
        f"{path.name}: version {version:04d} is also used by {found[version].name}.sql"
      )
    data = path.read_bytes()
    found[version] = Migration(
      version=version,
      name=path.stem,
      sql=data.decode(),
      checksum=hashlib.sha256(data).hexdigest(),
    )
  return [found[version] for version in sorted(found)]


def pending(migrations: list[Migration], applied: dict[int, str]) -> list[Migration]:
  for migration in migrations:
    recorded = applied.get(migration.version)
    if recorded is not None and recorded != migration.checksum:
      raise MigrationError(
        f"{migration.name}.sql changed after it was applied; add a new file instead"
      )
  return [migration for migration in migrations if migration.version not in applied]


async def execute(client: AsyncClient, migration: Migration) -> None:
  """Run the file's statement; a ClickHouse error becomes a MigrationError naming the file."""
  try:
    await client.command(migration.sql)
  except DatabaseError as exc:
    raise MigrationError(f"{migration.name}.sql failed: {exc}") from exc


async def migrate(client: AsyncClient, directory: Path) -> list[Migration]:
  """Apply the pending migrations in directory and record each one. Returns those applied.
  Stops at the first failure: the files before it stay applied and recorded.
  """
  # reading files blocks, so it runs in a worker thread, off the event loop
  migrations = await asyncio.to_thread(load_migrations, directory)
  if not migrations or migrations[0].version != 0:
    raise MigrationError(f"{directory}: the first file must be 0000_schema_migrations.sql")
  # 0000 creates the table that records the rest; IF NOT EXISTS makes it safe every run
  await execute(client, migrations[0])

  result = await client.query(
    "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
  )
  known = {migration.version for migration in migrations}
  applied: dict[int, str] = {}
  for version, name, checksum in result.result_rows:
    applied[version] = checksum
    if version not in known:
      # a rollback to an older image: the schema only expands, so the old code still works
      print(
        f"warning: the database has {name}, which is not in {directory.name}/",
        file=sys.stderr,
      )

  todo = pending(migrations, applied)
  for migration in todo:
    if migration.version != 0:  # 0000 already ran above
      await execute(client, migration)
    # ClickHouse DDL has no transactions: a crash right here leaves the statement applied
    # but not recorded, and the next run repeats it, which IF NOT EXISTS makes harmless
    row = [migration.version, migration.name, migration.checksum, clock.now()]
    await client.insert(TABLE, [row], column_names=COLUMNS)
  return todo


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
  """Read the command line. On bad arguments argparse prints usage and exits with 2."""
  parser = argparse.ArgumentParser(
    prog="python -m mieru.tools.ch_migrate",
    description="Apply the ClickHouse migrations in order.",
  )
  parser.add_argument(
    "--dir",
    type=Path,
    default=Path("clickhouse"),
    help="the folder with the NNNN_<slug>.sql files, relative to the working directory",
  )
  return parser.parse_args(argv)


async def run_once(settings: Settings, directory: Path) -> int:
  """Migrate the database in settings.clickhouse_url and report. Returns the exit code."""
  clickhouse = ClickHouse(settings)
  try:
    applied = await migrate(await clickhouse.client(), directory)
    latest = (await asyncio.to_thread(load_migrations, directory))[-1]
  except MigrationError as exc:
    print(f"{exc.code}: {exc.message}", file=sys.stderr)
    return 1
  except DatabaseError as exc:
    # ClickHouse is unreachable, or schema_migrations can't be read
    print(f"migration_failed: {exc}", file=sys.stderr)
    return 1
  finally:
    await clickhouse.close()
  for migration in applied:
    print(f"applied {migration.name}")
  if not applied:
    print(f"up to date ({latest.name})")
  return 0


def main(argv: Sequence[str] | None = None) -> int:
  """The command's entry point: parse the arguments and migrate the configured database."""
  args = parse_args(argv)
  return asyncio.run(run_once(get_settings(), args.dir))


if __name__ == "__main__":
  sys.exit(main())
