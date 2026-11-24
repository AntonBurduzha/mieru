from tests.factories import make_entry

from mieru.writer.batch import backoff_s, build_batch
from mieru.writer.enrich import to_row

SITE_ID = 7


def test_valid_entries_become_rows_in_stream_order() -> None:
  first, second = make_entry(SITE_ID, u="https://a.example/"), make_entry(SITE_ID)
  batch = build_batch([("1-0", first.to_fields()), ("1-1", second.to_fields())])
  assert batch.ids == ("1-0", "1-1")
  assert batch.rows == (to_row(first), to_row(second))
  assert batch.invalid == ()


def test_the_token_names_the_first_and_last_entry() -> None:
  fields = make_entry(SITE_ID).to_fields()
  batch = build_batch([("5-0", fields), ("5-1", fields), ("7-3", fields)])
  assert batch.token == "5-0:7-3"  # noqa: S105 (a dedup token, not a password)


def test_backoff_doubles_up_to_30_seconds() -> None:
  assert [backoff_s(failures) for failures in range(1, 8)] == [1, 2, 4, 8, 16, 30, 30]


def test_an_entry_that_does_not_parse_is_set_aside() -> None:
  good = make_entry(SITE_ID).to_fields()
  batch = build_batch(
    [
      ("1-0", good),
      ("1-1", {}),  # what Redis returns for a pending entry that was deleted
      ("1-2", good | {"p": "{not json"}),
      ("1-3", good | {"site_id": "seven"}),
      ("1-4", good),
    ]
  )
  assert batch.ids == ("1-0", "1-1", "1-2", "1-3", "1-4")
  assert len(batch.rows) == 2
  assert batch.invalid == (
    ("1-1", "KeyError"),
    ("1-2", "JSONDecodeError"),
    ("1-3", "ValidationError"),
  )
