import secrets
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from mieru.repositories.salts import delete_salts_before, get_salts, insert_salt

SALT_BYTES = 16
ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class SaltRotation:
  """What one rotation did: whether it stored tomorrow's salt, and how many salts it deleted."""

  created: bool
  deleted: int


async def ensure_salts(session: AsyncSession, today: date) -> dict[date, bytes]:
  """Create today's and tomorrow's salts if missing; return those of yesterday to tomorrow.

  Several processes may run it at once: the first insert of a day wins, the others read it.
  """
  for day in (today, today + ONE_DAY):
    await insert_salt(session, day, secrets.token_bytes(SALT_BYTES))
  return await get_salts(session, [today - ONE_DAY, today, today + ONE_DAY])


async def rotate_salts(session: AsyncSession, today: date) -> SaltRotation:
  """Create tomorrow's salt if missing, and delete every salt dated before yesterday.

  Yesterday's salt stays for sessions that cross midnight. Once a salt is gone, nobody can
  recompute the visitor ids made with it. The caller commits.
  """
  created = await insert_salt(session, today + ONE_DAY, secrets.token_bytes(SALT_BYTES))
  deleted = await delete_salts_before(session, today - ONE_DAY)
  return SaltRotation(created=created, deleted=deleted)
