from redis.asyncio import Redis

from mieru.core.config import Settings


def connect(settings: Settings) -> Redis:
    """Build a client with its own connection pool"""
    return Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        health_check_interval=30,
    )


async def ping(client: Redis) -> None:
    """Raise if Redis does not answer PING."""
    await client.ping()
