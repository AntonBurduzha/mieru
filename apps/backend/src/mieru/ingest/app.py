import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path

import structlog
from fastapi import FastAPI

from mieru.core import clock, redis
from mieru.core.config import Settings, get_settings
from mieru.core.errors import register_exception_handlers
from mieru.core.logging import RequestContextMiddleware, configure_logging
from mieru.db.session import make_engine, make_sessionmaker
from mieru.ingest.refresh import REFRESH_TIMEOUT_S, refresh_forever, refresh_once
from mieru.ingest.routes import router
from mieru.ingest.salts import SaltCache
from mieru.ingest.site_cache import SiteCache
from mieru.ingest.tracker import TRACKER_PATH, load_tracker

log = structlog.get_logger(__name__)


async def stop(task: asyncio.Task[None]) -> None:
  """Cancel a background task and wait until it has finished."""
  task.cancel()
  with suppress(asyncio.CancelledError):
    await task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
  """Open Redis and Postgres, and load the caches and the script before the first request."""
  settings: Settings = app.state.settings
  async with AsyncExitStack() as stack:
    # the same shape as the api, so clients added later close on a failed startup too
    app.state.redis = redis.connect(settings)
    stack.push_async_callback(app.state.redis.aclose)
    engine = make_engine(settings)
    stack.push_async_callback(engine.dispose)
    sessions = make_sessionmaker(engine)

    app.state.sites = SiteCache()
    app.state.salts = SaltCache()
    # No Postgres, no start. A query that hangs fails the start too, so Docker restarts it
    async with asyncio.timeout(REFRESH_TIMEOUT_S), sessions() as session:
      await refresh_once(session, app.state.sites, app.state.salts, today=clock.today())
    # Refresh the caches every REFRESH_INTERVAL_S until cancelled
    task = asyncio.create_task(refresh_forever(sessions, app.state.sites, app.state.salts))
    stack.push_async_callback(stop, task)

    app.state.tracker = await asyncio.to_thread(load_tracker, app.state.tracker_path)
    if app.state.tracker is None:
      # Events never need the script: a missing build costs /js/p.js, not collection
      log.warning("tracker_missing", path=str(app.state.tracker_path))
    log.info(
      "startup_complete", env=settings.env, version=settings.git_sha, sites=len(app.state.sites)
    )
    yield
  log.info("shutdown_complete")


def create_app(settings: Settings | None = None, *, tracker_path: Path = TRACKER_PATH) -> FastAPI:
  """Build the ingest app. uvicorn calls it with --factory; tests pass their own Settings.

  tracker_path is the built p.js; tests pass a file of their own, or none.
  """
  if settings is None:
    settings = get_settings()
  configure_logging(service="ingest", env=settings.env)
  app = FastAPI(
    title="Mieru ingest",
    version=settings.git_sha,
    lifespan=lifespan,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
  )
  app.state.settings = settings
  app.state.tracker_path = tracker_path
  app.add_middleware(RequestContextMiddleware, access_log=False)
  register_exception_handlers(app, hide_internal_errors=settings.env != "local")
  app.include_router(router)
  return app
