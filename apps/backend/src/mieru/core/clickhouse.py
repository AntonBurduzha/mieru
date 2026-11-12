import asyncio

import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient

from mieru.core.config import Settings


class ClickHouse:
    """A lazily connected HTTP client.

    The driver queries the server when it builds a client, so building it at startup
    would crash the api while ClickHouse is down. Here the first use connects, and a
    failed connect is tried again on the next use.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncClient | None = None
        # concurrent first requests share one connect instead of racing
        self._lock = asyncio.Lock()

    async def client(self) -> AsyncClient:
        """The connected client; raises the driver's error if ClickHouse is unreachable."""
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = await clickhouse_connect.get_async_client(
                        # http://user:password@host:8123/database
                        dsn=str(self._settings.clickhouse_url.get_secret_value()),
                        connect_timeout=2,
                        send_receive_timeout=30,
                    )
        return self._client

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                client, self._client = self._client, None
                await client.close()


async def ping(ch: ClickHouse) -> None:
    """Raise if ClickHouse cannot run SELECT 1."""
    client = await ch.client()
    await client.command("SELECT 1")
