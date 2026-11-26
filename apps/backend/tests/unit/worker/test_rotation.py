from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from mieru.worker.rotation import next_hour

ONE_HOUR = timedelta(hours=1)


def test_a_second_before_the_hour_gives_that_hour() -> None:
  now = datetime(2026, 11, 14, 22, 59, 59, tzinfo=UTC)
  assert next_hour(now) == datetime(2026, 11, 14, 23, 0, tzinfo=UTC)


def test_exactly_on_the_hour_gives_the_next_hour() -> None:
  now = datetime(2026, 11, 14, 23, 0, 0, tzinfo=UTC)
  assert next_hour(now) == datetime(2026, 11, 15, 0, 0, tzinfo=UTC)


def test_the_last_microsecond_of_the_day_gives_midnight() -> None:
  now = datetime(2026, 11, 14, 23, 59, 59, 999_999, tzinfo=UTC)
  assert next_hour(now) == datetime(2026, 11, 15, 0, 0, tzinfo=UTC)


def test_new_years_eve_gives_the_first_of_january() -> None:
  now = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
  assert next_hour(now) == datetime(2027, 1, 1, 0, 0, tzinfo=UTC)


# hypothesis takes naive bounds and makes each value aware through `timezones`. The last hour
# of year 9999 is left out, so the hour after any value still exists
LATEST = datetime.max - ONE_HOUR  # noqa: DTZ901


@settings(derandomize=True)
@given(now=st.datetimes(max_value=LATEST, timezones=st.just(UTC)))
def test_the_next_hour_is_utc_on_the_hour_and_at_most_an_hour_away(now: datetime) -> None:
  result = next_hour(now)
  assert result.tzinfo is UTC
  assert (result.minute, result.second, result.microsecond) == (0, 0, 0)
  assert timedelta(0) < result - now <= ONE_HOUR
