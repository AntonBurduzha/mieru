"""Ingest's defences against real Redis (database 15): bots, spam, the rate limit,
backpressure, the fallback queue while Redis is down, and the drop counters."""

import json
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
import time_machine
from fastapi import FastAPI
from pydantic import RedisDsn, Secret
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs
from tests.factories import make_entry, make_site
from tests.integration.conftest import eventually, serve
from tests.integration.ingest.conftest import Refresh, stream_fields

from mieru.core import clock, redis
from mieru.core.config import Settings
from mieru.ingest.app import create_app
from mieru.ingest.counters import report
from mieru.ingest.delivery import Delivery
from mieru.ingest.pipeline import Accepted
from mieru.ingest.refresh import refresh_once
from mieru.ingest.stream import STREAM, StreamEntry

pytestmark = pytest.mark.integration

NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
IP = "203.0.113.78"  # a documentation range, never a real visitor
BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
CURL = "curl/8.7.1"
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
# Port 1 has nothing listening: every call is refused at once
DEAD_REDIS_URL = "redis://127.0.0.1:1/0"


def event(domain: str, **fields: object) -> bytes:
  return json.dumps({"n": "pageview", "d": domain, "u": f"https://{domain}/"} | fields).encode()


async def post(
  client: httpx.AsyncClient, body: bytes, *, user_agent: str = BROWSER
) -> httpx.Response:
  # Content-Type as the tracker sends it; X-Real-IP as Caddy sets it
  headers = {"Content-Type": "text/plain", "User-Agent": user_agent, "X-Real-IP": IP}
  return await client.post("/api/event", content=body, headers=headers)


async def rate_keys(redis_db: Redis) -> list[str]:
  return cast(list[str], await redis_db.keys("rl:*"))


async def load_caches(app: FastAPI, db_session: AsyncSession) -> None:
  """What the refresh fixture does, for an app the test built with its own settings"""
  await refresh_once(db_session, app.state.sites, app.state.salts, today=clock.today())


def dead_redis(settings: Settings) -> Redis:
  return redis.connect(settings.model_copy(update={"redis_url": Secret(RedisDsn(DEAD_REDIS_URL))}))


@asynccontextmanager
async def redis_down(delivery: Delivery, settings: Settings) -> AsyncIterator[None]:
  """Point the delivery at a Redis that refuses every call; give the working one back after."""
  dead = dead_redis(settings)
  working, delivery.redis = delivery.redis, dead
  try:
    yield
  finally:
    delivery.redis = working
    await dead.aclose()


def kept(entry: StreamEntry) -> Accepted:
  """What process() hands to delivery for a kept event"""
  return Accepted(entry=entry, rate_limit_per_min=6000)


async def hold(delivery: Delivery, settings: Settings, entries: list[StreamEntry]) -> None:
  """Send the entries while Redis is down, so they wait in the delivery's queue."""
  async with redis_down(delivery, settings):
    for entry in entries:
      assert await delivery.send(kept(entry), now=NOON) is None


async def stream_entries(redis_db: Redis) -> list[StreamEntry]:
  return [StreamEntry.from_fields(fields) for fields in await stream_fields(redis_db)]


# Bots and spam referrers, through the app


@pytest.mark.parametrize("user_agent", [CURL, "", GOOGLEBOT], ids=["curl", "empty", "googlebot"])
async def test_bot_is_dropped_with_202_and_leaves_nothing_in_redis(
  client: httpx.AsyncClient,
  redis_db: Redis,
  db_session: AsyncSession,
  refresh: Refresh,
  user_agent: str,
) -> None:
  await make_site(db_session, domain="crawled.example")
  await refresh()
  response = await post(client, event("crawled.example"), user_agent=user_agent)

  assert response.status_code == 202
  assert response.headers["x-mieru-dropped"] == "bot"
  assert await stream_fields(redis_db) == []
  assert await rate_keys(redis_db) == []


async def test_spam_referrer_is_dropped_with_202_and_leaves_nothing_in_redis(
  client: httpx.AsyncClient, redis_db: Redis, db_session: AsyncSession, refresh: Refresh
) -> None:
  await make_site(db_session, domain="referred.example")
  await refresh()
  response = await post(client, event("referred.example", r="https://www.0-0.fr/"))

  assert response.status_code == 202
  assert response.headers["x-mieru-dropped"] == "spam_referrer"
  assert await stream_fields(redis_db) == []
  assert await rate_keys(redis_db) == []


async def test_unknown_site_with_a_bot_user_agent_is_dropped_as_unknown_site(
  client: httpx.AsyncClient, redis_db: Redis
) -> None:
  response = await post(client, event("unregistered.example"), user_agent=CURL)
  assert response.status_code == 202
  assert response.headers["x-mieru-dropped"] == "unknown_site"


