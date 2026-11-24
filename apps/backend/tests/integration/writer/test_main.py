"""python -m mieru.writer as Docker runs it: a process of its own, stopped by a signal."""

import asyncio
import os
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from redis.asyncio import Redis
from tests.factories import make_entry
from tests.integration.conftest import eventually

from mieru.core.config import Settings
from mieru.ingest import stream
from mieru.ingest.stream import STREAM
from mieru.writer.consumer import GROUP, ensure_group

pytestmark = [pytest.mark.integration, pytest.mark.slow]

BACKEND_DIR = Path(__file__).resolve().parents[3]


@asynccontextmanager
async def writer(settings: Settings, **env: str) -> AsyncIterator[asyncio.subprocess.Process]:
  """The writer in a subprocess, on the test databases; killed if a test leaves it running"""
  test_env = {
    "ENV": "test",
    "DATABASE_URL": str(settings.database_url.get_secret_value()),
    "REDIS_URL": str(settings.redis_url.get_secret_value()),
    "CLICKHOUSE_URL": str(settings.clickhouse_url.get_secret_value()),
  }
  process = await asyncio.create_subprocess_exec(
    sys.executable,
    "-m",
    "mieru.writer",
    cwd=BACKEND_DIR,
    env=os.environ | test_env | env,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
  )
  try:
    yield process
  finally:
    if process.returncode is None:
      process.kill()
      await process.wait()


async def stop(process: asyncio.subprocess.Process, *, timeout_s: float = 3) -> str:
  """Send SIGTERM and return the log, failing if the process outlives the timeout"""
  process.send_signal(signal.SIGTERM)
  async with asyncio.timeout(timeout_s):
    _stdout, stderr = await process.communicate()
  return stderr.decode()


async def count_rows(ch: AsyncClient, site_id: int) -> int:
  result = await ch.query(
    "SELECT count() FROM events WHERE site_id = {site_id:UInt32}", parameters={"site_id": site_id}
  )
  return int(result.result_rows[0][0])


async def test_sigterm_stores_the_batch_being_collected(
  settings: Settings, redis_db: Redis, ch: AsyncClient, site_id: int
) -> None:
  await ensure_group(redis_db)
  for _ in range(3):
    await stream.append(redis_db, make_entry(site_id))

  # A 10 s flush: the batch is still being collected when the signal comes
  async with writer(settings, WRITER_FLUSH_MS="10000") as process:

    async def batch_read() -> bool:
      summary = await redis_db.xpending(STREAM, GROUP)
      return int(summary["pending"]) == 3

    await eventually(batch_read, timeout_s=15)
    log = await stop(process)

  assert process.returncode == 0, log
  assert "shutdown_complete" in log
  assert await count_rows(ch, site_id) == 3
  assert await redis_db.xlen(STREAM) == 0


async def test_an_idle_writer_stops_on_sigterm(settings: Settings, redis_db: Redis) -> None:
  async with writer(settings) as process:

    async def started() -> bool:
      # The writer creates the group just after it starts handling signals
      return bool(await redis_db.exists(STREAM))

    await eventually(started, timeout_s=15)
    log = await stop(process)

  assert process.returncode == 0, log
  assert "shutdown_complete" in log


async def test_no_redis_no_start(settings: Settings) -> None:
  async with writer(settings, REDIS_URL="redis://127.0.0.1:1/15") as process:
    async with asyncio.timeout(30):
      _stdout, stderr = await process.communicate()

  assert process.returncode == 1
  assert "startup_complete" not in stderr.decode()
