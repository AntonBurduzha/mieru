"""The fallback queue: where events wait, oldest first, while Redis can't be reached."""

import asyncio
from contextlib import suppress

import pytest
from redis.asyncio import Redis
from structlog.testing import capture_logs
from tests.factories import make_entry

from mieru.ingest import stream
from mieru.ingest.delivery import Delivery, FallbackQueue


def test_fallback_queue_gives_the_oldest_entries_first() -> None:
  queue = FallbackQueue(10)
  first, second, third = (make_entry(7) for _ in range(3))
  for entry in (first, second, third):
    assert queue.put(entry) is True

  assert list(queue.oldest(2)) == [first, second]
  # oldest() only looks: the entries stay until Redis has confirmed them
  assert len(queue) == 3


def test_fallback_queue_remove_oldest_takes_from_the_front() -> None:
  queue = FallbackQueue(10)
  first, second, third = (make_entry(7) for _ in range(3))
  for entry in (first, second, third):
    queue.put(entry)

  queue.remove_oldest(2)
  assert list(queue.oldest(5)) == [third]
  assert len(queue) == 1


def test_fallback_queue_refuses_an_entry_when_full() -> None:
  queue = FallbackQueue(2)
  first, second, third = (make_entry(7) for _ in range(3))
  assert queue.put(first) is True
  assert queue.put(second) is True

  assert queue.put(third) is False
  assert list(queue.oldest(3)) == [first, second]

  # Room again once the oldest has gone
  queue.remove_oldest(1)
  assert queue.put(third) is True
  assert list(queue.oldest(3)) == [second, third]


def test_fallback_queue_of_size_0_refuses_every_entry() -> None:
  queue = FallbackQueue(0)
  assert queue.put(make_entry(7)) is False
  assert len(queue) == 0
  assert list(queue.oldest(1)) == []


async def test_delivery_run_logs_a_failure_and_keeps_going(monkeypatch: pytest.MonkeyPatch) -> None:
  calls = 0
  measured_again = asyncio.Event()

  async def length_failing_once(_redis: Redis) -> int:
    nonlocal calls
    calls += 1
    if calls == 1:
      raise RuntimeError("not a RedisError")
    measured_again.set()
    return 5

  monkeypatch.setattr(stream, "length", length_failing_once)
  delivery = Delivery(Redis(), high_water=10, fallback_size=10)
  with capture_logs() as logs:
    task = asyncio.create_task(delivery.run(interval_s=0.01))
    try:
      async with asyncio.timeout(2):
        await measured_again.wait()
    finally:
      task.cancel()
      with suppress(asyncio.CancelledError):
        await task
    await delivery.redis.aclose()

  assert delivery.stream_length == 5
  failures = [(log["event"], log["error_type"]) for log in logs]
  assert failures == [("delivery_failed", "RuntimeError")]
