"""The pipeline end to end: the tracker's posts, ingest, the stream, the writer, ClickHouse."""

import asyncio
import json

import httpx
import pytest
from redis.asyncio import Redis
from tests.integration.conftest import serve

from mieru.core import clock
from mieru.core.clickhouse import ClickHouse
from mieru.core.config import Settings
from mieru.ingest.app import create_app
from mieru.ingest.stream import STREAM
from mieru.models import Site
from mieru.writer.consumer import run_once

pytestmark = pytest.mark.e2e

CHROME = (
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
  " Chrome/131.0.0.0 Safari/537.36"
)
IP = "203.0.113.10"  # a documentation range, never a real visitor
REFERRER = "https://news.example/"


def tracker_body(name: str, url: str, *, domain: str, props: dict[str, str] | None = None) -> bytes:
  """The JSON the tracker posts: always the five keys, compact like JSON.stringify"""
  event = {"n": name, "d": domain, "u": url, "r": REFERRER, "p": props or {}}
  return json.dumps(event, separators=(",", ":")).encode()


async def post(client: httpx.AsyncClient, body: bytes) -> httpx.Response:
  # The headers a browser sends with the tracker's fetch; X-Real-IP is Caddy's
  headers = {"Content-Type": "text/plain", "User-Agent": CHROME, "X-Real-IP": IP}
  return await client.post("/api/event", content=body, headers=headers)


async def test_tracker_events_reach_clickhouse(
  settings: Settings, live_site: Site, redis_db: Redis
) -> None:
  # ClickHouse keeps whole seconds, so the lower bound is the start rounded down
  started = int(clock.now().timestamp())
  domain = live_site.domain
  bodies = [
    tracker_body("pageview", f"https://{domain}/", domain=domain),
    # The SPA's pushState: a new URL, and the page's referrer is still the first one
    tracker_body("pageview", f"https://{domain}/pricing?tab=2", domain=domain),
    tracker_body(
      "signup",
      f"https://{domain}/pricing?tab=2",
      domain=domain,
      props={"plan": "pro", "seats": "3"},
    ),
  ]
  # The real ingest app: its startup loads the committed site and today's salts
  async with serve(create_app(settings)) as client:
    responses = [await post(client, body) for body in bodies]
  assert [response.status_code for response in responses] == [202, 202, 202]
  assert [response.headers.get("x-mieru-dropped") for response in responses] == [None] * 3

  clickhouse = ClickHouse(settings)
  try:
    stored = await run_once(redis_db, clickhouse, batch_max=100, flush_ms=200, stop=asyncio.Event())
    ch = await clickhouse.client()
    result = await ch.query(
      "SELECT name, pathname, props, hostname, referrer, country, visitor_id,"
      " toUnixTimestamp(timestamp) FROM events WHERE site_id = {site_id:UInt32}"
      " ORDER BY name, pathname",
      parameters={"site_id": live_site.id},
    )
  finally:
    await clickhouse.close()
  ended = clock.now().timestamp()

  assert stored == 3
  rows = result.result_rows
  assert [(row[0], row[1], row[2]) for row in rows] == [
    ("pageview", "/", {}),
    ("pageview", "/pricing", {}),
    ("signup", "/pricing", {"plan": "pro", "seats": "3"}),
  ]
  # The driver reads a FixedString column as bytes
  assert {(row[3], row[4], row[5]) for row in rows} == {(domain, REFERRER, b"ZZ")}
  # One browser on one IP, on one day: one visitor
  visitor_ids = {row[6] for row in rows}
  assert len(visitor_ids) == 1
  assert visitor_ids != {0}
  assert all(started <= row[7] <= ended for row in rows)
  # Stored and deleted: nothing is left in the stream
  assert await redis_db.xlen(STREAM) == 0