async def test_startup_complete_logs_how_many_referrer_domains_are_loaded(
  settings: Settings, redis_db: Redis
) -> None:
  # Built outside capture_logs: create_app configures logging, which would end the capture
  app = create_app(settings)
  with capture_logs() as logs:
    async with serve(app):
      pass
  [startup] = [log for log in logs if log["event"] == "startup_complete"]
  assert startup["spam_domains"] > 2000


# The per-site rate limit


async def test_fourth_event_in_a_minute_is_dropped_as_rate_limited(
  client: httpx.AsyncClient, redis_db: Redis, db_session: AsyncSession, refresh: Refresh
) -> None:
  site = await make_site(db_session, domain="limited.example")
  site.rate_limit_per_min = 3
  await db_session.flush()
  key = f"rl:{site.id}:{int(NOON.timestamp()) // 60}"
  with time_machine.travel(NOON, tick=False):
    await refresh()
    responses = [await post(client, event("limited.example")) for _ in range(4)]

  assert [response.status_code for response in responses] == [202] * 4
  assert [response.headers.get("x-mieru-dropped") for response in responses] == [
    None,
    None,
    None,
    "rate_limited",
  ]
  assert len(await stream_fields(redis_db)) == 3
  # Every event is counted, the one over the limit too
  assert await redis_db.get(key) == "4"
  assert 0 < await redis_db.ttl(key) <= 120


async def test_rate_limited_site_is_accepted_again_in_the_next_minute(
  client: httpx.AsyncClient, redis_db: Redis, db_session: AsyncSession, refresh: Refresh
) -> None:
  site = await make_site(db_session, domain="next-minute.example")
  site.rate_limit_per_min = 1
  await db_session.flush()
  # The last second of one UTC minute, then the first of the next
  with time_machine.travel(NOON + timedelta(seconds=59), tick=False) as traveller:
    await refresh()
    this_minute = [await post(client, event("next-minute.example")) for _ in range(2)]
    traveller.move_to(NOON + timedelta(minutes=1))
    next_minute = await post(client, event("next-minute.example"))

  assert [response.headers.get("x-mieru-dropped") for response in this_minute] == [
    None,
    "rate_limited",
  ]
  assert "x-mieru-dropped" not in next_minute.headers
  assert len(await stream_fields(redis_db)) == 2
  assert await redis_db.get(f"rl:{site.id}:{int(NOON.timestamp()) // 60 + 1}") == "1"


# Backpressure: the stream is over its high-water mark


async def test_event_over_the_high_water_mark_is_dropped_as_backpressure(
  settings: Settings, redis_db: Redis, db_session: AsyncSession
) -> None:
  await make_site(db_session, domain="busy.example")
  app = create_app(settings.model_copy(update={"stream_high_water": 2}))
  async with serve(app) as client:
    await load_caches(app, db_session)
    for filler in range(3):
      await redis_db.xadd(STREAM, {"filler": str(filler)})
    await app.state.delivery.check_stream()
    response = await post(client, event("busy.example"))

    assert response.status_code == 202
    assert response.headers["x-mieru-dropped"] == "backpressure"
    assert await redis_db.xlen(STREAM) == 3
    assert await rate_keys(redis_db) == []


async def test_backpressure_drops_end_once_a_check_sees_the_stream_below_high_water(
  settings: Settings, redis_db: Redis, db_session: AsyncSession
) -> None:
  await make_site(db_session, domain="calmer.example")
  app = create_app(settings.model_copy(update={"stream_high_water": 2}))
  async with serve(app) as client:
    await load_caches(app, db_session)
    delivery = app.state.delivery
    fillers = [await redis_db.xadd(STREAM, {"filler": str(filler)}) for filler in range(3)]
    await delivery.check_stream()
    over = await post(client, event("calmer.example"))
    # The writer catches up
    await redis_db.xdel(STREAM, *fillers)
    await delivery.check_stream()
    below = await post(client, event("calmer.example"))

    assert over.headers["x-mieru-dropped"] == "backpressure"
    assert "x-mieru-dropped" not in below.headers
    assert [fields["u"] for fields in await stream_fields(redis_db)] == ["https://calmer.example/"]


async def test_backpressure_drop_needs_no_redis_call(
  settings: Settings, redis_db: Redis, db_session: AsyncSession
) -> None:
  await make_site(db_session, domain="no-call.example")
  app = create_app(settings.model_copy(update={"stream_high_water": 2}))
  async with serve(app) as client:
    await load_caches(app, db_session)
    delivery = app.state.delivery
    for filler in range(3):
      await redis_db.xadd(STREAM, {"filler": str(filler)})
    await delivery.check_stream()
    # A call to this Redis would fail and hold the event instead of dropping it
    async with redis_down(delivery, settings):
      response = await post(client, event("no-call.example"))
      held = len(delivery.queue)

  assert response.headers["x-mieru-dropped"] == "backpressure"
  assert held == 0


