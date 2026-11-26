"""The tracking script that ingest serves at /js/p.js: read once, kept gzipped in memory."""

import gzip
from dataclasses import dataclass
from pathlib import Path

TRACKER_PATH = Path(__file__).parent / "static" / "p.js"

CACHE_CONTROL = "public, max-age=86400"


@dataclass(frozen=True, slots=True)
class Tracker:
  """The script as built, and its gzipped copy."""

  body: bytes
  gzipped: bytes


def load_tracker(path: Path = TRACKER_PATH) -> Tracker | None:
  """Read the script and gzip it once; None when it hasn't been built"""
  try:
    body = path.read_bytes()
  except FileNotFoundError:
    return None
  return Tracker(body=body, gzipped=gzip.compress(body, compresslevel=9, mtime=0))


def accepts_gzip(accept_encoding: str) -> bool:
  """True when an Accept-Encoding header takes gzip: gzip, or else *, with a q above 0.

  gzip listed by name wins over *, so "gzip;q=0, *" refuses it. A q that doesn't parse
  counts as 0: a malformed header gets the plain file, never an error.
  """
  gzip_q: float | None = None
  any_q: float | None = None
  for item in accept_encoding.split(","):
    coding, _, params = item.partition(";")
    coding = coding.strip().lower()
    if coding == "gzip":
      gzip_q = quality(params)
    elif coding == "*":
      any_q = quality(params)
  q = gzip_q if gzip_q is not None else any_q
  # nan > 0 is False, so q=nan refuses too
  return q is not None and q > 0


def quality(params: str) -> float:
  """The q of one Accept-Encoding item: 1 when it has none, 0 when it doesn't parse."""
  for param in params.split(";"):
    name, _, value = param.partition("=")
    if name.strip().lower() == "q":
      try:
        return float(value.strip())
      except ValueError:
        return 0.0
  return 1.0
