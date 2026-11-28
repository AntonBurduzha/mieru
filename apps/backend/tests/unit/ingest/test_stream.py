import uuid
from datetime import UTC, datetime, timedelta

from mieru.ingest import stream
from mieru.ingest.stream import StreamEntry


def entry(**overrides: object) -> StreamEntry:
  values: dict[str, object] = {
    "site_id": 7,
    "org_id": uuid.UUID("2f1e4c4a-9f55-4c3e-8a39-0f3b1d6a2b10"),
    "ts": 1_763_121_600_000,
    "n": "signup",
    "u": "https://example.com/pricing",
    "r": "",
    "ua": "Firefox",
    "vid": 2**64 - 1,
    "pvid": 42,
    "cc": "ZZ",
    "p": {"plan": "pro", "city": "Київ"},
  }
  return StreamEntry.model_validate(values | overrides)


def test_every_field_is_a_string_in_redis() -> None:
  assert entry().to_fields() == {
    "v": "1",
    "site_id": "7",
    "org_id": "2f1e4c4a-9f55-4c3e-8a39-0f3b1d6a2b10",
    "ts": "1763121600000",
    "n": "signup",
    "u": "https://example.com/pricing",
    "r": "",
    "ua": "Firefox",
    "vid": "18446744073709551615",
    "pvid": "42",
    "cc": "ZZ",
    "p": '{"plan":"pro","city":"Київ"}',
  }


def test_a_missing_yesterday_id_is_an_empty_string() -> None:
  assert entry(pvid=None).to_fields()["pvid"] == ""


def test_from_fields_reads_back_what_to_fields_wrote() -> None:
  for original in (entry(), entry(pvid=None, p={})):
    assert StreamEntry.from_fields(original.to_fields()) == original


def test_rate_key_is_the_site_and_the_epoch_minute() -> None:
  noon = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
  assert stream.rate_key(7, noon) == "rl:7:29910960"
  # The window is the UTC minute on the clock, not 60 s from the first event
  assert stream.rate_key(7, noon + timedelta(seconds=59)) == "rl:7:29910960"
  assert stream.rate_key(7, noon + timedelta(minutes=1)) == "rl:7:29910961"
