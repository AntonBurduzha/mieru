import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from clickhouse_connect.driver.exceptions import OperationalError
from pydantic import AnyHttpUrl, Secret
from redis.asyncio import Redis
from structlog.testing import capture_logs
from tests.factories import make_entry

from mieru.core.clickhouse import ClickHouse
from mieru.core.config import Settings
from mieru.ingest import stream
from mieru.ingest.stream import STREAM, StreamEntry
from mieru.writer.consumer import GROUP, ensure_group, run_once

pytestmark = pytest.mark.integration

PROBE_UA = "MieruPrivacyProbe/1.0"
# Nothing listens on port 1, so every connection is refused at once
CLICKHOUSE_DOWN = "http://mieru:mieru@127.0.0.1:1/mieru_test"


@pytest.fixture
async def clickhouse(settings: Settings) -> AsyncIterator[ClickHouse]:
  """The writer's lazy client for mieru_test"""
  client = ClickHouse(settings)
  yield client
  await client.close()


async def cycle(
  redis_db: Redis, clickhouse: ClickHouse, *, batch_max: int = 100, flush_ms: int = 100
) -> int:
  """One writer cycle with fast settings, and no stop request"""
  return await run_once(
    redis_db, clickhouse, batch_max=batch_max, flush_ms=flush_ms, stop=asyncio.Event()
  )


async def append(redis_db: Redis, entries: list[StreamEntry]) -> None:
  for entry in entries:
    await stream.append(redis_db, entry)


async def count_rows(ch: AsyncClient, site_id: int) -> int:
  result = await ch.query(
    "SELECT count() FROM events WHERE site_id = {site_id:UInt32}", parameters={"site_id": site_id}
  )
  return int(result.result_rows[0][0])


async def pending(redis_db: Redis) -> int:
  summary = await redis_db.xpending(STREAM, GROUP)
  return int(summary["pending"])


async def test_entries_are_stored_and_removed_from_redis(
  redis_db: Redis, ch: AsyncClient, clickhouse: ClickHouse, site_id: int
) -> None:
  await ensure_group(redis_db)
  await append(
    redis_db,
    [
      make_entry(site_id, u="https://example.com/a", ts=1_795_264_205_250, vid=11, ua=PROBE_UA),
      make_entry(site_id, u="https://example.com/b"),
      make_entry(site_id, u="https://example.com/c"),
    ],
  )

  assert await cycle(redis_db, clickhouse) == 3

  result = await ch.query(
    "SELECT name, toUnixTimestamp(timestamp), visitor_id, session_id, hostname, pathname,"
    " country, props FROM events WHERE site_id = {site_id:UInt32} ORDER BY pathname",
    parameters={"site_id": site_id},
  )
  assert result.result_rows[0] == (
    "pageview",
    1_795_264_205,
    11,
    0,
    "example.com",
    "/a",
    b"ZZ",  # the driver reads a FixedString column as bytes
    {},
  )
  assert [row[5] for row in result.result_rows] == ["/a", "/b", "/c"]
  # Deleted, not only acknowledged: the raw user agent is gone from Redis
  assert await redis_db.xlen(STREAM) == 0
  assert await pending(redis_db) == 0


async def test_a_batch_by_size_stops_at_batch_max(
  redis_db: Redis, ch: AsyncClient, clickhouse: ClickHouse, site_id: int
) -> None:
  await ensure_group(redis_db)
  await append(redis_db, [make_entry(site_id) for _ in range(5)])

  started = time.monotonic()
  assert await cycle(redis_db, clickhouse, batch_max=3, flush_ms=5000) == 3
  assert time.monotonic() - started < 1
  assert await cycle(redis_db, clickhouse, batch_max=3) == 2
  assert await count_rows(ch, site_id) == 5


async def test_a_batch_by_time_waits_flush_ms_for_more(
  redis_db: Redis, clickhouse: ClickHouse, site_id: int
) -> None:
  await ensure_group(redis_db)
  await append(redis_db, [make_entry(site_id)])

  started = time.monotonic()
  assert await cycle(redis_db, clickhouse, flush_ms=300) == 1
  assert 0.3 <= time.monotonic() - started < 1.5


async def test_an_empty_stream_gives_an_empty_cycle(
  redis_db: Redis, clickhouse: ClickHouse
) -> None:
  await ensure_group(redis_db)
  started = time.monotonic()
  assert await cycle(redis_db, clickhouse, flush_ms=5000) == 0
  assert time.monotonic() - started < 1.5


async def test_a_failed_insert_leaves_every_entry_pending(
  redis_db: Redis, ch: AsyncClient, clickhouse: ClickHouse, settings: Settings, site_id: int
) -> None:
  down = ClickHouse(
    settings.model_copy(update={"clickhouse_url": Secret(AnyHttpUrl(CLICKHOUSE_DOWN))})
  )
  await ensure_group(redis_db)
  await append(redis_db, [make_entry(site_id) for _ in range(3)])

  with pytest.raises(OperationalError):
    await cycle(redis_db, down)
  assert await count_rows(ch, site_id) == 0
  assert await pending(redis_db) == 3
  assert await redis_db.xlen(STREAM) == 3

  # ClickHouse is back: the pending entries are read first, and stored once
  assert await cycle(redis_db, clickhouse) == 3
  assert await count_rows(ch, site_id) == 3
  assert await pending(redis_db) == 0


async def test_entries_from_before_group_creation_are_read(
  redis_db: Redis, ch: AsyncClient, clickhouse: ClickHouse, site_id: int
) -> None:
  await append(redis_db, [make_entry(site_id), make_entry(site_id)])
  await ensure_group(redis_db)

  assert await cycle(redis_db, clickhouse) == 2
  assert await count_rows(ch, site_id) == 2


async def test_a_deleted_stream_gets_its_group_back(
  redis_db: Redis, ch: AsyncClient, clickhouse: ClickHouse, site_id: int
) -> None:
  await ensure_group(redis_db)
  await redis_db.delete(STREAM)
  await append(redis_db, [make_entry(site_id)])

  assert await cycle(redis_db, clickhouse) == 1
  assert await count_rows(ch, site_id) == 1


async def test_an_invalid_entry_is_dropped_and_logged_without_its_fields(
  redis_db: Redis, ch: AsyncClient, clickhouse: ClickHouse, site_id: int
) -> None:
  await ensure_group(redis_db)
  await append(redis_db, [make_entry(site_id, ua=PROBE_UA)])
  bad_id = await redis_db.xadd(STREAM, {"v": "1", "site_id": str(site_id), "ua": PROBE_UA})
  await append(redis_db, [make_entry(site_id, ua=PROBE_UA)])

  with capture_logs() as logs:
    assert await cycle(redis_db, clickhouse) == 2

  assert [line for line in logs if line["event"] == "entry_invalid"] == [
    {"event": "entry_invalid", "entry_id": bad_id, "error_type": "KeyError", "log_level": "warning"}
  ]
  assert PROBE_UA not in str(logs)
  assert await count_rows(ch, site_id) == 2
  assert await redis_db.xlen(STREAM) == 0
  assert await pending(redis_db) == 0
