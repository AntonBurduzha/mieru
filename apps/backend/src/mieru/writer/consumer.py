import asyncio
import time
from collections.abc import Sequence
from contextlib import suppress
from typing import cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from mieru.core.clickhouse import ClickHouse
from mieru.ingest.stream import STREAM
from mieru.writer.batch import RawEntry, backoff_s, build_batch
from mieru.writer.inserter import insert_rows

log = structlog.get_logger(__name__)

GROUP = "writers"
CONSUMER = "writer-1"
# Under the Redis client's 2 s socket timeout, and the longest a stop request waits
MAX_BLOCK_MS = 1000


async def ensure_group(redis: Redis) -> None:
  """Create the consumer group, and the stream with it, unless the group exists."""
  try:
    await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
  except ResponseError as exc:
    if not str(exc).startswith("BUSYGROUP"):
      raise
    return
  log.info("group_created", stream=STREAM, group=GROUP)


async def read_batch(
  redis: Redis, *, batch_max: int, flush_ms: int, stop: asyncio.Event
) -> list[RawEntry]:
  """The next batch: this consumer's pending entries if it has any, else new ones."""
  # Pending entries come back as they are, never topped up: a retry keeps its token
  pending = await read(redis, "0", count=batch_max)
  if pending:
    return pending
  batch = await read(redis, ">", count=batch_max, block_ms=min(flush_ms, MAX_BLOCK_MS))
  if not batch:
    return []
  deadline = time.monotonic() + flush_ms / 1000
  while len(batch) < batch_max and not stop.is_set():
    left_ms = int((deadline - time.monotonic()) * 1000)
    if left_ms <= 0:
      break
    # BLOCK 0 would wait forever in redis, so left_ms is never 0 here
    batch += await read(
      redis, ">", count=batch_max - len(batch), block_ms=min(left_ms, MAX_BLOCK_MS)
    )
  return batch


async def read(
  redis: Redis, start: str, *, count: int, block_ms: int | None = None
) -> list[RawEntry]:
  """One XREADGROUP: "0" reads this consumer's pending entries, ">" new ones."""
  reply = await redis.xreadgroup(GROUP, CONSUMER, {STREAM: start}, count=count, block=block_ms)
  if not reply:
    return []
  _name, entries = cast(list[tuple[str, list[RawEntry]]], reply)[0]
  return entries


async def acknowledge(redis: Redis, ids: Sequence[str]) -> None:
  """Mark the entries as stored and delete them, both in one MULTI."""
  async with redis.pipeline(transaction=True) as pipe:
    pipe.xack(STREAM, GROUP, *ids)
    pipe.xdel(STREAM, *ids)
    await pipe.execute()


async def run_once(
  redis: Redis, ch: ClickHouse, *, batch_max: int, flush_ms: int, stop: asyncio.Event
) -> int:
  """One cycle: read a batch, insert it, acknowledge it. Returns the rows inserted."""
  try:
    entries = await read_batch(redis, batch_max=batch_max, flush_ms=flush_ms, stop=stop)
  except ResponseError as exc:
    if not str(exc).startswith("NOGROUP"):
      raise
    await ensure_group(redis)
    entries = await read_batch(redis, batch_max=batch_max, flush_ms=flush_ms, stop=stop)
  if not entries:
    return 0
  started = time.perf_counter()
  batch = build_batch(entries)
  if batch.rows:
    await insert_rows(await ch.client(), batch.rows, token=batch.token)
  # Only after the insert: an entry acknowledged first is lost if the insert fails
  await acknowledge(redis, batch.ids)
  for entry_id, error_type in batch.invalid:
    log.warning("entry_invalid", entry_id=entry_id, error_type=error_type)
  log.info(
    "batch_inserted",
    rows=len(batch.rows),
    invalid=len(batch.invalid),
    ms=round((time.perf_counter() - started) * 1000, 1),
  )
  return len(batch.rows)


async def run_forever(
  redis: Redis, ch: ClickHouse, *, batch_max: int, flush_ms: int, stop: asyncio.Event
) -> None:
  """Run cycles until stop is set. A failed cycle is retried after a growing pause."""
  failures = 0
  while not stop.is_set():
    try:
      await run_once(redis, ch, batch_max=batch_max, flush_ms=flush_ms, stop=stop)
    except Exception as exc:
      failures += 1
      delay = backoff_s(failures)
      log.warning("batch_failed", error_type=type(exc).__name__, retry_in_s=delay)
      # A stop ends the pause early; the batch stays pending for the next start
      with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=delay)
    else:
      failures = 0
