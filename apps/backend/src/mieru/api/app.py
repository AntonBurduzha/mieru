from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import structlog
from fastapi import FastAPI

from mieru.api.routes import health
from mieru.core import clickhouse, redis
from mieru.core.config import Settings, get_settings
from mieru.core.errors import register_exception_handlers
from mieru.core.logging import RequestContextMiddleware, configure_logging
from mieru.db.session import make_engine

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open shared clients before the first request, close them after the last.

    Each client registers its close right after it opens, so a failure part-way
    through startup still closes the ones already opened, in reverse order.
    """
    settings: Settings = app.state.settings
    async with AsyncExitStack() as stack:
        # Lazy: the api starts while ClickHouse is down and /health/ready reports it
        app.state.clickhouse = clickhouse.ClickHouse(settings)
        stack.push_async_callback(app.state.clickhouse.close)
        app.state.redis = redis.connect(settings)
        stack.push_async_callback(app.state.redis.aclose)
        app.state.engine = make_engine(settings)
        stack.push_async_callback(app.state.engine.dispose)
        log.info("startup_complete", env=settings.env, version=settings.git_sha)
        yield
    log.info("shutdown_complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API app. uvicorn calls it with --factory; tests pass their own Settings."""
    if settings is None:
        settings = get_settings()
    configure_logging(service="api", env=settings.env)
    show_docs = settings.env != "prod"
    app = FastAPI(
        title="Mieru API",
        version=settings.git_sha,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json" if show_docs else None,
        docs_url="/api/v1/docs" if show_docs else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app, hide_internal_errors=settings.env != "local")
    app.include_router(health.router)
    return app
