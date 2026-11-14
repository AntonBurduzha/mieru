import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import make_organization, make_site

from mieru.core.errors import Conflict, NotFound, ValidationFailed
from mieru.services.sites import create_site

pytestmark = pytest.mark.integration


async def test_stores_the_normalized_domain_with_the_defaults(db_session: AsyncSession) -> None:
  organization = await make_organization(db_session)
  site = await create_site(db_session, organization.id, " WWW.Shop.Example ", "Europe/Kyiv")
  assert site.organization_id == organization.id
  assert (site.domain, site.timezone) == ("shop.example", "Europe/Kyiv")
  assert (site.status, site.rate_limit_per_min) == ("active", 6000)


async def test_a_taken_domain_is_a_conflict_even_with_www_and_capitals(
  db_session: AsyncSession,
) -> None:
  taken = await make_site(db_session, domain="taken.example")
  with pytest.raises(Conflict):
    await create_site(db_session, taken.organization_id, "WWW.Taken.EXAMPLE", "UTC")
  # Only the failed insert was undone: the session still works
  other = await create_site(db_session, taken.organization_id, "free.example", "UTC")
  assert other.domain == "free.example"


@pytest.mark.parametrize("timezone", ["Mars/Olympus", "europe/kyiv", ""])
async def test_an_unknown_timezone_is_refused(db_session: AsyncSession, timezone: str) -> None:
  organization = await make_organization(db_session)
  with pytest.raises(ValidationFailed):
    await create_site(db_session, organization.id, "tz.example", timezone)


async def test_an_unknown_organization_is_not_found(db_session: AsyncSession) -> None:
  with pytest.raises(NotFound):
    await create_site(db_session, uuid.uuid4(), "orphan.example", "UTC")


async def test_two_sites_get_different_integer_ids(db_session: AsyncSession) -> None:
  organization = await make_organization(db_session)
  first = await create_site(db_session, organization.id, "one.example", "UTC")
  second = await create_site(db_session, organization.id, "two.example", "UTC")
  assert isinstance(first.id, int)
  assert first.id != second.id
