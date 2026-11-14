from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mieru.models import Organization


async def insert_organization(session: AsyncSession, name: str) -> Organization:
    """Add an organization and flush, so Postgres assigns its id."""
    organization = Organization(name=name)
    session.add(organization)
    await session.flush()
    return organization


async def get_organization(session: AsyncSession, org_id: UUID) -> Organization | None:
    """The organization with this id, or None."""
    return await session.get(Organization, org_id)
