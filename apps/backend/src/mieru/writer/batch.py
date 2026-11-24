from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mieru.ingest.stream import StreamEntry
from mieru.writer.enrich import EventRow, to_row

# A stream id and the entry's fields, as XREADGROUP returns them
RawEntry = tuple[str, Mapping[str, str]]

MAX_BACKOFF_S = 30.0


@dataclass(frozen=True, slots=True)
class Batch:
  """What one read from the stream becomes. Never empty."""

  ids: tuple[str, ...]  # every entry read, valid or not, in stream order
  rows: tuple[EventRow, ...]
  invalid: tuple[tuple[str, str], ...]  # (id, error type) of each entry that didn't parse

  @property
  def token(self) -> str:
    """The insert's dedup token. The same enties always give the same token."""
    return f"{self.ids[0]}:{self.ids[-1]}"


def build_batch(entries: Sequence[RawEntry]) -> Batch:
  """Turn the entries into rows. One that doesn't parse is set aside instead."""
  rows: list[EventRow] = []
  invalid: list[tuple[str, str]] = []
  for entry_id, fields in entries:
    try:
      rows.append(to_row(StreamEntry.from_fields(fields)))
    except Exception as exc:
      # The fields hold the user agent, so only the error's type is kept
      invalid.append((entry_id, type(exc).__name__))
  return Batch(
    ids=tuple(entry_id for entry_id, _fields in entries),
    rows=tuple(rows),
    invalid=tuple(invalid),
  )


def backoff_s(failures: int) -> float:
  """Seconds to wait after this many failures in a row: 1, 2, 4 ... up to 30."""
  return min(MAX_BACKOFF_S, 2.0 ** (failures - 1))
