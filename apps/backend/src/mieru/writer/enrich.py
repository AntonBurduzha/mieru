from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from mieru.ingest.stream import StreamEntry

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class EventRow(BaseModel):
  """One row of the ClickHouse events table: the fields are its columns, in table order."""

  model_config = ConfigDict(frozen=True)

  site_id: int
  timestamp: datetime
  name: str
  visitor_id: int
  session_id: int
  session_start: datetime
  hostname: str
  pathname: str
  source: str
  referrer: str
  utm_source: str
  utm_medium: str
  utm_campaign: str
  country: str
  device: str
  browser: str
  os: str
  props: dict[str, str]


def to_row(entry: StreamEntry) -> EventRow:
  """The events row for one stream entry. The user agent and the org id stay behind."""
  # Whole milliseconds, added as an integer: no float rounding
  timestamp = EPOCH + timedelta(milliseconds=entry.ts)
  hostname, pathname = split_url(entry.u)
  return EventRow(
    site_id=entry.site_id,
    timestamp=timestamp,
    name=entry.n,
    visitor_id=entry.vid,
    # Sessions come with T14; until then no event belongs to one
    session_id=0,
    session_start=timestamp,
    hostname=hostname,
    pathname=pathname,
    # Source, UTM tags, device, browser and OS come with T13
    source="",
    referrer=entry.r,
    utm_source="",
    utm_medium="",
    utm_campaign="",
    country=entry.cc,
    device="",
    browser="",
    os="",
    props=entry.p,
  )


def split_url(url: str) -> tuple[str, str]:
  """The host (lowercase, no port) and the path (no query or fragment) of a URL."""
  try:
    parsed = urlsplit(url)
    if parsed:
      return (parsed.hostname or "").lower(), parsed.path or "/"
  except ValueError:
    return "", "/"
