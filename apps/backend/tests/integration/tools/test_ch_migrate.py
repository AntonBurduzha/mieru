import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from pydantic import AnyHttpUrl, Secret

from mieru.core.clickhouse import ClickHouse
from mieru.core.config import Settings
from mieru.tools.ch_migrate import migrate, run_once

pytestmark = pytest.mark.integration

CLICKHOUSE_DIR = Path(__file__).resolve().parents[3] / "clickhouse"
# A database of its own, so these tests never touch mieru_test
SCRATCH_DATABASE = "mieru_test_migrations"
TABLE_A = "CREATE TABLE IF NOT EXISTS a (x UInt8) ENGINE = MergeTree ORDER BY x"
TABLE_B = "CREATE TABLE IF NOT EXISTS b (x UInt8) ENGINE = MergeTree ORDER BY x"
TABLE_C = "CREATE TABLE IF NOT EXISTS c (x UInt8) ENGINE = MergeTree ORDER BY x"


@pytest.fixture
async def scratch_settings(ch: AsyncClient, settings: Settings) -> Settings:
  """Settings for an empty scratch database, made fresh for each test"""
  await ch.command(f"DROP DATABASE IF EXISTS {SCRATCH_DATABASE} SYNC")
  await ch.command(f"CREATE DATABASE {SCRATCH_DATABASE}")
  url = str(settings.clickhouse_url.get_secret_value()).rsplit("/", 1)[0]
  return settings.model_copy(
    update={"clickhouse_url": Secret(AnyHttpUrl(f"{url}/{SCRATCH_DATABASE}"))}
  )


@pytest.fixture
async def scratch(scratch_settings: Settings) -> AsyncIterator[AsyncClient]:
  """A client for the scratch database"""
  clickhouse = ClickHouse(scratch_settings)
  yield await clickhouse.client()
  await clickhouse.close()


@pytest.fixture
def sql_dir(tmp_path: Path) -> Path:
  """A clickhouse/ folder holding the real 0000 file, for test migrations"""
  directory = tmp_path / "clickhouse"
  directory.mkdir()
  shutil.copy(CLICKHOUSE_DIR / "0000_schema_migrations.sql", directory)
  return directory


async def recorded(client: AsyncClient) -> list[str]:
  result = await client.query("SELECT name FROM schema_migrations ORDER BY version")
  return [row[0] for row in result.result_rows]


async def tables(client: AsyncClient) -> set[str]:
  result = await client.query("SHOW TABLES")
  return {row[0] for row in result.result_rows}


async def test_an_empty_database_gets_every_file_in_order(scratch: AsyncClient) -> None:
  applied = await migrate(scratch, CLICKHOUSE_DIR)

  assert [migration.name for migration in applied] == ["0000_schema_migrations", "0001_events"]
  assert await recorded(scratch) == ["0000_schema_migrations", "0001_events"]
  assert await tables(scratch) == {"schema_migrations", "events"}


async def test_the_first_run_prints_each_file(
  scratch_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
  assert await run_once(scratch_settings, CLICKHOUSE_DIR) == 0
  assert capsys.readouterr().out == "applied 0000_schema_migrations\napplied 0001_events\n"


async def test_a_second_run_is_up_to_date(
  scratch_settings: Settings, scratch: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
  await run_once(scratch_settings, CLICKHOUSE_DIR)
  capsys.readouterr()

  assert await run_once(scratch_settings, CLICKHOUSE_DIR) == 0

  assert capsys.readouterr().out == "up to date (0001_events)\n"
  assert await recorded(scratch) == ["0000_schema_migrations", "0001_events"]


async def test_the_run_fails_at_a_bad_statement_and_resumes_after_the_fix(
  scratch_settings: Settings,
  scratch: AsyncClient,
  sql_dir: Path,
  capsys: pytest.CaptureFixture[str],
) -> None:
  (sql_dir / "0001_a.sql").write_text(TABLE_A)
  (sql_dir / "0002_b.sql").write_text(TABLE_B.replace("ENGINE", "ENGIN"))
  (sql_dir / "0003_c.sql").write_text(TABLE_C)

  assert await run_once(scratch_settings, sql_dir) == 1
  assert "migration_failed: 0002_b.sql failed" in capsys.readouterr().err
  assert await recorded(scratch) == ["0000_schema_migrations", "0001_a"]
  assert await tables(scratch) == {"schema_migrations", "a"}

  (sql_dir / "0002_b.sql").write_text(TABLE_B)
  assert await run_once(scratch_settings, sql_dir) == 0
  assert capsys.readouterr().out == "applied 0002_b\napplied 0003_c\n"
  assert await tables(scratch) == {"schema_migrations", "a", "b", "c"}


async def test_a_changed_applied_file_fails_and_applies_nothing(
  scratch_settings: Settings,
  scratch: AsyncClient,
  sql_dir: Path,
  capsys: pytest.CaptureFixture[str],
) -> None:
  (sql_dir / "0001_a.sql").write_text(TABLE_A)
  await run_once(scratch_settings, sql_dir)
  (sql_dir / "0001_a.sql").write_text(TABLE_A + " SETTINGS index_granularity = 1024")
  (sql_dir / "0002_b.sql").write_text(TABLE_B)
  capsys.readouterr()

  assert await run_once(scratch_settings, sql_dir) == 1

  assert "0001_a.sql changed after it was applied" in capsys.readouterr().err
  assert await recorded(scratch) == ["0000_schema_migrations", "0001_a"]
  assert "b" not in await tables(scratch)


async def test_a_bad_file_name_fails(
  scratch_settings: Settings, sql_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  (sql_dir / "1_a.sql").write_text(TABLE_A)

  assert await run_once(scratch_settings, sql_dir) == 1
  assert "1_a.sql: the name must be NNNN_<slug>.sql" in capsys.readouterr().err


async def test_a_recorded_version_without_a_file_is_a_warning(
  scratch_settings: Settings, sql_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  (sql_dir / "0001_a.sql").write_text(TABLE_A)
  (sql_dir / "0002_b.sql").write_text(TABLE_B)
  await run_once(scratch_settings, sql_dir)
  (sql_dir / "0002_b.sql").unlink()  # an older image, from before 0002
  capsys.readouterr()

  assert await run_once(scratch_settings, sql_dir) == 0

  captured = capsys.readouterr()
  assert captured.err == "warning: the database has 0002_b, which is not in clickhouse/\n"
  assert captured.out == "up to date (0001_a)\n"
