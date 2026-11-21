from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mieru.models import Salt


async def get_salts(session: AsyncSession, days: Sequence[date]) -> dict[date, bytes]:
  """Get the salts for the given days."""
  result = await session.execute(select(Salt.day, Salt.salt).where(Salt.day.in_(days)))
  return {day: salt for day, salt in result}


async def insert_salt(session: AsyncSession, day: date, salt: bytes) -> None:
  """Store the salt for a day unless the day has one: the first writer wins."""
  statement = insert(Salt).values(day=day, salt=salt).on_conflict_do_nothing(index_elements=["day"])
  await session.execute(statement)
