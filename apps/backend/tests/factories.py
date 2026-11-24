from collections.abc import Sequence
from itertools import count
from uuid import uuid4

from clickhouse_connect.driver.asyncclient import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from mieru.core import clock
from mieru.ingest.stream import StreamEntry
from mieru.models import Organization, Site
from mieru.repositories.organizations import insert_organization
from mieru.repositories.sites import insert_site

# Unique names, domains and ids within a test run
_sequence = count(1)

# The columns of the ClickHouse events table, in the order of clickhouse/0001_events.sql
EVENT_COLUMNS = (
  "site_id",
  "timestamp",
  "name",
  "visitor_id",
  "session_id",
  "session_start",
  "hostname",
  "pathname",
  "source",
  "referrer",
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "country",
  "device",
  "browser",
  "os",
  "props",
)


async def make_organization(session: AsyncSession, *, name: str | None = None) -> Organization:
  """An organization; the name is unique unless given."""
  return await insert_organization(session, name or f"Organization {next(_sequence)}")


async def make_site(
  session: AsyncSession,
  *,
  organization: Organization | None = None,
  domain: str | None = None,
  timezone: str = "UTC",
) -> Site:
  """A site, in a new organization unless one is given; the domain is unique unless given."""
  if organization is None:
    organization = await make_organization(session)
  domain = domain or f"site-{next(_sequence)}.test"
  return await insert_site(session, organization.id, domain, timezone)


def make_event(site_id: int, **overrides: object) -> dict[str, object]:
  """A page view for the events table, now, in a new session; overrides replace columns."""
  unknown = overrides.keys() - set(EVENT_COLUMNS)
  if unknown:
    raise KeyError(f"not columns of events: {sorted(unknown)}")
  now = clock.now()
  event: dict[str, object] = {
    "site_id": site_id,
    "timestamp": now,
    "name": "pageview",
    "visitor_id": next(_sequence),
    "session_id": next(_sequence),
    "session_start": now,
    "hostname": "example.com",
    "pathname": "/",
    "source": "Direct",
    "referrer": "",
    "utm_source": "",
    "utm_medium": "",
    "utm_campaign": "",
    "country": "ZZ",
    "device": "desktop",
    "browser": "Firefox",
    "os": "Linux",
    "props": {},
  }
  return event | overrides


async def insert_events(client: AsyncClient, events: Sequence[dict[str, object]]) -> None:
  """Insert the events into ClickHouse in one block, as the writer will."""
  rows = [[event[column] for column in EVENT_COLUMNS] for event in events]
  await client.insert("events", rows, column_names=EVENT_COLUMNS)


def make_entry(site_id: int, **overrides: object) -> StreamEntry:
  """A stream entry for a page view, now, by a new visitor; overrides replace fields."""
  fields: dict[str, object] = {
    "site_id": site_id,
    "org_id": uuid4(),
    "ts": int(clock.now().timestamp() * 1000),
    "n": "pageview",
    "u": "https://example.com/",
    "r": "",
    "ua": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
    "vid": next(_sequence),
    "pvid": None,
    "cc": "ZZ",
    "p": {},
  }
  return StreamEntry.model_validate(fields | overrides)
