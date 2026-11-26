from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mieru.models import Salt


async def get_salts(session: AsyncSession, days: Sequence[date]) -> dict[date, bytes]:
  """Get the salts for the given days."""
  result = await session.execute(select(Salt.day, Salt.salt).where(Salt.day.in_(days)))
  return {day: salt for day, salt in result}


async def insert_salt(session: AsyncSession, day: date, salt: bytes) -> bool:
  """Store the salt for a day unless the day has one: the first writer wins.

  Returns whether this call stored it. A skipped row returns nothing, so RETURNING tells.
  """
  statement = (
    insert(Salt)
    .values(day=day, salt=salt)
    .on_conflict_do_nothing(index_elements=["day"])
    .returning(Salt.day)
  )
  result = await session.execute(statement)
  return result.scalar_one_or_none() is not None


async def delete_salts_before(session: AsyncSession, day: date) -> int:
  """Delete the salts of every day before the given one; return how many went."""
  result = await session.execute(delete(Salt).where(Salt.day < day).returning(Salt.day))
  return len(result.all())
