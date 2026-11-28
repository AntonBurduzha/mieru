"""Drop counters: counted in memory per reason, logged as one line a minute."""

import asyncio
from collections import Counter
from contextlib import suppress

from structlog.testing import capture_logs

from mieru.ingest.counters import Counters, report, report_forever


def test_take_hands_over_what_was_counted_and_resets() -> None:
  counters = Counters()
  counters.accepted += 2
  counters.dropped["bot"] += 3
  counters.dropped["spam_referrer"] += 1

  accepted, dropped = counters.take()
  assert (accepted, dropped) == (2, Counter({"bot": 3, "spam_referrer": 1}))
  assert (counters.accepted, counters.dropped) == (0, Counter())


def test_counted_events_once_taken_are_not_changed_by_later_ones() -> None:
  counters = Counters()
  counters.dropped["bot"] += 1
  _accepted, dropped = counters.take()

  counters.dropped["bot"] += 5
  assert dropped == Counter({"bot": 1})


def test_report_logs_events_counted_with_only_the_non_zero_reasons() -> None:
  counters = Counters()
  counters.accepted = 5
  counters.dropped["bot"] += 2
  counters.dropped["rate_limited"] += 1

  with capture_logs() as logs:
    report(counters)
  [line] = logs
  assert line["event"] == "events_counted"
  assert line["accepted"] == 5
  # Nine reasons exist; the line names the two that happened
  assert line["dropped"] == {"bot": 2, "rate_limited": 1}


def test_report_logs_events_counted_when_only_drops_were_counted() -> None:
  counters = Counters()
  counters.dropped["backpressure"] += 4

  with capture_logs() as logs:
    report(counters)
  [line] = logs
  assert (line["event"], line["accepted"], line["dropped"]) == (
    "events_counted",
    0,
    {"backpressure": 4},
  )


def test_report_resets_so_a_second_call_logs_nothing_until_more_is_counted() -> None:
  counters = Counters()
  counters.accepted = 1

  with capture_logs() as logs:
    report(counters)
    report(counters)
  assert [log["event"] for log in logs] == ["events_counted"]
  assert counters.take() == (0, Counter())


def test_report_logs_nothing_when_nothing_was_counted() -> None:
  with capture_logs() as logs:
    report(Counters())
  assert logs == []


async def test_report_forever_logs_what_was_counted_each_interval() -> None:
  counters = Counters()
  counters.accepted = 1

  with capture_logs() as logs:
    task = asyncio.create_task(report_forever(counters, interval_s=0))
    try:
      async with asyncio.timeout(5):
        # The report resets the counts, so accepted falls to 0 once it has run
        while counters.accepted:  # noqa: ASYNC110
          await asyncio.sleep(0)
    finally:
      task.cancel()
      with suppress(asyncio.CancelledError):
        await task

  [line] = [log for log in logs if log["event"] == "events_counted"]
  assert line["accepted"] == 1
