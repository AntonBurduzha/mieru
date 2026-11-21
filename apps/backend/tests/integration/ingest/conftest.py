from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from tests.integration.conftest import serve

from mieru.core import clock, redis
from mieru.core.config import Settings
from mieru.ingest.app import create_app
from mieru.ingest.refresh import refresh_once
from mieru.ingest.stream import STREAM

Refresh = Callable[[], Awaitable[None]]


@pytest.fixture
async def redis_db(settings: Settings) -> AsyncIterator[Redis]:
  """Redis database 15, emptied before the test: the one the app writes to"""
  client = redis.connect(settings)
  await client.flushdb()
  yield client
  await client.aclose()


@pytest.fixture
def ingest_app(settings: Settings) -> FastAPI:
  return create_app(settings)


@pytest.fixture
async def client(ingest_app: FastAPI, redis_db: Redis) -> AsyncIterator[httpx.AsyncClient]:
  """A client for the ingest app, started after Redis was emptied"""
  async with serve(ingest_app) as client:
    yield client


@pytest.fixture
def refresh(ingest_app: FastAPI, db_session: AsyncSession) -> Refresh:
  """Reload the app's caches from the test's session.

  The app's own engine can't see rows the test hasn't committed, so a test that adds a
  site calls this before posting.
  """
  state = ingest_app.state

  async def run() -> None:
    await refresh_once(db_session, state.sites, state.salts, today=clock.today())

  return run


async def stream_fields(redis_db: Redis) -> list[dict[str, str]]:
  """The fields of every entry in the stream, oldest first"""
  # The client decodes responses, so ids, keys and values are all str
  rows = cast(list[tuple[str, dict[str, str]]], await redis_db.xrange(STREAM))
  return [fields for _id, fields in rows]
