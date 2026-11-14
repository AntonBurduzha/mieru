from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
  AsyncEngine,
  AsyncSession,
  async_sessionmaker,
  create_async_engine,
)

from mieru.core.config import Settings


def make_engine(settings: Settings) -> AsyncEngine:
  """Build the engine and its pool"""
  return create_async_engine(
    str(settings.database_url.get_secret_value()),
    pool_size=5,
    pool_pre_ping=True,
    connect_args={"timeout": 2},
  )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
  """Build the session factory. Objects stay readable after commit, without a new query."""
  # In async code a lazy reload of an expired attribute can't run, it raises instead
  return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> None:
  """Raise if Postgres cannot run SELECT 1."""
  async with engine.connect() as conn:
    await conn.execute(text("SELECT 1"))
