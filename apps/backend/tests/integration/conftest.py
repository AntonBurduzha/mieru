import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from pydantic import PostgresDsn, RedisDsn, Secret
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from mieru.api.app import create_app as create_api_app
from mieru.core.config import Settings, get_settings
from mieru.db.session import make_engine
from mieru.ingest.app import create_app as create_ingest_app

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE = "mieru_test"


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The developer's settings, switched to ENV=test, Redis database 15 and mieru_test"""
    base = get_settings()
    redis_test_url = str(base.redis_url.get_secret_value()).rsplit("/", 1)[0] + "/15"
    database_test_url = (
        str(base.database_url.get_secret_value()).rsplit("/", 1)[0] + "/" + TEST_DATABASE
    )
    return base.model_copy(
        update={
            "env": "test",
            "redis_url": Secret(RedisDsn(redis_test_url)),
            "database_url": Secret(PostgresDsn(database_test_url)),
        }
    )


@pytest.fixture(scope="session")
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    """A connection to the developer's own database, for CREATE and DROP DATABASE"""
    # Postgres refuses CREATE DATABASE inside a transaction, hence AUTOCOMMIT
    engine = create_async_engine(
        str(get_settings().database_url.get_secret_value()),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def alembic_config() -> Callable[[str], Config]:
    """A function that builds the Alembic config for the database at a URL"""

    def build(url: str) -> Config:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        # ConfigParser reads % as interpolation, so a % in a password must be doubled
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        config.attributes["configure_logger"] = False
        return config

    return build


@pytest.fixture(scope="session", autouse=True)
async def test_database(
    settings: Settings, admin_engine: AsyncEngine, alembic_config: Callable[[str], Config]
) -> None:
    """Create mieru_test if it's missing and migrate it to the latest revision, once per run"""
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DATABASE}
        )
        if not exists:
            await conn.execute(text(f"CREATE DATABASE {TEST_DATABASE}"))
    # env.py calls asyncio.run(), which can't start inside this running loop: use a thread
    config = alembic_config(str(settings.database_url.get_secret_value()))
    await asyncio.to_thread(command.upgrade, config, "head")


@pytest.fixture(scope="session")
async def db_engine(settings: Settings, test_database: None) -> AsyncIterator[AsyncEngine]:
    """The pool for mieru_test, shared by every test in the run"""
    engine = make_engine(settings)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose work, commits included, is rolled back when the test ends"""
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        # commit() and rollback() in the code under test act on a SAVEPOINT inside it
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


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
