import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient

pytestmark = pytest.mark.integration

COLUMNS = [
  ("site_id", "UInt32"),
  ("timestamp", "DateTime('UTC')"),
  ("name", "LowCardinality(String)"),
  ("visitor_id", "UInt64"),
  ("session_id", "UInt64"),
  ("session_start", "DateTime('UTC')"),
  ("hostname", "LowCardinality(String)"),
  ("pathname", "String"),
  ("source", "LowCardinality(String)"),
  ("referrer", "String"),
  ("utm_source", "LowCardinality(String)"),
  ("utm_medium", "LowCardinality(String)"),
  ("utm_campaign", "LowCardinality(String)"),
  ("country", "LowCardinality(FixedString(2))"),
  ("device", "LowCardinality(String)"),
  ("browser", "LowCardinality(String)"),
  ("os", "LowCardinality(String)"),
  ("props", "Map(LowCardinality(String), String)"),
]


async def test_events_has_the_columns_and_types_of_the_spec(ch: AsyncClient) -> None:
  result = await ch.query(
    "SELECT name, type FROM system.columns"
    " WHERE database = currentDatabase() AND table = 'events' ORDER BY position"
  )
  assert [tuple(row) for row in result.result_rows] == COLUMNS


async def test_the_long_text_and_time_columns_are_compressed(ch: AsyncClient) -> None:
  result = await ch.query(
    "SELECT name, compression_codec FROM system.columns"
    " WHERE database = currentDatabase() AND table = 'events' AND compression_codec != ''"
  )
  assert {row[0]: row[1] for row in result.result_rows} == {
    "timestamp": "CODEC(Delta(4), ZSTD(1))",
    "pathname": "CODEC(ZSTD(3))",
    "referrer": "CODEC(ZSTD(3))",
  }


async def test_events_is_split_by_month_and_sorted_by_site_first(ch: AsyncClient) -> None:
  result = await ch.query(
    "SELECT engine, partition_key, sorting_key, create_table_query FROM system.tables"
    " WHERE database = currentDatabase() AND name = 'events'"
  )
  engine, partition_key, sorting_key, create_query = result.result_rows[0]
  assert engine == "MergeTree"
  assert partition_key == "toYYYYMM(timestamp)"
  assert sorting_key == "site_id, toDate(timestamp), name, visitor_id, timestamp"
  # the writer's retried batches are dropped by this window (T14 adds the token)
  assert "non_replicated_deduplication_window = 1000" in create_query