async def test_backpressure_check_keeps_the_last_length_while_redis_is_down(
  settings: Settings, redis_db: Redis
) -> None:
  delivery = Delivery(redis_db, high_water=2, fallback_size=10)
  assert delivery.stream_length == 0
  for filler in range(3):
    await redis_db.xadd(STREAM, {"filler": str(filler)})
  await delivery.check_stream()
  assert delivery.stream_length == 3

  async with redis_down(delivery, settings):
    await delivery.check_stream()
    assert delivery.stream_length == 3


# The fallback queue: Redis can't be reached


async def test_fallback_holds_new_events_without_touching_redis_while_it_holds_any(
  settings: Settings, redis_db: Redis
) -> None:
  delivery = Delivery(redis_db, high_water=1000, fallback_size=10)
  first, second = make_entry(7), make_entry(7)
  await hold(delivery, settings, [first])

  # Redis answers again, but nothing has drained yet: the second event waits behind the first
  assert await delivery.send(kept(second), now=NOON) is None
  assert list(delivery.queue.oldest(5)) == [first, second]
  assert await stream_fields(redis_db) == []
  assert await rate_keys(redis_db) == []


async def test_fallback_drain_writes_held_events_in_post_order(
  settings: Settings, redis_db: Redis
) -> None:
  delivery = Delivery(redis_db, high_water=1000, fallback_size=10)
  entries = [make_entry(7, u=f"https://example.com/{page}") for page in ("a", "b", "c")]
  await hold(delivery, settings, entries)

  with capture_logs() as logs:
    written = await delivery.drain()

  assert written == 3
  assert await stream_entries(redis_db) == entries
  assert len(delivery.queue) == 0
  [drained] = [log for log in logs if log["event"] == "fallback_drained"]
  assert drained["events"] == 3


async def test_fallback_drain_writes_more_than_one_batch_in_post_order(
  settings: Settings, redis_db: Redis
) -> None:
  delivery = Delivery(redis_db, high_water=10_000, fallback_size=2000)
  # Batches of 500: two full ones and one entry
  entries = [make_entry(7, u=f"https://example.com/{page}") for page in range(1001)]
  await hold(delivery, settings, entries)

  assert await delivery.drain() == 1001
  assert await stream_entries(redis_db) == entries
  assert len(delivery.queue) == 0


async def test_fallback_drain_keeps_the_queue_while_redis_is_down(
  settings: Settings, redis_db: Redis
) -> None:
  delivery = Delivery(redis_db, high_water=1000, fallback_size=10)
  entries = [make_entry(7), make_entry(7)]
  await hold(delivery, settings, entries)

  async with redis_down(delivery, settings):
    with capture_logs() as logs:
      written = await delivery.drain()

  assert written == 0
  assert list(delivery.queue.oldest(5)) == entries
  assert [log for log in logs if log["event"] == "fallback_drained"] == []


async def test_fallback_of_size_0_drops_every_event_as_redis_unavailable(
  settings: Settings, redis_db: Redis
) -> None:
  delivery = Delivery(redis_db, high_water=1000, fallback_size=0)
  async with redis_down(delivery, settings):
    results = [await delivery.send(kept(make_entry(7)), now=NOON) for _ in range(2)]

  assert results == ["redis_unavailable", "redis_unavailable"]
  assert len(delivery.queue) == 0


async def test_fallback_is_drained_by_the_background_run_once_redis_answers(
  ingest_app: FastAPI,
  client: httpx.AsyncClient,
  settings: Settings,
  redis_db: Redis,
  db_session: AsyncSession,
  refresh: Refresh,
) -> None:
  await make_site(db_session, domain="background.example")
  await refresh()
  delivery = ingest_app.state.delivery
  pages = ["https://background.example/a", "https://background.example/b"]

  with capture_logs() as logs:
    async with redis_down(delivery, settings):
      for page in pages:
        await post(client, event("background.example", u=page))
      assert len(delivery.queue) == 2

    async def drained() -> bool:
      return len(delivery.queue) == 0 and any(log["event"] == "fallback_drained" for log in logs)

    # Nothing calls drain() here: the app's own background task does
    await eventually(drained, timeout_s=5)

  assert [fields["u"] for fields in await stream_fields(redis_db)] == pages
  [line] = [log for log in logs if log["event"] == "fallback_drained"]
  assert line["events"] == 2


