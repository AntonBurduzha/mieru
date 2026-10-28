from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from mieru.core.errors import (
    AppError,
    Conflict,
    Forbidden,
    NotFound,
    QuotaExceeded,
    RateLimited,
    Unauthorized,
    ValidationFailed,
    register_exception_handlers,
)


def build_app(*, hide_internal_errors: bool) -> FastAPI:
    """Tiny app whose routes fail in every way the real apps can."""
    app = FastAPI()
    register_exception_handlers(app, hide_internal_errors=hide_internal_errors)

    @app.get("/site")
    async def missing_site() -> None:
        raise NotFound("Site not found", details={"site_id": 7})

    @app.get("/teapot")
    async def custom_error() -> None:
        raise AppError("I'm a teapot", code="teapot", status_code=418)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database password is hunter2")

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    return app


def make_client(*, hide_internal_errors: bool) -> httpx.AsyncClient:
    """Client for build_app()"""
    transport = httpx.ASGITransport(
        app=build_app(hide_internal_errors=hide_internal_errors), raise_app_exceptions=False
    )
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(hide_internal_errors=True) as c:
        yield c


@pytest.mark.parametrize(
    ("error_class", "status", "code"),
    [
        (NotFound, 404, "not_found"),
        (Conflict, 409, "conflict"),
        (Unauthorized, 401, "unauthorized"),
        (Forbidden, 403, "forbidden"),
        (QuotaExceeded, 402, "quota_exceeded"),
        (ValidationFailed, 422, "validation_failed"),
        (RateLimited, 429, "rate_limited"),
    ],
)
def test_subclass_defaults(error_class: type[AppError], status: int, code: str) -> None:
    error = error_class("message")
    assert (error.status_code, error.code, error.message, error.details) == (
        status,
        code,
        "message",
        None,
    )


async def test_app_error_uses_unified_shape(client: httpx.AsyncClient) -> None:
    response = await client.get("/site")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Site not found", "details": {"site_id": 7}}
    }


async def test_code_and_status_can_be_overridden(client: httpx.AsyncClient) -> None:
    response = await client.get("/teapot")
    assert response.status_code == 418
    assert response.json()["error"]["code"] == "teapot"
    assert response.json()["error"]["message"] == "I'm a teapot"


async def test_unknown_route_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get("/nope")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "details": None}
    }


async def test_wrong_method_keeps_allow_header(client: httpx.AsyncClient) -> None:
    response = await client.post("/site")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert response.headers["allow"] == "GET"


async def test_validation_error_does_not_echo_input(client: httpx.AsyncClient) -> None:
    response = await client.get("/items/not-a-number")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert error["details"]["errors"][0]["loc"] == ["path", "item_id"]
    assert "not-a-number" not in response.text


async def test_unhandled_error_hides_message_when_asked(client: httpx.AsyncClient) -> None:
    response = await client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error", "details": None}
    }
    assert "hunter2" not in response.text


async def test_unhandled_error_shows_message_outside_prod() -> None:
    async with make_client(hide_internal_errors=False) as client:
        response = await client.get("/boom")
    assert response.status_code == 500
    assert response.json()["error"]["message"] == "RuntimeError: database password is hunter2"
