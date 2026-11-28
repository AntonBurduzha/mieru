import json
from datetime import UTC, datetime

import httpx
import pytest
import time_machine
from fastapi import FastAPI
from pydantic import RedisDsn, Secret
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs
from tests.factories import make_site
from tests.integration.ingest.conftest import Refresh, stream_fields

from mieru.core import redis
from mieru.core.config import Settings
from mieru.ingest.pipeline import MAX_BODY_BYTES
from mieru.ingest.stream import StreamEntry

pytestmark = pytest.mark.integration

NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
PRIVACY_IP = "203.0.113.77"  # a documentation range, never a real visitor


def event(domain: str, **fields: object) -> bytes:
  return json.dumps({"n": "pageview", "d": domain, "u": f"https://{domain}/"} | fields).encode()


async def post(
  client: httpx.AsyncClient, body: bytes, ip: str = PRIVACY_IP, **headers: str
) -> httpx.Response:
  # Content-Type as the tracker sends it: text/plain needs no CORS preflight
  base = {"Content-Type": "text/plain", "User-Agent": "MieruTest/1.0", "X-Real-IP": ip}
  return await client.post("/api/event", content=body, headers=base | headers)


async def entries(redis_db: Redis) -> list[StreamEntry]:
  return [StreamEntry.from_fields(fields) for fields in await stream_fields(redis_db)]


async def test_accepted_event_is_appended_to_the_stream(
  client: httpx.AsyncClient, redis_db: Redis, db_session: AsyncSession, refresh: Refresh
) -> None:
  site = await make_site(db_session, domain="accepted.example")
  with time_machine.travel(NOON, tick=False):
    await refresh()
    body = event("accepted.example", n="signup", r="https://news.example/", p={"plan": "pro"})
    response = await post(client, body)

  assert response.status_code == 202
  assert "x-mieru-dropped" not in response.headers
  [entry] = await entries(redis_db)
  assert (entry.v, entry.site_id, entry.org_id) == (1, site.id, site.organization_id)
  assert entry.ts == 1_794_657_600_000  # NOON in milliseconds
  assert (entry.n, entry.u, entry.r, entry.p) == (
    "signup",
    "https://accepted.example/",
    "https://news.example/",
    {"plan": "pro"},
  )
  assert (entry.ua, entry.cc) == ("MieruTest/1.0", "ZZ")


@pytest.mark.parametrize(
  "body",
  [
    b"not json",
    event("x.example", u="ftp://x.example/"),
    b'{"n": "pageview"}',
    # Valid JSON with an ignored key: only the size cap refuses it
    event("x.example", pad="x" * MAX_BODY_BYTES),
  ],
  ids=["not_json", "ftp_url", "missing_fields", "over_8_kb"],
)
async def test_invalid_body_is_400_and_nothing_is_stored(
  client: httpx.AsyncClient, redis_db: Redis, body: bytes
) -> None:
  response = await post(client, body)
  assert response.status_code == 400
  assert response.json()["error"] == {
    "code": "invalid_event",
    "message": "Malformed event",
    "details": None,
  }
  assert await stream_fields(redis_db) == []


async def test_unknown_site_gets_202_and_nothing_is_stored(
  client: httpx.AsyncClient, redis_db: Redis
) -> None:
  response = await post(client, event("unregistered.example"))
  assert response.status_code == 202
  assert response.headers["x-mieru-dropped"] == "unknown_site"
  assert response.content == b""
  assert await stream_fields(redis_db) == []


async def test_unknown_site_check_matches_www_and_capitals(
  client: httpx.AsyncClient, redis_db: Redis, db_session: AsyncSession, refresh: Refresh
) -> None:
  await make_site(db_session, domain="spelled.example")
  await refresh()
  response = await post(client, event("WWW.Spelled.EXAMPLE"))
  assert "x-mieru-dropped" not in response.headers
  assert len(await entries(redis_db)) == 1


async def test_ip_from_x_real_ip_decides_the_visitor_and_forwarded_headers_do_not(
  client: httpx.AsyncClient, redis_db: Redis, db_session: AsyncSession, refresh: Refresh
) -> None:
  await make_site(db_session, domain="ip.example")
  await refresh()
  body = event("ip.example")
  await post(client, body)
  await post(client, body, **{"X-Forwarded-For": "198.51.100.1", "Forwarded": "for=198.51.100.1"})
  await post(client, body, ip="198.51.100.2")

  first, forwarded, other_ip = await entries(redis_db)
  assert forwarded.vid == first.vid
  assert other_ip.vid != first.vid


async def test_ip_never_reaches_the_stream(
  client: httpx.AsyncClient, redis_db: Redis, db_session: AsyncSession, refresh: Refresh
) -> None:
  await make_site(db_session, domain="private.example")
  await refresh()
  await post(client, event("private.example", p={"note": "hello"}))

  [fields] = await stream_fields(redis_db)
  assert all(PRIVACY_IP not in f"{key}={value}" for key, value in fields.items())


async def test_redis_unavailable_still_answers_202(
  client: httpx.AsyncClient,
  ingest_app: FastAPI,
  settings: Settings,
  redis_db: Redis,
  db_session: AsyncSession,
  refresh: Refresh,
) -> None:
  await make_site(db_session, domain="redis-down.example")
  await refresh()
  delivery = ingest_app.state.delivery
  # Port 1 has nothing listening: the connection is refused at once
  down = settings.model_copy(update={"redis_url": Secret(RedisDsn("redis://127.0.0.1:1/0"))})
  working, delivery.redis = delivery.redis, redis.connect(down)
  try:
    with capture_logs() as logs:
      response = await post(client, event("redis-down.example"))
    # Checked before Redis comes back, so the background drain can't have run yet
    held = list(delivery.queue.oldest(10))
    stored = await stream_fields(redis_db)
  finally:
    dead, delivery.redis = delivery.redis, working
    await dead.aclose()
  # Held, not dropped: the answer is the same as for a kept event
  assert response.status_code == 202
  assert "x-mieru-dropped" not in response.headers
  assert [entry.u for entry in held] == ["https://redis-down.example/"]
  assert stored == []
  [outage] = [log for log in logs if log["event"] == "redis_unavailable"]
  assert outage["error_type"] == "ConnectionError"
