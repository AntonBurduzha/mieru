from collections.abc import Mapping
from datetime import date


class SaltCache:
  """The salts of yesterday, today and tomorrow, by UTC day."""

  def __init__(self) -> None:
    self._by_day: dict[date, bytes] = {}

  def get(self, day: date) -> bytes | None:
    return self._by_day.get(day)

  def replace(self, salts: Mapping[date, bytes]) -> None:
    self._by_day = dict(salts)
