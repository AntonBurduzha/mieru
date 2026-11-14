from datetime import datetime
from pathlib import Path

import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from tests.factories import insert_events, make_event

from mieru.tools.ch_migrate import load_migrations

pytestmark = pytest.mark.integration

CLICKHOUSE_DIR = Path(__file__).resolve().parents[3] / "clickhouse"

seen: list[int] = []


async def count_events(ch: AsyncClient, site_id: int) -> int:
  # {site_id:UInt32} is a server-side parameter: the value travels apart from the SQL
  result = await ch.command(
    "SELECT count() FROM events WHERE site_id = {site_id:UInt32}",
    parameters={"site_id": site_id},
  )
  assert isinstance(result, int)
  return result


async def test_the_test_database_is_fresh(ch: AsyncClient, ch_test_database: datetime) -> None:
  result = await ch.query(
    "SELECT name, toUnixTimestamp(applied_at) FROM schema_migrations ORDER BY version"
  )
  names = [migration.name for migration in load_migrations(CLICKHOUSE_DIR)]
  assert [row[0] for row in result.result_rows] == names
  # applied_at has whole seconds, so compare with the start rounded down
  started = int(ch_test_database.timestamp())
  assert all(row[1] >= started for row in result.result_rows)


async def test_test_site_ids_start_at_100000(site_id: int) -> None:
  seen.append(site_id)
  assert site_id >= 100_000


# The next two run in this order: the second proves it doesn't count the first one's rows
async def test_a_test_inserts_three_events(ch: AsyncClient, site_id: int) -> None:
  seen.append(site_id)
  await insert_events(ch, [make_event(site_id) for _ in range(3)])
  assert await count_events(ch, site_id) == 3


async def test_the_next_test_counts_only_its_own(ch: AsyncClient, site_id: int) -> None:
  seen.append(site_id)
  await insert_events(ch, [make_event(site_id, pathname=f"/{n}") for n in range(5)])
  assert await count_events(ch, site_id) == 5
  assert len(set(seen)) == len(seen)
