"""rotate_forever across a full UTC hour.

time-machine moves the wall clock that mieru.core.clock reads, but asyncio sleeps on the
monotonic clock, which it leaves alone. So a loop started at 23:59:58 waits about two
real seconds for its 00:00 run.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime, timedelta

import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs
from structlog.typing import EventDict
from tests.integration.conftest import eventually

from mieru.core import clock
from mieru.db.session import SessionFactory
from mieru.repositories.salts import get_salts, insert_salt
from mieru.worker.rotation import rotate_forever

pytestmark = [pytest.mark.integration, pytest.mark.slow]

ONE_DAY = timedelta(days=1)
# Earlier than every real salt, so a rotation can't reach the salts the ingest app committed
DAY = date(2001, 3, 10)
BEFORE_MIDNIGHT = datetime(2001, 3, 10, 23, 59, 58, tzinfo=UTC)
BEFORE_ONE_AM = datetime(2001, 3, 11, 0, 59, 58, tzinfo=UTC)


async def seed(session: AsyncSession, days: Sequence[date]) -> dict[date, bytes]:
  """Store a salt of its own for each day, so a changed salt shows; return them"""
  salts = {day: bytes([n]) * 16 for n, day in enumerate(days, start=1)}
  for day, salt in salts.items():
    await insert_salt(session, day, salt)
  return salts


def logged(logs: list[EventDict], event: str) -> list[EventDict]:
  return [line for line in logs if line["event"] == event]


def leaks(logs: list[EventDict], salt: bytes) -> bool:
  """Whether a captured log entry holds the salt: raw, as hex, or as its repr"""
  forms = (salt.hex(), repr(salt)[2:-1])
  for line in logs:
    for value in line.values():
      # A str is searched as it is: its repr would double the backslashes of an escaped salt
      text = value if isinstance(value, str) else repr(value)
      if (isinstance(value, bytes) and salt in value) or any(form in text for form in forms):
        return True
  return False


@asynccontextmanager
async def running(
  sessions: SessionFactory, stop: asyncio.Event
) -> AsyncIterator[asyncio.Task[None]]:
  """rotate_forever in a background task, cancelled if the test leaves it running"""
  task = asyncio.create_task(rotate_forever(sessions, stop=stop))
  try:
    yield task
  finally:
    task.cancel()
    with suppress(asyncio.CancelledError):
      await task


async def test_rotate_forever_rotates_at_the_full_hour(db_session: AsyncSession) -> None:
  seeded = await seed(db_session, [DAY - 2 * ONE_DAY, DAY - ONE_DAY, DAY, DAY + ONE_DAY])
  stop = asyncio.Event()

  @asynccontextmanager
  async def sessions() -> AsyncIterator[AsyncSession]:
    yield db_session

  with capture_logs() as logs, time_machine.travel(BEFORE_MIDNIGHT, tick=True):
    async with running(sessions, stop) as task:

      async def rotated() -> bool:
        # A loop that died ends the wait too: awaiting it below raises its error
        return task.done() or bool(logged(logs, "salts_rotated"))

      await eventually(rotated, timeout_s=10)
      stop.set()
      async with asyncio.timeout(1):
        await task

  # One run, at 00:00: the new day is D+1, so D-1 and D-2 are now before yesterday
  rotations = logged(logs, "salts_rotated")
  assert [(line.get("today"), line.get("created"), line.get("deleted")) for line in rotations] == [
    ("2001-03-11", True, 2)
  ]
  # The loop is stopped, so the test can use the session again
  salts = await get_salts(db_session, [DAY + offset * ONE_DAY for offset in range(-2, 3)])
  assert sorted(salts) == [DAY, DAY + ONE_DAY, DAY + 2 * ONE_DAY]
  assert (salts[DAY], salts[DAY + ONE_DAY]) == (seeded[DAY], seeded[DAY + ONE_DAY])
  assert len(salts[DAY + 2 * ONE_DAY]) == 16
  for salt in [*seeded.values(), *salts.values()]:
    assert not leaks(logs, salt)


async def test_a_failed_run_logs_job_failed_and_the_loop_keeps_running(
  db_session: AsyncSession,
) -> None:
  stop = asyncio.Event()
  runs: list[datetime] = []  # the clock at each run's request for a session

  with capture_logs() as logs, time_machine.travel(BEFORE_MIDNIGHT, tick=True) as traveller:

    @asynccontextmanager
    async def flaky_sessions() -> AsyncIterator[AsyncSession]:
      """Postgres is down for the 00:00 run and back for the next one"""
      runs.append(clock.now())
      if len(runs) == 1:
        # Skip the rest of the hour, so the next run is 2 real seconds away, not 3,600
        traveller.move_to(BEFORE_ONE_AM)
        raise ConnectionRefusedError("postgres is down")
      yield db_session

    async with running(flaky_sessions, stop) as task:

      async def retried() -> bool:
        # A loop that died ends the wait too: awaiting it below raises its error
        return task.done() or bool(logged(logs, "salts_rotated"))

      await eventually(retried, timeout_s=15)
      stop.set()
      async with asyncio.timeout(1):
        await task

  failures = logged(logs, "job_failed")
  assert [
    (line.get("task"), line.get("error_type"), line.get("log_level")) for line in failures
  ] == [("rotate_salts", "ConnectionRefusedError", "warning")]
  events = [line["event"] for line in logs]
  assert events.index("job_failed") < events.index("salts_rotated")
  # The run after the failure waited for 01:00 instead of retrying at once (at 00:59:58)
  assert runs[1] >= datetime(2001, 3, 11, 0, 59, 59, tzinfo=UTC)
