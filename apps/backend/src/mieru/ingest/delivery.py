import asyncio
from collections import deque
from contextlib import suppress
from datetime import datetime
from itertools import islice

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from mieru.ingest import stream
from mieru.ingest.pipeline import Accepted, DropReason
from mieru.ingest.stream import StreamEntry

log = structlog.get_logger(__name__)

DRAIN_BATCH = 500
RUN_INTERVAL_S = 1.0
FLUSH_TIMEOUT_S = 5.0


class FallbackQueue:
  """Events held in memory while Redis is unreachable, oldest first, at most maxsize."""

  def __init__(self, maxsize: int) -> None:
    self.maxsize = maxsize
    self._entries: deque[StreamEntry] = deque()

  def __len__(self) -> int:
    return len(self._entries)

  def put(self, entry: StreamEntry) -> bool:
    """Hold the entry after the others; False when the queue is full."""
    if len(self._entries) >= self.maxsize:
      return False
    self._entries.append(entry)
    return True

  def oldest(self, n: int) -> list[StreamEntry]:
    """The first n entries, still held."""
    return list(islice(self._entries, n))

  def remove_oldest(self, n: int) -> None:
    """Let go of the first n entries, once Redis has them."""
    for _ in range(min(n, len(self._entries))):
      self._entries.popleft()


class Delivery:
  """Gets accepted events into the stream: rate limit, backpressure, and the fallback queue."""

  def __init__(self, redis: Redis, *, high_water: int, fallback_size: int) -> None:
    self.redis = redis
    self.high_water = high_water
    self.queue = FallbackQueue(fallback_size)
    self.stream_length = 0  # as of the last check_stream
    self._full_logged = False
    # One drain at a time, so flush at shutdown waits for the one run has started
    self._drain_lock = asyncio.Lock()
    self._drain_task: asyncio.Task[int] | None = None  # kept, as the loop holds tasks weakly

  async def send(self, accepted: Accepted, *, now: datetime) -> DropReason | None:
    """Write one event to the stream, or hold it; None means kept, or the reason it was dropped."""
    if self.stream_length > self.high_water:
      return "backpressure"
    entry = accepted.entry
    if self.queue:
      # Redis failed a moment ago: queue behind the others, and don't wait on Redis again
      return self._hold(entry)
    try:
      count = await stream.count_event(self.redis, entry.site_id, now)
      if count > accepted.rate_limit_per_min:
        return "rate_limited"
      await stream.append(self.redis, entry)
    except RedisError as exc:
      log.warning("redis_unavailable", error_type=type(exc).__name__)
      return self._hold(entry)
    return None

  def _hold(self, entry: StreamEntry) -> DropReason | None:
    """Put the entry in the fallback queue, or drop it when the queue is full."""
    if self.queue.put(entry):
      return None
    if not self._full_logged:
      log.warning("fallback_full", size=self.queue.maxsize)
      self._full_logged = True
    return "redis_unavailable"

  async def check_stream(self) -> None:
    """Measure the stream. When Redis can't answer, keep the last length."""
    with suppress(RedisError):
      self.stream_length = await stream.length(self.redis)

  async def drain(self) -> int:
    """Write held entries to the stream, oldest first, until the queue is empty or Redis fails."""
    async with self._drain_lock:
      written = 0
      while self.queue:
        batch = self.queue.oldest(DRAIN_BATCH)
        try:
          await stream.append_many(self.redis, batch)
        except RedisError:
          return written  # still down; the next run tries again
        # Removed only now, so a failed batch is still held
        self.queue.remove_oldest(len(batch))
        written += len(batch)
      if written:
        self._full_logged = False
        log.info("fallback_drained", events=written)
      return written

  async def run(self, *, interval_s: float = RUN_INTERVAL_S) -> None:
    """Measure the stream and drain the queue every interval_s, until cancelled."""
    while True:
      try:
        await self.check_stream()
        # A cancel after EXEC would leave a batch in Redis and in the queue, and flush would
        # write it again. Shielded, the drain runs to the end, and flush waits for it
        self._drain_task = asyncio.create_task(self.drain())
        await asyncio.shield(self._drain_task)
      except Exception as exc:
        # A stopped loop would freeze stream_length and strand the queue until a restart
        log.warning("delivery_failed", error_type=type(exc).__name__)
      await asyncio.sleep(interval_s)

  async def flush(self, *, timeout_s: float = FLUSH_TIMEOUT_S) -> int:
    """A last drain at shutdown. What is still held then is lost, and logged."""
    written = 0
    with suppress(TimeoutError):
      async with asyncio.timeout(timeout_s):
        written = await self.drain()
    # Out of time: stop the drain run started too, so nothing writes once Redis closes
    if self._drain_task is not None and not self._drain_task.done():
      self._drain_task.cancel()
      with suppress(asyncio.CancelledError):
        await self._drain_task
    if self.queue:
      log.warning("fallback_lost", events=len(self.queue))
    return written
