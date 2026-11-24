"""The stream entry, written by the real ingest code and read by the real writer code."""

import json
from datetime import UTC, datetime
from uuid import UUID

from mieru.ingest.pipeline import Accepted, parse_event, process
from mieru.ingest.salts import SaltCache
from mieru.ingest.site_cache import CachedSite, SiteCache
from mieru.ingest.visitor import visitor_id
from mieru.writer.batch import build_batch
from mieru.writer.enrich import EventRow

ORG_ID = UUID("0b9f4f9e-8d1c-4a55-9f0a-6f4f2f1b2c3d")
NOW = datetime(2026, 11, 21, 12, 30, 5, 250_000, tzinfo=UTC)
SALT = bytes(range(16))
IP = "203.0.113.77"
UA = "MieruPrivacyProbe/1.0"


def test_an_entry_from_ingest_becomes_the_expected_row() -> None:
  sites = SiteCache()
  sites.replace([CachedSite(id=7, org_id=ORG_ID, domain="example.com", status="active")])
  salts = SaltCache()
  salts.replace({NOW.date(): SALT})
  body = {
    "n": "signup",
    "d": "www.example.com",
    "u": "https://example.com/pricing?utm_source=hn",
    "r": "https://news.ycombinator.com/",
    "p": {"plan": "pro"},
  }
  result = process(
    parse_event(json.dumps(body).encode()),
    sites=sites,
    salts=salts,
    ip=IP,
    user_agent=UA,
    now=NOW,
  )
  assert isinstance(result, Accepted)

  batch = build_batch([("1795264205250-0", result.entry.to_fields())])

  assert batch.invalid == ()
  assert batch.rows == (
    EventRow(
      site_id=7,
      timestamp=NOW,
      name="signup",
      visitor_id=visitor_id(SALT, "example.com", IP, UA),
      session_id=0,
      session_start=NOW,
      hostname="example.com",
      pathname="/pricing",
      source="",
      referrer="https://news.ycombinator.com/",
      utm_source="",
      utm_medium="",
      utm_campaign="",
      country="ZZ",
      device="",
      browser="",
      os="",
      props={"plan": "pro"},
    ),
  )
