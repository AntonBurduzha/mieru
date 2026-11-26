import asyncio
from contextlib import suppress
from datetime import datetime, timedelta

import structlog

from mieru.core import clock
from mieru.db.session import SessionFactory
from mieru.services.salts import SaltRotation, rotate_salts

log = structlog.get_logger(__name__)

ONE_HOUR = timedelta(hours=1)
# A query that hangs gives up long before the next run is due
JOB_TIMEOUT_S = 10.0


def next_hour(now: datetime) -> datetime:
  """The first full hour strictly after now: 23:00:00 gives 00:00, never 23:00 again."""
  return now.replace(minute=0, second=0, microsecond=0) + ONE_HOUR


async def rotate_once(sessions: SessionFactory) -> SaltRotation:
  """Rotate the salts for the current UTC day, in a transaction of its own."""
  today = clock.today()
  async with asyncio.timeout(JOB_TIMEOUT_S), sessions() as session:
    rotation = await rotate_salts(session, today)
    await session.commit()
  log.info(
    "salts_rotated",
    today=today.isoformat(),
    created=rotation.created,
    deleted=rotation.deleted,
  )
  return rotation


async def sleep_until(moment: datetime, stop: asyncio.Event) -> None:
  """Wait until the clock reaches moment, or until stop is set."""
  # asyncio sleeps on the monotonic clock, which can wake a little before the wall clock
  # gets there: check the wall clock after each sleep and sleep what is left
  while not stop.is_set() and (left := (moment - clock.now()).total_seconds()) > 0:
    with suppress(TimeoutError):
      await asyncio.wait_for(stop.wait(), timeout=left)


async def rotate_forever(sessions: SessionFactory, *, stop: asyncio.Event) -> None:
  """Rotate the salts at every full UTC hour until stop is set.

  A failed run is logged and waits for the next hour: the salts it would have deleted stay
  an hour longer, and ingest still creates today's and tomorrow's on its own.
  """
  while True:
    # From the clock after each run, so a slow or failed run never shifts the schedule
    await sleep_until(next_hour(clock.now()), stop)
    if stop.is_set():
      return
    try:
      await rotate_once(sessions)
    except Exception as exc:
      log.warning("job_failed", task="rotate_salts", error_type=type(exc).__name__)
