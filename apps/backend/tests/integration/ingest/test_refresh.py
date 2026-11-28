import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import date

import pytest
from pydantic import PostgresDsn, Secret
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs
from tests.factories import make_site
from tests.integration.conftest import serve

from mieru.core.config import Settings
from mieru.ingest.app import create_app
from mieru.ingest.refresh import refresh_forever, refresh_once
from mieru.ingest.salts import SaltCache
from mieru.ingest.site_cache import SiteCache

pytestmark = pytest.mark.integration

DAY = date(2031, 3, 10)


async def test_refresh_loads_sites_and_salts(db_session: AsyncSession) -> None:
  site = await make_site(db_session, domain="cached.example")
  sites, salts = SiteCache(), SaltCache()
  await refresh_once(db_session, sites, salts, today=DAY)

  cached = sites.get("cached.example")
  assert cached is not None
  assert (cached.id, cached.org_id, cached.status) == (site.id, site.organization_id, "active")
  assert salts.get(DAY) is not None
  assert salts.get(date(2031, 3, 11)) is not None


async def test_refresh_again_keeps_the_salts_and_finds_new_sites(db_session: AsyncSession) -> None:
  sites, salts = SiteCache(), SaltCache()
  await refresh_once(db_session, sites, salts, today=DAY)
  salt = salts.get(DAY)
  await make_site(db_session, domain="later.example")
  assert sites.get("later.example") is None

  await refresh_once(db_session, sites, salts, today=DAY)
  assert sites.get("later.example") is not None
  assert salts.get(DAY) == salt


async def test_startup_fails_without_postgres(settings: Settings) -> None:
  # Port 1 has nothing listening: the connection is refused at once
  url = "postgresql+asyncpg://mieru:mieru@127.0.0.1:1/mieru"
  down = settings.model_copy(update={"database_url": Secret(PostgresDsn(url))})
  with pytest.raises(ConnectionRefusedError):
    async with serve(create_app(down)):
      pass


async def test_refresh_forever_survives_a_failed_refresh(db_session: AsyncSession) -> None:
  await make_site(db_session, domain="retried.example")
  sites, salts = SiteCache(), SaltCache()
  refreshed = asyncio.Event()
  calls = 0

  @asynccontextmanager
  async def flaky_sessions() -> AsyncIterator[AsyncSession]:
    """Postgres is down for the first refresh and back for the second"""
    nonlocal calls
    calls += 1
    if calls == 1:
      raise ConnectionRefusedError("postgres is down")
    if calls > 2:
      # Park the loop here, away from the session, until the test cancels it
      await asyncio.Event().wait()
    yield db_session
    refreshed.set()

  with capture_logs() as logs:
    task = asyncio.create_task(refresh_forever(flaky_sessions, sites, salts, interval_s=0))
    try:
      async with asyncio.timeout(5):
        await refreshed.wait()
    finally:
      task.cancel()
      with suppress(asyncio.CancelledError):
        await task

  assert sites.get("retried.example") is not None

  failures = [log for log in logs if log["event"] == "cache_refresh_failed"]
  assert failures[0]["error_type"] == "ConnectionRefusedError"


async def test_refresh_puts_each_rate_limit_per_min_in_the_cache(db_session: AsyncSession) -> None:
  limited = await make_site(db_session, domain="limited.example")
  limited.rate_limit_per_min = 3
  await make_site(db_session, domain="default-limit.example")
  await db_session.flush()
  sites, salts = SiteCache(), SaltCache()
  await refresh_once(db_session, sites, salts, today=DAY)

  cached = sites.get("limited.example")
  assert cached is not None
  assert cached.rate_limit_per_min == 3
  default = sites.get("default-limit.example")
  assert default is not None
  assert default.rate_limit_per_min == 6000
