"""python -m mieru.worker as Docker runs it: a process of its own, stopped by a signal."""

import asyncio
import os
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration.conftest import eventually

from mieru.core.config import Settings
from mieru.db.session import make_sessionmaker
from mieru.models import Salt
from mieru.repositories.salts import get_salts, insert_salt

pytestmark = [pytest.mark.integration, pytest.mark.slow]

BACKEND_DIR = Path(__file__).resolve().parents[3]

# Long before yesterday, whatever the real date is
OLD_DAY = date(2000, 1, 1)


@asynccontextmanager
async def worker(settings: Settings, **env: str) -> AsyncIterator[asyncio.subprocess.Process]:
  """The worker in a subprocess, on the test databases; killed if a test leaves it running"""
  test_env = {
    "ENV": "test",
    "DATABASE_URL": str(settings.database_url.get_secret_value()),
    "REDIS_URL": str(settings.redis_url.get_secret_value()),
    "CLICKHOUSE_URL": str(settings.clickhouse_url.get_secret_value()),
  }
  process = await asyncio.create_subprocess_exec(
    sys.executable,
    "-m",
    "mieru.worker",
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


@pytest.fixture
async def old_salt(db_engine: AsyncEngine) -> AsyncIterator[None]:
  """A committed salt for OLD_DAY, which the worker's own process can see"""
  sessions = make_sessionmaker(db_engine)
  async with sessions() as session:
    await insert_salt(session, OLD_DAY, b"o" * 16)
    await session.commit()
  yield
  # The worker deletes it; this only cleans up after a failed run, so that a leftover row
  # can't add to what other tests' rotations delete
  async with sessions() as session:
    await session.execute(delete(Salt).where(Salt.day == OLD_DAY))
    await session.commit()


@pytest.mark.usefixtures("old_salt")
async def test_the_worker_deletes_an_old_salt_at_startup_and_stops_on_sigterm(
  settings: Settings, db_engine: AsyncEngine
) -> None:
  sessions = make_sessionmaker(db_engine)

  async with worker(settings) as process:

    async def old_salt_gone() -> bool:
      if process.returncode is not None:
        _stdout, stderr = await process.communicate()
        pytest.fail(f"the worker exited with {process.returncode}:\n{stderr.decode()}")
      # A new session for each poll, so it sees what the worker committed since the last one
      async with sessions() as session:
        return not await get_salts(session, [OLD_DAY])

    await eventually(old_salt_gone, timeout_s=15)
    log = await stop(process)

  assert process.returncode == 0, log
  assert "startup_complete" in log
  assert "shutdown_complete" in log


async def test_no_postgres_no_start(settings: Settings) -> None:
  # Port 1 has nothing listening: the connection is refused at once
  url = "postgresql+asyncpg://mieru:mieru@127.0.0.1:1/mieru"
  async with worker(settings, DATABASE_URL=url) as process:
    async with asyncio.timeout(30):
      _stdout, stderr = await process.communicate()

  log = stderr.decode()
  assert process.returncode == 1, log
  assert "startup_complete" not in log
  # The exit comes from Postgres, not from some other error at startup
  assert "ConnectionRefusedError" in log
