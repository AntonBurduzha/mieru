import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from tests.factories import make_entry

from mieru.writer.enrich import to_row
from mieru.writer.inserter import COLUMNS, insert_rows

pytestmark = pytest.mark.integration


async def count_rows(ch: AsyncClient, site_id: int) -> int:
  result = await ch.query(
    "SELECT count() FROM events WHERE site_id = {site_id:UInt32}", parameters={"site_id": site_id}
  )
  return int(result.result_rows[0][0])


async def test_the_same_token_twice_stores_the_rows_once(ch: AsyncClient, site_id: int) -> None:
  rows = [to_row(make_entry(site_id)), to_row(make_entry(site_id))]
  # The ack failed after a good insert, so the writer sends the same batch again
  await insert_rows(ch, rows, token=f"test-{site_id}:a")
  await insert_rows(ch, rows, token=f"test-{site_id}:a")
  assert await count_rows(ch, site_id) == 2


async def test_two_tokens_store_identical_rows_twice(ch: AsyncClient, site_id: int) -> None:
  rows = [to_row(make_entry(site_id))]
  # Two different batches can hold identical rows: same visitor, page and second
  await insert_rows(ch, rows, token=f"test-{site_id}:a")
  await insert_rows(ch, rows, token=f"test-{site_id}:b")
  assert await count_rows(ch, site_id) == 2


async def test_the_columns_match_the_events_table(ch: AsyncClient) -> None:
  result = await ch.query(
    "SELECT name FROM system.columns"
    " WHERE database = currentDatabase() AND table = 'events' ORDER BY position"
  )
  assert tuple(row[0] for row in result.result_rows) == COLUMNS
