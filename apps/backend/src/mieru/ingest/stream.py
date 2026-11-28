import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT

STREAM = "events"
FORMAT_VERSION = 1
# A rate-limit counter outlives its minute by one more, then Redis deletes it
RATE_KEY_TTL_S = 120


class StreamEntry(BaseModel):
  """One event on its way from ingest to the writer. It never holds the IP."""

  model_config = ConfigDict(frozen=True)

  v: int = FORMAT_VERSION
  site_id: int
  org_id: UUID
  ts: int  # milliseconds since the epoch, from the server's clock
  n: str  # the event name
  u: str  # the URL
  r: str  # the referrer URL
  ua: str  # the user agent string
  vid: int  # the id under today's salt, for visitors across midnight
  pvid: int | None  # the id under yesterday's salt, for sessions across midnight
  cc: str  # the country code
  p: dict[str, str]  # the properties

  def to_fields(self) -> dict[str, str]:
    """The entry as Redis stores it: every value a string."""
    return {
      "v": str(self.v),
      "site_id": str(self.site_id),
      "org_id": str(self.org_id),
      "ts": str(self.ts),
      "n": self.n,
      "u": self.u,
      "r": self.r,
      "ua": self.ua,
      "vid": str(self.vid),
      "pvid": str(self.pvid) if self.pvid is not None else "",
      "cc": self.cc,
      "p": json.dumps(self.p, separators=(",", ":"), ensure_ascii=False),
    }

  @classmethod
  def from_fields(cls, fields: Mapping[str, str]) -> Self:
    return cls.model_validate(
      {**fields, "pvid": fields["pvid"] or None, "p": json.loads(fields["p"])}
    )


def _xadd_fields(entry: StreamEntry) -> dict[FieldT, EncodableT]:
  # xadd is typed for a dict of its own key and value types, so copy into one
  return {key: value for key, value in entry.to_fields().items()}


async def append(redis: Redis, entry: StreamEntry) -> str:
  """Add the entry to the end of the stream and return its id."""
  # No MAXLEN: trimming would delete entries the writer hasn't stored yet
  return str(await redis.xadd(STREAM, _xadd_fields(entry)))


async def append_many(redis: Redis, entries: Sequence[StreamEntry]) -> None:
  """Add the entries in order, in one MULTI: Redis stores all of them or none."""
  async with redis.pipeline(transaction=True) as pipe:
    for entry in entries:
      pipe.xadd(STREAM, _xadd_fields(entry))
    await pipe.execute()


def rate_key(site_id: int, now: datetime) -> str:
  """The site's rate-limit counter for the UTC minute that holds now."""
  return f"rl:{site_id}:{int(now.timestamp()) // 60}"


async def count_event(redis: Redis, site_id: int, now: datetime) -> int:
  """Count one event against the site's minute, and return the count so far."""
  key = rate_key(site_id, now)
  # One round trip and no MULTI: the two needn't be atomic (decision 23).
  # NX sets the expiry only on the first INCR, so the window never slides
  async with redis.pipeline(transaction=False) as pipe:
    pipe.incr(key)
    pipe.expire(key, RATE_KEY_TTL_S, nx=True)
    count, _ = await pipe.execute()
  return int(count)


async def length(redis: Redis) -> int:
  """The number of entries in the stream."""
  return int(await redis.xlen(STREAM))