async def test_fallback_held_events_are_written_when_the_lifespan_ends(
  settings: Settings, redis_db: Redis, db_session: AsyncSession
) -> None:
  await make_site(db_session, domain="shutdown.example")
  app = create_app(settings)
  dead = dead_redis(settings)
  try:
    with capture_logs() as logs:
      async with serve(app) as client:
        await load_caches(app, db_session)
        delivery = app.state.delivery
        working, delivery.redis = delivery.redis, dead
        response = await post(client, event("shutdown.example"))
        held = len(delivery.queue)
        # Redis is back just as the app stops: the shutdown writes what waits
        delivery.redis = working
  finally:
    await dead.aclose()

  assert "x-mieru-dropped" not in response.headers
  assert held == 1
  assert [fields["u"] for fields in await stream_fields(redis_db)] == ["https://shutdown.example/"]
  assert [log for log in logs if log["event"] == "fallback_lost"] == []


async def test_fallback_left_after_the_shutdown_drain_is_logged_as_lost(
  settings: Settings, redis_db: Redis, db_session: AsyncSession
) -> None:
  await make_site(db_session, domain="lost.example")
  app = create_app(settings)
  dead = dead_redis(settings)
  try:
    with capture_logs() as logs:
      async with serve(app) as client:
        await load_caches(app, db_session)
        # Redis stays down through the shutdown
        app.state.delivery.redis = dead
        await post(client, event("lost.example"))
  finally:
    await dead.aclose()

  [lost] = [log for log in logs if log["event"] == "fallback_lost"]
  assert lost["events"] == 1
  assert await stream_fields(redis_db) == []


async def test_fallback_full_drops_the_event_as_redis_unavailable_and_logs_fallback_full_once(
  settings: Settings, redis_db: Redis, db_session: AsyncSession
) -> None:
  await make_site(db_session, domain="full.example")
  app = create_app(settings.model_copy(update={"ingest_fallback_queue": 1}))
  async with serve(app) as client:
    await load_caches(app, db_session)
    delivery = app.state.delivery
    with capture_logs() as logs:
      async with redis_down(delivery, settings):
        responses = [await post(client, event("full.example")) for _ in range(3)]
        held = len(delivery.queue)

  assert [response.status_code for response in responses] == [202] * 3
  assert [response.headers.get("x-mieru-dropped") for response in responses] == [
    None,
    "redis_unavailable",
    "redis_unavailable",
  ]
  assert held == 1
  [full] = [log for log in logs if log["event"] == "fallback_full"]
  assert full["size"] == 1


# Drop counters


async def test_every_outcome_is_counted_and_the_report_holds_no_ip_or_user_agent(
  ingest_app: FastAPI,
  client: httpx.AsyncClient,
  redis_db: Redis,
  db_session: AsyncSession,
  refresh: Refresh,
) -> None:
  await make_site(db_session, domain="counted.example")
  await refresh()
  referrer = "https://news.example/a-private-path"

  with capture_logs() as logs:
    await post(client, event("counted.example"), user_agent=CURL)
    await post(client, event("unregistered.example"))
    await post(client, event("counted.example", r=referrer))
    report(ingest_app.state.counters)

  [line] = [log for log in logs if log["event"] == "events_counted"]
  assert line["accepted"] == 1
  assert line["dropped"] == {"bot": 1, "unknown_site": 1}
  # Every captured line, not only the report
  captured = repr(logs)
  for private in (IP, BROWSER, CURL, referrer):
    assert private not in captured


async def test_held_event_is_counted_as_accepted(
  ingest_app: FastAPI,
  client: httpx.AsyncClient,
  settings: Settings,
  redis_db: Redis,
  db_session: AsyncSession,
  refresh: Refresh,
) -> None:
  await make_site(db_session, domain="held-count.example")
  await refresh()
  async with redis_down(ingest_app.state.delivery, settings):
    await post(client, event("held-count.example"))

  assert ingest_app.state.counters.take() == (1, Counter())


async def test_delivery_drop_is_counted_under_its_reason(
  ingest_app: FastAPI,
  client: httpx.AsyncClient,
  redis_db: Redis,
  db_session: AsyncSession,
  refresh: Refresh,
) -> None:
  site = await make_site(db_session, domain="counted-limit.example")
  site.rate_limit_per_min = 1
  await db_session.flush()
  with time_machine.travel(NOON, tick=False):
    await refresh()
    for _ in range(2):
      await post(client, event("counted-limit.example"))

  assert ingest_app.state.counters.take() == (1, Counter({"rate_limited": 1}))


async def test_shutdown_reports_what_was_counted_since_the_last_report(
  settings: Settings, redis_db: Redis, db_session: AsyncSession
) -> None:
  await make_site(db_session, domain="last-report.example")
  app = create_app(settings)
  with capture_logs() as logs:
    async with serve(app) as client:
      await load_caches(app, db_session)
      await post(client, event("last-report.example"))
      await post(client, event("last-report.example"), user_agent=CURL)

  [line] = [log for log in logs if log["event"] == "events_counted"]
  assert (line["accepted"], line["dropped"]) == (1, {"bot": 1})
