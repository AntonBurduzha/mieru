import json
import uuid
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mieru.core.errors import InvalidEvent
from mieru.ingest.pipeline import (
  MAX_BODY_BYTES,
  Accepted,
  Dropped,
  IngestEvent,
  parse_event,
  process,
)
from mieru.ingest.salts import SaltCache
from mieru.ingest.site_cache import CachedSite, SiteCache
from mieru.ingest.visitor import visitor_id

NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
TODAY = NOON.date()
YESTERDAY = TODAY - timedelta(days=1)
SALTS = {YESTERDAY: b"y" * 16, TODAY: b"t" * 16, TODAY + timedelta(days=1): b"n" * 16}
SITE = CachedSite(id=7, org_id=uuid.uuid4(), domain="example.com", status="active")
IP = "203.0.113.7"
UA = "Mozilla/5.0 Firefox/140.0"


def body(**fields: object) -> bytes:
  """A valid page view as JSON bytes; fields replace or add keys, None removes one."""
  event: dict[str, object] = {"n": "pageview", "d": "example.com", "u": "https://example.com/"}
  event |= fields
  return json.dumps({k: v for k, v in event.items() if v is not None}).encode()


def run(
  event: IngestEvent,
  *,
  now: datetime = NOON,
  site: CachedSite = SITE,
  salts: dict[date, bytes] = SALTS,
) -> Accepted | Dropped:
  """process() with caches that hold one site and the given salts."""
  site_cache, salt_cache = SiteCache(), SaltCache()
  site_cache.replace([site])
  salt_cache.replace(salts)
  return process(event, sites=site_cache, salts=salt_cache, ip=IP, user_agent=UA, now=now)


# Parsing and limits


def test_a_full_event_parses() -> None:
  event = parse_event(body(r="https://news.example/", p={"plan": "pro"}, extra="ignored"))
  assert (event.n, event.d, event.u) == ("pageview", "example.com", "https://example.com/")
  assert (event.r, event.p) == ("https://news.example/", {"plan": "pro"})


def test_referrer_and_props_are_optional() -> None:
  event = parse_event(body())
  assert (event.r, event.p) == (None, {})


@pytest.mark.parametrize(
  "raw", [b"", b"not json", b"[]", b"null", b'{"n": "pageview"', b"\xff\xfe"]
)
def test_invalid_json_is_refused(raw: bytes) -> None:
  with pytest.raises(InvalidEvent):
    parse_event(raw)


@pytest.mark.parametrize(
  ("fields", "valid"),
  [
    ({"n": "x" * 120}, True),
    ({"n": "x" * 121}, False),
    ({"n": ""}, False),
    ({"u": "https://e.com/" + "x" * 1986}, True),
    ({"u": "https://e.com/" + "x" * 1987}, False),
    ({"r": "x" * 2000}, True),
    ({"r": "x" * 2001}, False),
    ({"p": {f"k{i}": "v" for i in range(30)}}, True),
    ({"p": {f"k{i}": "v" for i in range(31)}}, False),
    ({"p": {"k" * 64: "v"}}, True),
    ({"p": {"k" * 65: "v"}}, False),
    ({"p": {"": "v"}}, False),
    ({"p": {"k": "v" * 2000}}, True),
    ({"p": {"k": "v" * 2001}}, False),
    ({"p": {"seats": 3}}, False),
    ({"u": "HTTP://EXAMPLE.COM/"}, True),
    ({"u": "file:///home/me/page.html"}, False),
    ({"u": "javascript:alert(1)"}, False),
    ({"n": None}, False),
    ({"d": None}, False),
    ({"u": None}, False),
    ({"n": 5}, False),
  ],
)
def test_invalid_fields_are_refused_at_their_limits(fields: dict[str, object], valid: bool) -> None:
  if valid:
    parse_event(body(**fields))
  else:
    with pytest.raises(InvalidEvent):
      parse_event(body(**fields))


def test_invalid_event_never_carries_the_input() -> None:
  with pytest.raises(InvalidEvent) as caught:
    parse_event(body(u="file:///secret-path"))
  assert "secret-path" not in str(caught.value)
  assert caught.value.__cause__ is None
  assert caught.value.details is None


def test_invalid_body_over_8_kb_is_refused() -> None:
  # An unknown key is ignored, but its bytes still count
  padding = MAX_BODY_BYTES - len(body(pad=""))
  exactly = body(pad="x" * padding)
  assert len(exactly) == MAX_BODY_BYTES
  parse_event(exactly)
  with pytest.raises(InvalidEvent):
    parse_event(body(pad="x" * (padding + 1)))


@settings(derandomize=True)
@given(raw=st.binary(max_size=MAX_BODY_BYTES + 10))
def test_any_body_parses_or_raises_invalid_event(raw: bytes) -> None:
  with suppress(InvalidEvent):
    parse_event(raw)


@settings(derandomize=True)
@given(
  value=st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=50),
    lambda inner: st.lists(inner, max_size=5) | st.dictionaries(st.text(max_size=5), inner),
  )
)
def test_any_body_of_json_parses_or_raises_invalid_event(value: object) -> None:
  with suppress(InvalidEvent):
    parse_event(json.dumps(value).encode())


# Site lookup and status


def test_unknown_site_is_dropped() -> None:
  assert run(parse_event(body(d="nope.example"))) == Dropped("unknown_site")


def test_unknown_site_check_uses_the_normalized_domain() -> None:
  assert isinstance(run(parse_event(body(d="WWW.Example.COM"))), Accepted)


@pytest.mark.parametrize("status", ["blocked", "over_quota"])
def test_status_other_than_active_is_dropped(status: Literal["blocked", "over_quota"]) -> None:
  site = CachedSite(id=7, org_id=SITE.org_id, domain="example.com", status=status)
  assert run(parse_event(body()), site=site) == Dropped(status)


