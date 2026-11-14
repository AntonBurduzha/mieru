from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mieru.models import Site


async def insert_site(session: AsyncSession, org_id: UUID, domain: str, timezone: str) -> Site:
    """Add a site to an organization and flush. A taken domain raises IntegrityError."""
    site = Site(organization_id=org_id, domain=domain, timezone=timezone)
    session.add(site)
    await session.flush()
    return site
