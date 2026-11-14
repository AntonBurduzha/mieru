import hashlib
from pathlib import Path

import pytest

from mieru.tools.ch_migrate import Migration, MigrationError, load_migrations, main, pending

CLICKHOUSE_DIR = Path(__file__).resolve().parents[3] / "clickhouse"


def write(directory: Path, name: str, sql: str = "SELECT 1") -> None:
  (directory / name).write_text(sql)


def test_the_files_load_in_version_order(tmp_path: Path) -> None:
  write(tmp_path, "0010_later.sql")
  write(tmp_path, "0000_first.sql", "CREATE TABLE IF NOT EXISTS a (x UInt8) ENGINE = Log")
  write(tmp_path, "0001_second.sql")
  write(tmp_path, "notes.md", "not a migration")  # only *.sql files count

  migrations = load_migrations(tmp_path)

  assert [(m.version, m.name) for m in migrations] == [
    (0, "0000_first"),
    (1, "0001_second"),
    (10, "0010_later"),
  ]
  first = migrations[0]
  assert first.sql == "CREATE TABLE IF NOT EXISTS a (x UInt8) ENGINE = Log"
  assert first.checksum == hashlib.sha256(first.sql.encode()).hexdigest()


def test_the_repo_has_the_schema_migrations_and_events_files() -> None:
  names = [migration.name for migration in load_migrations(CLICKHOUSE_DIR)]
  assert names[:2] == ["0000_schema_migrations", "0001_events"]


@pytest.mark.parametrize("name", ["1_events.sql", "0001-events.sql", "0001_Events.sql", "x.sql"])
def test_a_file_with_a_bad_name_is_refused(tmp_path: Path, name: str) -> None:
  write(tmp_path, name)
  with pytest.raises(MigrationError, match=name):
    load_migrations(tmp_path)


def test_a_repeated_version_is_refused(tmp_path: Path) -> None:
  write(tmp_path, "0001_a.sql")
  write(tmp_path, "0001_b.sql")
  with pytest.raises(MigrationError, match="version 0001 is also used"):
    load_migrations(tmp_path)


def test_a_missing_directory_is_refused(tmp_path: Path) -> None:
  with pytest.raises(MigrationError, match="is not a directory"):
    load_migrations(tmp_path / "missing")


def migration(version: int, sql: str = "SELECT 1") -> Migration:
  return Migration(
    version=version,
    name=f"{version:04d}_m",
    sql=sql,
    checksum=hashlib.sha256(sql.encode()).hexdigest(),
  )


def test_pending_keeps_the_files_not_applied_in_order() -> None:
  migrations = [migration(0), migration(1), migration(2)]
  applied = {0: migrations[0].checksum}
  assert pending(migrations, applied) == [migrations[1], migrations[2]]


def test_pending_is_empty_when_everything_is_applied() -> None:
  migrations = [migration(0), migration(1)]
  assert pending(migrations, {m.version: m.checksum for m in migrations}) == []


def test_an_applied_file_that_changed_is_refused() -> None:
  migrations = [migration(0), migration(1, "SELECT 2"), migration(2)]
  applied = {0: migrations[0].checksum, 1: migration(1, "SELECT 1").checksum}
  with pytest.raises(MigrationError, match=r"0001_m\.sql changed after it was applied"):
    pending(migrations, applied)


def test_bad_arguments_exit_with_2() -> None:
  with pytest.raises(SystemExit) as exc_info:
    main(["--no-such-option"])
  assert exc_info.value.code == 2
