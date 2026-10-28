from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mieru.core.config import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    """Build the engine and its pool"""
    return create_async_engine(
        str(settings.database_url),
        pool_size=5,
        pool_pre_ping=True,
        connect_args={"timeout": 2},
    )


async def ping(engine: AsyncEngine) -> None:
    """Raise if Postgres cannot run SELECT 1."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
