from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mieru.models import Site


async def insert_site(session: AsyncSession, org_id: UUID, domain: str, timezone: str) -> Site:
  """Add a site to an organization and flush. A taken domain raises IntegrityError."""
  site = Site(organization_id=org_id, domain=domain, timezone=timezone)
  session.add(site)
  await session.flush()
  return site


async def list_all_sites(session: AsyncSession) -> Sequence[Site]:
  """Every site of every organization, for the ingest cache only.

  The one query not scoped by org: its rows stay inside ingest.
  """
  return (await session.scalars(select(Site).order_by(Site.id))).all()
