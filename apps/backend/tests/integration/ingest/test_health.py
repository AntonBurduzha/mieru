import httpx
import pytest

from mieru.core.config import Settings

pytestmark = pytest.mark.integration


async def test_liveness(ingest_client: httpx.AsyncClient) -> None:
    response = await ingest_client.get("/health")
    assert response.json() == {"status": "ok"}


async def test_ready_checks_redis_only(ingest_client: httpx.AsyncClient) -> None:
    response = await ingest_client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"redis": "ok"}


async def test_version(ingest_client: httpx.AsyncClient, settings: Settings) -> None:
    response = await ingest_client.get("/version")
    assert response.json() == {"version": settings.git_sha}


async def test_no_public_docs(ingest_client: httpx.AsyncClient) -> None:
    assert (await ingest_client.get("/docs")).status_code == 404
    assert (await ingest_client.get("/openapi.json")).status_code == 404
