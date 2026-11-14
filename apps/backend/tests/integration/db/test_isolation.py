from collections.abc import Callable

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import make_site

from mieru.core.config import Settings
from mieru.models import Site

pytestmark = pytest.mark.integration

DOMAIN = "isolation.test"


async def sites_named(session: AsyncSession, domain: str) -> int:
  query = select(func.count()).select_from(Site).where(Site.domain == domain)
  return (await session.scalar(query)) or 0


async def test_the_test_database_is_at_the_latest_migration(
  db_session: AsyncSession, settings: Settings, alembic_config: Callable[[str], Config]
) -> None:
  config = alembic_config(str(settings.database_url.get_secret_value()))
  head = ScriptDirectory.from_config(config).get_current_head()
  assert await db_session.scalar(text("SELECT version_num FROM alembic_version")) == head


# The next two run in this order: the second proves the first one's row is gone
async def test_a_test_writes_and_commits_a_site(db_session: AsyncSession) -> None:
  await make_site(db_session, domain=DOMAIN)
  await db_session.commit()
  assert await sites_named(db_session, DOMAIN) == 1


async def test_the_next_test_does_not_see_it(db_session: AsyncSession) -> None:
  assert await sites_named(db_session, DOMAIN) == 0
