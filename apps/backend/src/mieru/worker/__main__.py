import asyncio
import signal
from contextlib import AsyncExitStack

import structlog

from mieru.core.config import Settings, get_settings
from mieru.core.logging import configure_logging
from mieru.db.session import make_engine, make_sessionmaker
from mieru.worker.rotation import rotate_forever, rotate_once

log = structlog.get_logger(__name__)


async def run(settings: Settings) -> None:
  """Rotate the salts now, then at every full UTC hour until stopped."""
  stop = asyncio.Event()
  loop = asyncio.get_running_loop()
  for sig in (signal.SIGTERM, signal.SIGINT):
    # A signal only asks to stop: a rotation in progress still commits
    loop.add_signal_handler(sig, stop.set)

  async with AsyncExitStack() as stack:
    engine = make_engine(settings)
    stack.push_async_callback(engine.dispose)
    sessions = make_sessionmaker(engine)
    # No Postgres, no start: the error ends the process, and Docker restarts it
    await rotate_once(sessions)
    log.info("startup_complete", env=settings.env, version=settings.git_sha)
    await rotate_forever(sessions, stop=stop)
  log.info("shutdown_complete")


def main() -> None:
  """The worker's entry point: python -m mieru.worker."""
  settings = get_settings()
  configure_logging(service="worker", env=settings.env)
  asyncio.run(run(settings))


if __name__ == "__main__":
  main()
