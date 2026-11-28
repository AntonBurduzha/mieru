from fastapi import APIRouter, Request, Response

from mieru.core import clock, health, redis
from mieru.core.errors import NotFound
from mieru.ingest.counters import Counters
from mieru.ingest.delivery import Delivery
from mieru.ingest.pipeline import MAX_BODY_BYTES, Dropped, DropReason, parse_event, process
from mieru.ingest.tracker import CACHE_CONTROL, Tracker, accepts_gzip

router = APIRouter()

DROPPED_HEADER = "X-Mieru-Dropped"
SCRIPT_TYPE = "text/javascript; charset=utf-8"


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
  delivery: Delivery = state.delivery
  counters: Counters = state.counters
  now = clock.now()
  result = process(
    parse_event(body),
    sites=state.sites,
    salts=state.salts,
    ip=client_ip(request),
    user_agent=request.headers.get("user-agent", ""),
    now=now,
  )
  if isinstance(result, Dropped):
    reason: DropReason | None = result.reason
  else:
    reason = await delivery.send(result, now=now)
  if reason is not None:
    counters.dropped[reason] += 1
    return dropped(reason)
  counters.accepted += 1
  return Response(status_code=202)


@router.api_route("/js/p.js", methods=["GET", "HEAD"])
async def script(request: Request) -> Response:
  """The tracking script, gzipped when the client takes it; cached for a day."""
  tracker: Tracker | None = request.app.state.tracker
  if tracker is None:
    raise NotFound("The tracking script is not built")
  # Vary, because a shared cache must not hand the gzipped copy to a client without gzip
  headers = {"Cache-Control": CACHE_CONTROL, "Vary": "Accept-Encoding"}
  body = tracker.body
  if accepts_gzip(request.headers.get("accept-encoding", "")):
    body = tracker.gzipped
    headers["Content-Encoding"] = "gzip"
  return Response(body, media_type=SCRIPT_TYPE, headers=headers)


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
