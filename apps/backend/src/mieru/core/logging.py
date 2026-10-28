import logging
import re
import time
import uuid
from typing import Literal

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.typing import EventDict, Processor, WrappedLogger

REQUEST_ID_HEADER = "X-Request-Id"
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")

log = structlog.get_logger(__name__)


def configure_logging(*, service: str, env: Literal["local", "test", "prod"]) -> None:
    """Route structlog and stdlib logging (uvicorn, libraries) through one formatter."""

    def add_service(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
        event_dict["service"] = service
        return event_dict

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        add_service,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    json_tracebacks = structlog.processors.ExceptionRenderer(
        structlog.tracebacks.ExceptionDictTransformer(show_locals=False),
    )
    renderer = list[Processor](
        [structlog.dev.ConsoleRenderer()]
        if env == "local"
        else [json_tracebacks, structlog.processors.JSONRenderer()]
    )
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *renderer],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # httpx logs every request URL at INFO, and a URL can carry tokens or personal data
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # uvicorn's access log prints the client IP
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True


def _request_id_from(scope: Scope) -> str:
    """The caller's X-Request-Id if it is safe to reuse, otherwise a new random id."""
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            candidate: str = value.decode("latin-1")
            if REQUEST_ID_RE.fullmatch(candidate):
                return candidate
            break
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, access_log: bool = True) -> None:
        self.app = app
        self.access_log = access_log

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id_from(scope)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, path=scope["path"])
        # stays 500 if the app raises before sending a response
        status = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            if self.access_log:
                log.info(
                    "request_finished",
                    method=scope["method"],
                    status=status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
