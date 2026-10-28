import json
import logging
from collections.abc import Iterator

import httpx
import pytest
import structlog
from fastapi import FastAPI

from mieru.core.logging import RequestContextMiddleware, configure_logging


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/fails")
    async def fails() -> None:
        # private - should never reach logs
        visitor_ip = "203.0.113.7"
        try:
            raise ValueError(f"no country for a {len(visitor_ip)}-character address")
        except ValueError:
            structlog.get_logger().exception("lookup_failed")

    @app.get("/whoami")
    async def whoami() -> dict[str, object]:
        structlog.get_logger().info("inside_route")
        return {"request_id": structlog.contextvars.get_contextvars().get("request_id")}

    return app


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test")


async def test_new_request_id_when_none_sent() -> None:
    async with client() as c:
        response = await c.get("/whoami")
    request_id = response.headers["x-request-id"]
    # uuid4().hex
    assert len(request_id) == 32
    assert response.json() == {"request_id": request_id}


async def test_valid_incoming_request_id_is_kept() -> None:
    async with client() as c:
        response = await c.get("/whoami", headers={"X-Request-Id": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"


@pytest.mark.parametrize("bad", ["has space", "x" * 65, 'quote"d'])
async def test_unsafe_incoming_request_id_is_replaced(bad: str) -> None:
    async with client() as c:
        response = await c.get("/whoami", headers={"X-Request-Id": bad})
    assert response.headers["x-request-id"] != bad


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Put the previous log handlers back after a test that reconfigures logging."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    yield
    root.handlers = saved_handlers  # the test's handler points at a stream pytest closes
    structlog.reset_defaults()


@pytest.mark.usefixtures("restore_logging")
async def test_log_lines_are_json_with_request_fields(capsys: pytest.CaptureFixture[str]) -> None:
    # Inside the test body, so the handler binds to the stderr that capsys captures
    configure_logging(service="api", env="test")
    async with client() as c:
        await c.get("/whoami?secret=1", headers={"X-Request-Id": "req-1"})

    lines = {
        record["event"]: record
        for record in map(json.loads, capsys.readouterr().err.splitlines())  # fails if not JSON
    }
    inside, finished = lines["inside_route"], lines["request_finished"]
    assert inside["request_id"] == "req-1"
    assert {k: finished[k] for k in ("service", "request_id", "path", "status", "level")} == {
        "service": "api",
        "request_id": "req-1",
        "path": "/whoami",  # path only: the query string never reaches the logs
        "status": 200,
        "level": "info",
    }
    assert {"timestamp", "duration_ms"} <= finished.keys()


@pytest.mark.usefixtures("restore_logging")
async def test_tracebacks_leave_out_local_variables(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service="api", env="test")
    async with client() as c:
        await c.get("/fails")

    output = capsys.readouterr().err
    record = next(r for r in map(json.loads, output.splitlines()) if r["event"] == "lookup_failed")
    assert record["exception"][0]["exc_type"] == "ValueError"
    assert "203.0.113.7" not in output
