from collections.abc import Sequence
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mieru.repositories.salts import get_salts, insert_salt
from mieru.services import salts as salt_service
from mieru.services.salts import ensure_salts

pytestmark = pytest.mark.integration

# Far from the real date, so salts that the ingest app's own startup committed don't matter
DAY = date(2031, 3, 10)
ONE_DAY = timedelta(days=1)

# Earlier than every real salt. A rotation deletes every salt before yesterday, so with a
# later day `deleted` would also count the salts the ingest app's startup committed
EARLY_DAY = date(2001, 3, 10)


def days_around(day: date, before: int, after: int) -> list[date]:
  """The days from `before` days before `day` to `after` days after it"""
  return [day + offset * ONE_DAY for offset in range(-before, after + 1)]


async def seed(session: AsyncSession, days: Sequence[date]) -> dict[date, bytes]:
  """Store a salt of its own for each day, so a changed salt shows; return them"""
  salts = {day: bytes([n]) * 16 for n, day in enumerate(days, start=1)}
  for day, salt in salts.items():
    await insert_salt(session, day, salt)
  return salts


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


async def test_insert_salt_says_it_stored_the_salt(db_session: AsyncSession) -> None:
  assert await insert_salt(db_session, EARLY_DAY, b"a" * 16) is True
  assert await get_salts(db_session, [EARLY_DAY]) == {EARLY_DAY: b"a" * 16}


async def test_insert_salt_says_the_day_had_one_and_keeps_it(db_session: AsyncSession) -> None:
  await insert_salt(db_session, EARLY_DAY, b"a" * 16)
  assert await insert_salt(db_session, EARLY_DAY, b"b" * 16) is False
  assert await get_salts(db_session, [EARLY_DAY]) == {EARLY_DAY: b"a" * 16}


async def test_rotate_keeps_yesterday_today_and_tomorrow_and_deletes_the_rest(
  db_session: AsyncSession,
) -> None:
  seeded = await seed(db_session, days_around(EARLY_DAY, before=3, after=1))
  rotation = await salt_service.rotate_salts(db_session, EARLY_DAY)

  assert rotation == salt_service.SaltRotation(created=False, deleted=2)
  kept = days_around(EARLY_DAY, before=1, after=1)
  salts = await get_salts(db_session, days_around(EARLY_DAY, before=3, after=2))
  assert salts == {day: seeded[day] for day in kept}


async def test_rotate_with_no_salts_creates_tomorrows(db_session: AsyncSession) -> None:
  rotation = await salt_service.rotate_salts(db_session, EARLY_DAY)

  assert rotation == salt_service.SaltRotation(created=True, deleted=0)
  salts = await get_salts(db_session, days_around(EARLY_DAY, before=1, after=2))
  assert list(salts) == [EARLY_DAY + ONE_DAY]
  assert len(salts[EARLY_DAY + ONE_DAY]) == 16


async def test_rotate_keeps_a_salt_days_ahead(db_session: AsyncSession) -> None:
  seeded = await seed(db_session, [EARLY_DAY + 5 * ONE_DAY])
  await salt_service.rotate_salts(db_session, EARLY_DAY)
  assert await get_salts(db_session, list(seeded)) == seeded


async def test_rotate_twice_changes_nothing_the_second_time(db_session: AsyncSession) -> None:
  await seed(db_session, days_around(EARLY_DAY, before=3, after=0))
  await salt_service.rotate_salts(db_session, EARLY_DAY)
  window = days_around(EARLY_DAY, before=3, after=2)
  after_first = await get_salts(db_session, window)

  second = await salt_service.rotate_salts(db_session, EARLY_DAY)
  assert second == salt_service.SaltRotation(created=False, deleted=0)
  assert await get_salts(db_session, window) == after_first
