import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient

from mieru.core.config import Settings


async def connect(settings: Settings) -> AsyncClient:
    """Open an HTTP client"""
    return await clickhouse_connect.get_async_client(
        # http://user:password@host:8123/database
        dsn=str(settings.clickhouse_url),
        connect_timeout=2,
        send_receive_timeout=30,
    )


async def ping(client: AsyncClient) -> None:
    """Raise if ClickHouse cannot run SELECT 1."""
    await client.command("SELECT 1")
