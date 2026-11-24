import asyncio
import signal
from contextlib import AsyncExitStack

import structlog

from mieru.core import redis
from mieru.core.clickhouse import ClickHouse
from mieru.core.config import Settings, get_settings
from mieru.core.logging import configure_logging
from mieru.writer.consumer import ensure_group, run_forever

log = structlog.get_logger(__name__)


async def run(settings: Settings) -> None:
  """Move events from the stream to ClickHouse until stopped."""
  stop = asyncio.Event()
  loop = asyncio.get_running_loop()
  for sig in (signal.SIGTERM, signal.SIGINT):
    # A signal only asks to stop: the batch in hand is still inserted and acknowledged
    loop.add_signal_handler(sig, stop.set)

  async with AsyncExitStack() as stack:
    client = redis.connect(settings)
    stack.push_async_callback(client.aclose)
    clickhouse = ClickHouse(settings)
    stack.push_async_callback(clickhouse.close)
    # No Redis, no start: the error ends the process, and Docker restarts it
    await ensure_group(client)
    log.info(
      "startup_complete",
      env=settings.env,
      version=settings.git_sha,
      batch_max=settings.writer_batch_max,
      flush_ms=settings.writer_flush_ms,
    )
    await run_forever(
      client,
      clickhouse,
      batch_max=settings.writer_batch_max,
      flush_ms=settings.writer_flush_ms,
      stop=stop,
    )
  log.info("shutdown_complete")


def main() -> None:
  """The writer's entry point: python -m mieru.writer."""
  settings = get_settings()
  configure_logging(service="writer", env=settings.env)
  asyncio.run(run(settings))


if __name__ == "__main__":
  main()
