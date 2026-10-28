from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from mieru.core import redis
from mieru.core.config import Settings, get_settings
from mieru.core.errors import register_exception_handlers
from mieru.core.logging import RequestContextMiddleware, configure_logging
from mieru.ingest.routes import router

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.redis = redis.connect(settings)
    log.info("startup_complete", env=settings.env, version=settings.git_sha)
    try:
        yield
    finally:
        await app.state.redis.aclose()
        log.info("shutdown_complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ingest app. Tests pass their own Settings."""
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
    app.add_middleware(RequestContextMiddleware, access_log=False)
    register_exception_handlers(app, hide_internal_errors=settings.env == "prod")
    app.include_router(router)
    return app


app = create_app()
