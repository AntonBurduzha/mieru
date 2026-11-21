from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mieru.repositories.salts import get_salts, insert_salt
from mieru.services.salts import ensure_salts

pytestmark = pytest.mark.integration

# Far from the real date, so salts that the ingest app's own startup committed don't matter
DAY = date(2031, 3, 10)
ONE_DAY = timedelta(days=1)


async def test_creates_todays_and_tomorrows_salts(db_session: AsyncSession) -> None:
  salts = await ensure_salts(db_session, DAY)
  assert sorted(salts) == [DAY, DAY + ONE_DAY]
  assert all(len(salt) == 16 for salt in salts.values())
  assert salts[DAY] != salts[DAY + ONE_DAY]


async def test_returns_yesterdays_salt_but_never_creates_it(db_session: AsyncSession) -> None:
  await insert_salt(db_session, DAY - ONE_DAY, b"y" * 16)
  salts = await ensure_salts(db_session, DAY)
  assert sorted(salts) == [DAY - ONE_DAY, DAY, DAY + ONE_DAY]
  assert salts[DAY - ONE_DAY] == b"y" * 16


async def test_a_second_call_keeps_the_salts(db_session: AsyncSession) -> None:
  first = await ensure_salts(db_session, DAY)
  second = await ensure_salts(db_session, DAY)
  assert second == first
  assert await get_salts(db_session, [DAY, DAY + ONE_DAY]) == first


async def test_tomorrow_keeps_the_salt_it_got_the_day_before(db_session: AsyncSession) -> None:
  monday = await ensure_salts(db_session, DAY)
  tuesday = await ensure_salts(db_session, DAY + ONE_DAY)
  assert tuesday[DAY] == monday[DAY]
  assert tuesday[DAY + ONE_DAY] == monday[DAY + ONE_DAY]
