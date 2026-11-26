"""GET and HEAD /js/p.js: the tracker served from memory, and ingest running without it."""

import gzip
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs
from tests.factories import make_site
from tests.integration.conftest import serve
from tests.integration.ingest.conftest import Refresh, stream_fields

from mieru.core.config import Settings
from mieru.ingest.app import create_app

pytestmark = pytest.mark.integration

# Not the real tracker: any script will do, and a repeated one compresses well
SCRIPT = b'(()=>{const e="mieru";' + b"console.log(e);" * 40 + b"})();\n"


@pytest.fixture
def script() -> bytes | None:
  """The bytes of the script file, or None for no file. The served tests set it."""
  return None


@pytest.fixture
def ingest_app(settings: Settings, tmp_path: Path, script: bytes | None) -> FastAPI:
  """The ingest app with its script in tmp_path. client and refresh use this one.

  The file, if there is one, is written before the app is built, so its startup reads it.
  """
  path = tmp_path / "p.js"
  if script is not None:
    path.write_bytes(script)
  return create_app(settings, tracker_path=path)


# A test parametrized on a fixture's name gets that value instead of the fixture's
with_script = pytest.mark.parametrize("script", [SCRIPT], ids=["script"])


async def fetch(
  client: httpx.AsyncClient, method: str, accept_encoding: str
) -> tuple[httpx.Response, bytes]:
  """The response and its body as sent, before httpx undoes any Content-Encoding"""
  # Explicit, because httpx sends "gzip, deflate" by default
  headers = {"Accept-Encoding": accept_encoding}
  async with client.stream(method, "/js/p.js", headers=headers) as response:
    raw = b"".join([chunk async for chunk in response.aiter_raw()])
  return response, raw


def assert_script_headers(response: httpx.Response) -> None:
  assert response.headers["content-type"] == "text/javascript; charset=utf-8"
  assert response.headers["cache-control"] == "public, max-age=86400"
  assert response.headers["vary"] == "Accept-Encoding"
  assert "set-cookie" not in response.headers


# With a script file


@with_script
async def test_served_script_is_gzipped_for_a_client_that_accepts_gzip(
  client: httpx.AsyncClient,
) -> None:
  response, raw = await fetch(client, "GET", "gzip, br")
  assert response.status_code == 200
  assert response.headers["content-encoding"] == "gzip"
  assert gzip.decompress(raw) == SCRIPT
  assert_script_headers(response)


@with_script
async def test_served_script_is_plain_for_identity(client: httpx.AsyncClient) -> None:
  response, raw = await fetch(client, "GET", "identity")
  assert response.status_code == 200
  assert "content-encoding" not in response.headers
  assert raw == SCRIPT
  assert_script_headers(response)


@with_script
@pytest.mark.parametrize("accept_encoding", ["gzip, br", "identity"])
async def test_served_script_answers_head_with_the_get_headers(
  client: httpx.AsyncClient, accept_encoding: str
) -> None:
  get, _ = await fetch(client, "GET", accept_encoding)
  head, _ = await fetch(client, "HEAD", accept_encoding)
  assert head.status_code == 200
  assert_script_headers(head)
  assert head.headers.get("content-encoding") == get.headers.get("content-encoding")


# Without a script file


async def test_missing_script_is_logged_at_startup_and_ingest_starts(
  ingest_app: FastAPI, tmp_path: Path
) -> None:
  with capture_logs() as logs:
    async with serve(ingest_app):
      pass
  missing = [log for log in logs if log["event"] == "tracker_missing"]
  assert [(log["log_level"], str(log["path"])) for log in missing] == [
    ("warning", str(tmp_path / "p.js"))
  ]
  assert [log["event"] for log in logs if log["event"] == "startup_complete"] == [
    "startup_complete"
  ]


async def test_missing_script_gives_404_not_found(client: httpx.AsyncClient) -> None:
  response = await client.get("/js/p.js")
  assert response.status_code == 404
  assert response.json()["error"]["code"] == "not_found"


async def test_missing_script_still_lets_events_in(
  client: httpx.AsyncClient, redis_db: Redis, db_session: AsyncSession, refresh: Refresh
) -> None:
  site = await make_site(db_session)
  await refresh()
  # The five keys the tracker always sends
  body: dict[str, object] = {
    "n": "pageview",
    "d": site.domain,
    "u": f"https://{site.domain}/",
    "r": None,
    "p": {},
  }
  response = await client.post(
    "/api/event",
    content=json.dumps(body).encode(),
    headers={"Content-Type": "text/plain", "User-Agent": "MieruTest/1.0"},
  )
  assert response.status_code == 202
  assert "x-mieru-dropped" not in response.headers
  assert len(await stream_fields(redis_db)) == 1
