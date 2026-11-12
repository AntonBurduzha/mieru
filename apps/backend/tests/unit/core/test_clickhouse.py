import asyncio

import clickhouse_connect
import pytest
from clickhouse_connect.driver.exceptions import OperationalError

from mieru.core import clickhouse
from mieru.core.config import Settings


class FakeClient:
    async def close(self) -> None:
        return None


@pytest.mark.usefixtures("no_settings_env")
async def test_clickhouse_client_retries_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # typed as object: the fake stands in for the driver's AsyncClient
    fake_client: object = FakeClient()
    connects: list[int] = []

    async def fake_get_async_client(*_args: object, **_kwargs: object) -> object:
        connects.append(1)
        if len(connects) == 1:
            raise OperationalError("ClickHouse is not answering")
        return fake_client

    monkeypatch.setattr(clickhouse_connect, "get_async_client", fake_get_async_client)
    settings = Settings.model_validate(
        {
            "env": "test",
            "database_url": "postgresql+asyncpg://mieru:mieru@localhost:1/mieru",
            "redis_url": "redis://localhost:1/15",
            "clickhouse_url": "http://mieru:mieru@localhost:1/mieru",
        }
    )
    ch = clickhouse.ClickHouse(settings)

    with pytest.raises(OperationalError):
        await ch.client()
    assert await ch.client() is fake_client  # tried again after the failure
    assert await ch.client() is fake_client  # and kept the client it made
    assert len(connects) == 2


def settings_for_fake_connect() -> Settings:
    return Settings.model_validate(
        {
            "env": "test",
            "database_url": "postgresql+asyncpg://mieru:mieru@localhost:1/mieru",
            "redis_url": "redis://localhost:1/15",
            "clickhouse_url": "http://mieru:mieru@localhost:1/mieru",
        }
    )


class ClosableClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.usefixtures("no_settings_env")
async def test_concurrent_first_calls_connect_once(monkeypatch: pytest.MonkeyPatch) -> None:
    connects: list[int] = []

    async def slow_connect(*_args: object, **_kwargs: object) -> object:
        connects.append(1)
        await asyncio.sleep(0.01)  # both callers arrive while the first connect is running
        return ClosableClient()

    monkeypatch.setattr(clickhouse_connect, "get_async_client", slow_connect)
    ch = clickhouse.ClickHouse(settings_for_fake_connect())

    first, second = await asyncio.gather(ch.client(), ch.client())

    assert first is second
    assert len(connects) == 1


@pytest.mark.usefixtures("no_settings_env")
async def test_close_waits_for_a_connect_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    made = ClosableClient()
    started = asyncio.Event()

    async def slow_connect(*_args: object, **_kwargs: object) -> object:
        started.set()
        await asyncio.sleep(0.01)
        return made

    monkeypatch.setattr(clickhouse_connect, "get_async_client", slow_connect)
    ch = clickhouse.ClickHouse(settings_for_fake_connect())

    connecting = asyncio.create_task(ch.client())
    await started.wait()
    await ch.close()  # shutdown arrives while the first request is still connecting
    await connecting

    assert made.closed
