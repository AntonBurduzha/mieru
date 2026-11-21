import secrets
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from mieru.repositories.salts import get_salts, insert_salt

SALT_BYTES = 16
ONE_DAY = timedelta(days=1)


async def ensure_salts(session: AsyncSession, today: date) -> dict[date, bytes]:
  """Create today's and tomorrow's salts if missing; return those of yesterday to tomorrow.

  Several processes may run it at once: the first insert of a day wins, the others read it.
  """
  for day in (today, today + ONE_DAY):
    await insert_salt(session, day, secrets.token_bytes(SALT_BYTES))
  return await get_salts(session, [today - ONE_DAY, today, today + ONE_DAY])
