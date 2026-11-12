import logging
import subprocess
from collections.abc import Callable, Iterator
from typing import Literal

import httpx
import pytest
import structlog
from fastapi import FastAPI

from mieru.api import app as api_app
from mieru.core import clickhouse as clickhouse_module
from mieru.core import redis as redis_module
from mieru.core.config import Settings

Env = Literal["local", "test", "prod"]

# Port 1 has nothing listening: nothing in these tests may reach a real store
UNREACHABLE = {
    "database_url": "postgresql+asyncpg://mieru:mieru@localhost:1/mieru",
    "redis_url": "redis://localhost:1/15",
    "clickhouse_url": "http://mieru:mieru@localhost:1/mieru",
}

FAILURE_DETAIL = "pool exhausted for tenant 4242"


@pytest.fixture(autouse=True)
def restore_logging(no_settings_env: None) -> Iterator[None]:
    """create_app() configures logging; put the previous handlers back afterwards."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    yield
    root.handlers = saved_handlers
    structlog.reset_defaults()


def make_settings(env: Env) -> Settings:
    return Settings.model_validate({"env": env, **UNREACHABLE})


def build_app(env: Env) -> FastAPI:
    """The real api app, plus one route that fails unexpectedly."""
    app = api_app.create_app(make_settings(env))

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(FAILURE_DETAIL)

    return app


def client_for(app: FastAPI) -> httpx.AsyncClient:
    # ASGITransport never runs the lifespan, so no store is opened
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.parametrize(
    ("env", "details_shown"), [("local", True), ("test", False), ("prod", False)]
)
async def test_internal_errors_hidden_unless_local(env: Env, details_shown: bool) -> None:
    async with client_for(build_app(env)) as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    message = response.json()["error"]["message"]
    assert (FAILURE_DETAIL in message) is details_shown
    if not details_shown:
        assert message == "Internal server error"


@pytest.mark.parametrize(("env", "status"), [("local", 200), ("test", 200), ("prod", 404)])
async def test_no_docs_in_prod(env: Env, status: int) -> None:
    async with client_for(build_app(env)) as client:
        openapi = await client.get("/api/v1/openapi.json")
        docs = await client.get("/api/v1/docs")

    assert (openapi.status_code, docs.status_code) == (status, status)


def test_import_builds_nothing(
    import_in_clean_process: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    result = import_in_clean_process("mieru.api.app")
    assert result.returncode == 0, result.stderr


class FakeClickHouse:
    def __init__(self, closed: list[str]) -> None:
        self.closed = closed

    async def close(self) -> None:
        self.closed.append("clickhouse")


class FakeRedis:
    def __init__(self, closed: list[str]) -> None:
        self.closed = closed

    async def aclose(self) -> None:
        self.closed.append("redis")


async def test_failed_startup_closes_opened_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[str] = []

    def broken_engine(_settings: Settings) -> None:
        raise RuntimeError("engine could not be built")

    monkeypatch.setattr(clickhouse_module, "ClickHouse", lambda _settings: FakeClickHouse(closed))
    monkeypatch.setattr(redis_module, "connect", lambda _settings: FakeRedis(closed))
    monkeypatch.setattr(api_app, "make_engine", broken_engine)
    app = api_app.create_app(make_settings("test"))

    with pytest.raises(RuntimeError, match="engine could not be built"):
        async with app.router.lifespan_context(app):
            pass

    assert sorted(closed) == ["clickhouse", "redis"]
