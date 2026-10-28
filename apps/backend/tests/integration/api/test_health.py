import httpx
import pytest
from pydantic import RedisDsn

from mieru.api.app import create_app
from mieru.core.config import Settings

pytestmark = pytest.mark.integration


async def test_liveness(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_when_all_dependencies_answer(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"postgres": "ok", "redis": "ok", "clickhouse": "ok"}


async def test_not_ready_when_redis_is_down(settings: Settings) -> None:
    # Port 1 has nothing listening: same effect as a stopped Redis, without touching compose
    broken = settings.model_copy(update={"redis_url": RedisDsn("redis://localhost:1/15")})
    app = create_app(broken)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"postgres": "ok", "redis": "error", "clickhouse": "ok"}


async def test_version(api_client: httpx.AsyncClient, settings: Settings) -> None:
    response = await api_client.get("/api/v1/version")
    assert response.json() == {"version": settings.git_sha}


async def test_unknown_route_uses_error_shape(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "x-request-id" in response.headers


async def test_openapi_is_under_api_prefix(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert "/health/ready" in response.json()["paths"]
