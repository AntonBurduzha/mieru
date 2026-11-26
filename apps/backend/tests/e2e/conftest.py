"""Pipeline fixtures: the integration fixtures, and a site the apps' own engines can see.

The star import brings in settings, the autouse test_database and ch_test_database,
db_engine, redis_db and serve. A shared tests/conftest.py would give the unit tests the
autouse database fixtures too.
"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.factories import make_organization, make_site
from tests.integration.conftest import *  # noqa: F403

from mieru.db.session import make_sessionmaker
from mieru.models import Organization, Site


@pytest.fixture
async def live_site(db_engine: AsyncEngine) -> AsyncIterator[Site]:
  """A committed site in a new organization; deleted, then its organization, at teardown.

  db_session's outer transaction is rolled back, so the app's own engine never sees it.
  This site is committed, so the ingest app's startup loads it like any real site.
  """
  sessions = make_sessionmaker(db_engine)
  async with sessions() as session:
    organization = await make_organization(session)
    site = await make_site(
      session, organization=organization, domain=f"e2e-{uuid4().hex[:12]}.example.com"
    )
    await session.commit()
  try:
    yield site
  finally:
    async with sessions() as session:
      await session.execute(delete(Site).where(Site.id == site.id))
      await session.execute(delete(Organization).where(Organization.id == organization.id))
      await session.commit()
