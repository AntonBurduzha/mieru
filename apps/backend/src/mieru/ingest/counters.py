import asyncio
from collections import Counter

import structlog

from mieru.ingest.pipeline import DropReason

log = structlog.get_logger(__name__)

REPORT_INTERVAL_S = 60.0


class Counters:
  """Events kept and dropped by this process since the last report."""

  def __init__(self) -> None:
    self.accepted = 0
    self.dropped: Counter[DropReason] = Counter()

  def take(self) -> tuple[int, dict[DropReason, int]]:
    """The counts so far, and start again from zero."""
    accepted, dropped = self.accepted, dict(self.dropped)
    self.accepted, self.dropped = 0, Counter()
    return accepted, dropped


def report(counters: Counters) -> None:
  """Log what was kept and dropped since the last report; nothing when there was no traffic."""
  accepted, dropped = counters.take()
  if accepted or dropped:
    log.info("events_counted", accepted=accepted, dropped=dropped)


async def report_forever(counters: Counters, *, interval_s: float = REPORT_INTERVAL_S) -> None:
  """Report every interval_s until cancelled."""
  while True:
    await asyncio.sleep(interval_s)
    report(counters)
