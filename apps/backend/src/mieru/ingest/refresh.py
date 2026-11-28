import asyncio
from datetime import date

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from mieru.core import clock
from mieru.db.session import SessionFactory
from mieru.ingest.salts import SaltCache
from mieru.ingest.site_cache import CachedSite, SiteCache
from mieru.repositories.sites import list_all_sites
from mieru.services.salts import ensure_salts

log = structlog.get_logger(__name__)

REFRESH_INTERVAL_S = 30.0
# A query that hangs gives up long before the next refresh is due
REFRESH_TIMEOUT_S = 10.0


async def refresh_once(
  session: AsyncSession, sites: SiteCache, salts: SaltCache, *, today: date
) -> None:
  """Reload both caches from Postgres, creating today's and tomorrow's salts if missing."""
  day_salts = await ensure_salts(session, today)
  rows = await list_all_sites(session)
  await session.commit()
  sites.replace(
    CachedSite(
      id=row.id,
      org_id=row.organization_id,
      domain=row.domain,
      status=row.status,
      rate_limit_per_min=row.rate_limit_per_min,
    )
    for row in rows
  )
  salts.replace(day_salts)


async def refresh_forever(
  sessions: SessionFactory,
  sites: SiteCache,
  salts: SaltCache,
  *,
  interval_s: float = REFRESH_INTERVAL_S,
) -> None:
  """Refresh the caches every interval_s until cancelled. A failure keeps the old caches."""
  while True:
    await asyncio.sleep(interval_s)
    try:
      async with asyncio.timeout(REFRESH_TIMEOUT_S), sessions() as session:
        await refresh_once(session, sites, salts, today=clock.today())
    except Exception as exc:
      # Ingest keeps serving from memory while Postgres is down
      log.warning("cache_refresh_failed", error_type=type(exc).__name__)
