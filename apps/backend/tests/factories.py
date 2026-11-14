from itertools import count

from sqlalchemy.ext.asyncio import AsyncSession

from mieru.models import Organization, Site
from mieru.repositories.organizations import insert_organization
from mieru.repositories.sites import insert_site

# Unique names and domains within a test run
_sequence = count(1)


async def make_organization(session: AsyncSession, *, name: str | None = None) -> Organization:
    """An organization; the name is unique unless given."""
    return await insert_organization(session, name or f"Organization {next(_sequence)}")


async def make_site(
    session: AsyncSession,
    *,
    organization: Organization | None = None,
    domain: str | None = None,
    timezone: str = "UTC",
) -> Site:
    """A site, in a new organization unless one is given; the domain is unique unless given."""
    if organization is None:
        organization = await make_organization(session)
    domain = domain or f"site-{next(_sequence)}.test"
    return await insert_site(session, organization.id, domain, timezone)
