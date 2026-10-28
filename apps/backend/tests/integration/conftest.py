"""Integration fixtures: test settings, both apps with their lifespan, HTTP clients."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from pydantic import RedisDsn

from mieru.api.app import create_app as create_api_app
from mieru.core.config import Settings, get_settings
from mieru.ingest.app import create_app as create_ingest_app


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The developer's settings, switched to ENV=test"""
    base = get_settings()
    redis_test_url = str(base.redis_url).rsplit("/", 1)[0] + "/15"
    return base.model_copy(update={"env": "test", "redis_url": RedisDsn(redis_test_url)})


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's lifespan and give a client that calls it in-process."""
    # ASGITransport sends HTTP requests only; it never runs startup/shutdown, so we do it here
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def api_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """A client for an API app"""
    async with serve(create_api_app(settings)) as client:
        yield client


@pytest.fixture
async def ingest_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """A client for an ingest app"""
    async with serve(create_ingest_app(settings)) as client:
        yield client
