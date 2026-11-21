import structlog
from fastapi import APIRouter, Request, Response
from redis.exceptions import RedisError

from mieru.core import clock, health, redis
from mieru.ingest import stream
from mieru.ingest.pipeline import MAX_BODY_BYTES, Dropped, DropReason, parse_event, process

log = structlog.get_logger(__name__)

router = APIRouter()

DROPPED_HEADER = "X-Mieru-Dropped"


@router.get("/health")
async def liveness() -> dict[str, str]:
  """The process is up and serving"""
  return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request, response: Response) -> dict[str, health.Status]:
  """Redis answers"""
  state = request.app.state
  results = await health.check({"redis": lambda: redis.ping(state.redis)})
  if "error" in results.values():
    response.status_code = 503
  return results


@router.get("/version")
async def version(request: Request) -> dict[str, str]:
  """The git commit this process was built from"""
  return {"version": request.app.state.settings.git_sha}


@router.post("/api/event")
async def event(request: Request) -> Response:
  """Take one event from the tracker: 202 whether it was kept or dropped."""
  body = await read_body(request, MAX_BODY_BYTES)
  state = request.app.state
  result = process(
    parse_event(body),
    sites=state.sites,
    salts=state.salts,
    ip=client_ip(request),
    user_agent=request.headers.get("user-agent", ""),
    now=clock.now(),
  )
  if isinstance(result, Dropped):
    return dropped(result.reason)
  try:
    await stream.append(state.redis, result.entry)
  except RedisError as exc:
    # The only trace of the outage until T12 adds drop counters
    log.warning("event_dropped", reason="redis_unavailable", error_type=type(exc).__name__)
    return dropped("redis_unavailable")
  return Response(status_code=202)


async def read_body(request: Request, limit: int) -> bytes:
  """The body, read only until it's longer than limit: a huge body is never read whole."""
  body = bytearray()
  async for chunk in request.stream():
    body += chunk
    if len(body) > limit:
      break
  return bytes(body)


def client_ip(request: Request) -> str:
  """X-Real-IP, which Caddy sets; else the TCP peer, for local runs without Caddy."""
  # X-Forwarded-For is never read: the client controls it. Run uvicorn with
  # --no-proxy-headers, or it rewrites request.client from that header
  real_ip = request.headers.get("x-real-ip")
  if real_ip:
    return real_ip
  return request.client.host if request.client else ""


def dropped(reason: DropReason) -> Response:
  """The answer to a dropped event: the same 202, with the reason in a header."""
  return Response(status_code=202, headers={DROPPED_HEADER: reason})
