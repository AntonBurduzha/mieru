from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.factories import make_entry

from mieru.writer.enrich import to_row

SITE_ID = 7
AT = datetime(2026, 11, 21, 12, 30, 5, 250_000, tzinfo=UTC)
AT_MS = 1_795_264_205_250  # AT in milliseconds since the epoch
PROBE_UA = "MieruPrivacyProbe/1.0"


def test_the_event_fields_are_copied() -> None:
  entry = make_entry(
    SITE_ID, n="signup", vid=42, r="https://news.ycombinator.com/", cc="DE", p={"plan": "pro"}
  )
  row = to_row(entry)
  assert (row.site_id, row.name, row.visitor_id) == (SITE_ID, "signup", 42)
  assert (row.referrer, row.country, row.props) == (
    "https://news.ycombinator.com/",
    "DE",
    {"plan": "pro"},
  )


def test_the_timestamp_is_the_entry_time_in_utc() -> None:
  row = to_row(make_entry(SITE_ID, ts=AT_MS))
  assert row.timestamp == AT
  assert row.timestamp.tzinfo is UTC


@pytest.mark.parametrize(
  ("url", "hostname", "pathname"),
  [
    ("https://example.com/pricing", "example.com", "/pricing"),
    ("https://Shop.Example.COM:8443/Cart?utm_source=x#top", "shop.example.com", "/Cart"),
    ("https://example.com", "example.com", "/"),
    ("https://example.com?q=1", "example.com", "/"),
    ("http://[::1", "", "/"),
  ],
)
def test_host_and_path_come_from_the_url(url: str, hostname: str, pathname: str) -> None:
  row = to_row(make_entry(SITE_ID, u=url))
  assert (row.hostname, row.pathname) == (hostname, pathname)


def test_no_session_and_no_enrichment_yet() -> None:
  row = to_row(make_entry(SITE_ID, ts=AT_MS))
  assert (row.session_id, row.session_start) == (0, AT)
  enrichment = (row.source, row.utm_source, row.utm_medium, row.utm_campaign)
  assert enrichment == ("", "", "", "")
  assert (row.device, row.browser, row.os) == ("", "", "")


def test_the_row_holds_no_user_agent_and_no_org_id() -> None:
  entry = make_entry(SITE_ID, ua=PROBE_UA)
  dumped = str(to_row(entry).model_dump())
  assert PROBE_UA not in dumped
  assert str(entry.org_id) not in dumped


@settings(derandomize=True)
@given(scheme=st.sampled_from(["http://", "https://"]), rest=st.text(max_size=200))
def test_any_url_that_ingest_accepts_gives_a_row(scheme: str, rest: str) -> None:
  row = to_row(make_entry(SITE_ID, u=scheme + rest))
  assert row.pathname.startswith("/")
  assert row.hostname == row.hostname.lower()
