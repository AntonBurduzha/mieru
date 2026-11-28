from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, ValidationError

from mieru.core.errors import InvalidEvent
from mieru.ingest.bots import is_bot, is_spam_referrer, spam_domains
from mieru.ingest.salts import SaltCache
from mieru.ingest.site_cache import SiteCache
from mieru.ingest.stream import StreamEntry
from mieru.ingest.visitor import visitor_id

MAX_BODY_BYTES = 8192
UNKNOWN_COUNTRY = "ZZ"

DropReason = Literal[
  "unknown_site",
  "blocked",
  "over_quota",
  "no_salt",
  "bot",
  "spam_referrer",
  "rate_limited",
  "backpressure",
  "redis_unavailable",
]


def _http_url(value: str) -> str:
  if not value.lower().startswith(("http://", "https://")):
    raise ValueError("not an http(s) URL")
  return value


Name = Annotated[str, StringConstraints(min_length=1, max_length=120)]
Domain = Annotated[str, StringConstraints(min_length=1, max_length=253)]
Url = Annotated[str, StringConstraints(min_length=1, max_length=2000), AfterValidator(_http_url)]
Referrer = Annotated[str, StringConstraints(max_length=2000)]
PropKey = Annotated[str, StringConstraints(min_length=1, max_length=64)]
PropValue = Annotated[str, StringConstraints(max_length=2000)]


class IngestEvent(BaseModel):
  """The body the tracker posts. Short keys keep the script small; extra keys are ignored."""

  n: Name
  d: Domain
  u: Url
  r: Referrer | None = None
  p: dict[PropKey, PropValue] = Field(default_factory=dict, max_length=30)


def parse_event(body: bytes) -> IngestEvent:
  """Validate a request body, or raise InvalidEvent."""
  if len(body) > MAX_BODY_BYTES:
    raise InvalidEvent("Malformed event")
  try:
    return IngestEvent.model_validate_json(body)
  except ValidationError:
    raise InvalidEvent("Malformed event") from None


@dataclass(frozen=True, slots=True)
class Accepted:
  """The event was kept. It will be written to Redis, within the site's limit."""

  entry: StreamEntry
  rate_limit_per_min: int


@dataclass(frozen=True, slots=True)
class Dropped:
  reason: DropReason


def process(
  event: IngestEvent,
  *,
  sites: SiteCache,
  salts: SaltCache,
  ip: str,
  user_agent: str,
  now: datetime,
) -> Accepted | Dropped:
  """Decide what happens to a valid event: the stream entry to append, or why it's dropped."""
  site = sites.get(event.d)
  if site is None:
    return Dropped("unknown_site")
  if site.status == "blocked":
    return Dropped("blocked")
  if site.status == "over_quota":
    return Dropped("over_quota")
  # In memory, before any Redis call: a bot flood costs no I/O
  if is_bot(user_agent):
    return Dropped("bot")
  if is_spam_referrer(event.r, spam_domains()):
    return Dropped("spam_referrer")

  today = now.date()
  today_salt = salts.get(today)
  if today_salt is None:
    return Dropped("no_salt")
  yesterday_salt = salts.get(today - timedelta(days=1))
  vid = visitor_id(today_salt, site.domain, ip, user_agent)
  pvid = None if yesterday_salt is None else visitor_id(yesterday_salt, site.domain, ip, user_agent)
  entry = StreamEntry(
    v=1,
    site_id=site.id,
    org_id=site.org_id,
    ts=int(now.timestamp() * 1000),
    n=event.n,
    u=event.u,
    r=event.r or "",
    ua=user_agent,
    vid=vid,
    pvid=pvid,
    cc=UNKNOWN_COUNTRY,
    p=event.p,
  )
  return Accepted(entry, rate_limit_per_min=site.rate_limit_per_min)
