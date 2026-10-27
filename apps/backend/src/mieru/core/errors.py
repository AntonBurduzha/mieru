from collections.abc import Mapping
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mieru.core.logging import REQUEST_ID_HEADER

log = structlog.get_logger(__name__)


class AppError(Exception):
    """Base class for errors the API reports to clients on purpose."""

    code: str = "app_error"
    status_code: int = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NotFound(AppError):
    code = "not_found"
    status_code = 404


class Conflict(AppError):
    code = "conflict"
    status_code = 409


class Unauthorized(AppError):
    code = "unauthorized"
    status_code = 401


class Forbidden(AppError):
    code = "forbidden"
    status_code = 403


class QuotaExceeded(AppError):
    code = "quota_exceeded"
    status_code = 402


class ValidationFailed(AppError):
    code = "validation_failed"
    status_code = 422


class RateLimited(AppError):
    code = "rate_limited"
    status_code = 429


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the unified error body."""
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(body, status_code=status_code, headers=headers)


async def _app_error(_request: Request, exc: Exception) -> JSONResponse:
    # For AppError only
    assert isinstance(exc, AppError)
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _http_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    return error_response(exc.status_code, code, exc.detail, headers=exc.headers)


async def _validation_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in exc.errors()
    ]
    return error_response(422, "validation_failed", "Request is invalid", {"errors": errors})


def register_exception_handlers(app: FastAPI, *, hide_internal_errors: bool) -> None:
    """Map every error the app can raise to the unified shape."""

    async def unhandled_error(_request: Request, exc: Exception) -> JSONResponse:
        # uvicorn logs the traceback; this line ties the failure to the request id
        log.error("unhandled_error", error_type=type(exc).__name__)
        if hide_internal_errors:
            message = "Internal server error"
        else:
            message = f"{type(exc).__name__}: {exc}"
        request_id = structlog.contextvars.get_contextvars().get("request_id")
        headers = {REQUEST_ID_HEADER: request_id} if request_id else None
        return error_response(500, "internal_error", message, headers=headers)

    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, unhandled_error)