# The stream entry


def test_an_accepted_event_becomes_a_stream_entry() -> None:
  event = parse_event(body(n="signup", r="https://news.example/", p={"plan": "pro"}))
  result = run(event)
  assert isinstance(result, Accepted)
  entry = result.entry
  assert (entry.v, entry.site_id, entry.org_id) == (1, 7, SITE.org_id)
  assert entry.ts == 1_794_657_600_000  # 2026-11-14 12:00 UTC in milliseconds
  assert (entry.n, entry.u, entry.r, entry.p) == (
    "signup",
    "https://example.com/",
    "https://news.example/",
    {"plan": "pro"},
  )
  assert (entry.ua, entry.cc) == (UA, "ZZ")
  assert entry.vid == visitor_id(SALTS[TODAY], "example.com", IP, UA)
  assert entry.pvid == visitor_id(SALTS[YESTERDAY], "example.com", IP, UA)


def test_the_visitor_id_hashes_the_stored_domain_not_the_sent_one() -> None:
  plain, spelled = (run(parse_event(body(d=d))) for d in ("example.com", "www.EXAMPLE.com"))
  assert isinstance(plain, Accepted)
  assert isinstance(spelled, Accepted)
  assert plain.entry.vid == spelled.entry.vid


# Salts around midnight


def test_salt_changes_the_visitor_id_at_midnight() -> None:
  before = datetime(2026, 11, 14, 23, 59, 59, tzinfo=UTC)
  after = datetime(2026, 11, 15, 0, 0, 0, tzinfo=UTC)
  salts = {
    date(2026, 11, 13): b"a" * 16,
    date(2026, 11, 14): b"b" * 16,
    date(2026, 11, 15): b"c" * 16,
  }
  last, first = (run(parse_event(body()), now=now, salts=salts) for now in (before, after))
  assert isinstance(last, Accepted)
  assert isinstance(first, Accepted)
  assert first.entry.vid != last.entry.vid
  # The writer finds the open session under yesterday's id
  assert first.entry.pvid == last.entry.vid


def test_salt_missing_for_yesterday_leaves_pvid_empty() -> None:
  result = run(parse_event(body()), salts={TODAY: SALTS[TODAY]})
  assert isinstance(result, Accepted)
  assert result.entry.pvid is None


def test_salt_missing_for_today_drops_the_event() -> None:
  assert run(parse_event(body()), salts={YESTERDAY: SALTS[YESTERDAY]}) == Dropped("no_salt")


# Bots, spam referrers and the order of the drops

GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
CURL = "curl/8.7.1"
SPAM_REFERRER = "https://0-0.fr/"  # the first domain of the shipped list


def run_as(
  event: IngestEvent,
  *,
  user_agent: str,
  site: CachedSite = SITE,
  salts: dict[date, bytes] = SALTS,
) -> Accepted | Dropped:
  """run() with the user agent the request sent."""
  site_cache, salt_cache = SiteCache(), SaltCache()
  site_cache.replace([site])
  salt_cache.replace(salts)
  return process(event, sites=site_cache, salts=salt_cache, ip=IP, user_agent=user_agent, now=NOON)


@pytest.mark.parametrize("user_agent", ["", GOOGLEBOT, CURL], ids=["empty", "googlebot", "curl"])
def test_bot_user_agent_is_dropped(user_agent: str) -> None:
  assert run_as(parse_event(body()), user_agent=user_agent) == Dropped("bot")


@pytest.mark.parametrize(
  "referrer",
  [SPAM_REFERRER, "https://www.0-0.fr/pricing", "https://blog.0-0.fr/"],
  ids=["listed", "www", "subdomain"],
)
def test_spam_referrer_is_dropped(referrer: str) -> None:
  assert run(parse_event(body(r=referrer))) == Dropped("spam_referrer")


def test_unknown_site_is_dropped_before_bot() -> None:
  assert run_as(parse_event(body(d="nope.example")), user_agent=CURL) == Dropped("unknown_site")


@pytest.mark.parametrize("status", ["blocked", "over_quota"])
def test_site_status_is_dropped_before_bot(status: Literal["blocked", "over_quota"]) -> None:
  site = CachedSite(id=7, org_id=SITE.org_id, domain="example.com", status=status)
  assert run_as(parse_event(body()), user_agent=CURL, site=site) == Dropped(status)


def test_bot_is_dropped_before_spam_referrer() -> None:
  assert run_as(parse_event(body(r=SPAM_REFERRER)), user_agent=CURL) == Dropped("bot")


def test_bot_is_dropped_before_no_salt() -> None:
  no_today = {YESTERDAY: SALTS[YESTERDAY]}
  assert run_as(parse_event(body()), user_agent=CURL, salts=no_today) == Dropped("bot")


def test_spam_referrer_is_dropped_before_no_salt() -> None:
  no_today = {YESTERDAY: SALTS[YESTERDAY]}
  assert run(parse_event(body(r=SPAM_REFERRER)), salts=no_today) == Dropped("spam_referrer")


# The site's rate limit travels with the accepted event


def test_accepted_carries_the_rate_limit_per_min_of_its_site() -> None:
  site = CachedSite(
    id=7, org_id=SITE.org_id, domain="example.com", status="active", rate_limit_per_min=3
  )
  result = run(parse_event(body()), site=site)
  assert isinstance(result, Accepted)
  assert result.rate_limit_per_min == 3


def test_a_site_without_its_own_limit_is_accepted_at_6000_per_minute() -> None:
  result = run(parse_event(body()))
  assert isinstance(result, Accepted)
  assert result.rate_limit_per_min == 6000
