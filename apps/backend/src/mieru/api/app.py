from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
    """Open shared clients before the first request, close them after the last."""
    settings: Settings = app.state.settings
    app.state.clickhouse = await clickhouse.connect(settings)
    app.state.redis = redis.connect(settings)
    app.state.engine = make_engine(settings)
    log.info("startup_complete", env=settings.env, version=settings.git_sha)
    try:
        yield
    finally:
        await app.state.engine.dispose()
        await app.state.redis.aclose()
        await app.state.clickhouse.close()
        log.info("shutdown_complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API app. Tests pass their own Settings."""
    if settings is None:
        settings = get_settings()
    configure_logging(service="api", env=settings.env)
    app = FastAPI(
        title="Mieru API",
        version=settings.git_sha,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app, hide_internal_errors=settings.env == "prod")
    app.include_router(health.router)
    return app


app = create_app()
