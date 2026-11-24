from collections.abc import Sequence

from clickhouse_connect.driver.asyncclient import AsyncClient

from mieru.writer.enrich import EventRow

TABLE = "events"
COLUMNS = tuple(EventRow.model_fields)


async def insert_rows(client: AsyncClient, rows: Sequence[EventRow], *, token: str) -> None:
  """Insert the rows as one block. ClickHouse skips a block whose token it already stored."""
  data = [[getattr(row, column) for column in COLUMNS] for row in rows]
  await client.insert(
    TABLE, data, column_names=COLUMNS, settings={"insert_deduplication_token": token}
  )
