"""The tracker in memory: the file, its gzipped copy, and the Accept-Encoding check."""

import gzip
from datetime import UTC, datetime
from pathlib import Path

import pytest
import time_machine
from hypothesis import given, settings
from hypothesis import strategies as st
from mieru.ingest.tracker import accepts_gzip, load_tracker

# Not the real tracker: any script will do, and a repeated one compresses well
SCRIPT = b'(()=>{const e="mieru";' + b"console.log(e);" * 40 + b"})();\n"


def write_script(tmp_path: Path) -> Path:
  path = tmp_path / "p.js"
  path.write_bytes(SCRIPT)
  return path


def coding_with_q(coding: str, q: str) -> str:
  return f"{coding};q={q}"


# Header-shaped values, so hypothesis reaches the q parsing, and plain text for the rest
codings = st.sampled_from(["gzip", "GZIP", "*", "br", "identity", ""]) | st.text(max_size=10)
q_values = st.sampled_from(["0", "1", "0.5", "abc", "", "nan", "-1", "1e999"]) | st.text(max_size=8)
items = st.builds(coding_with_q, codings, q_values) | codings
headers = st.lists(items, max_size=5).map(", ".join) | st.text(max_size=100)


# accepts_gzip


@pytest.mark.parametrize("header", ["gzip", "GZIP", "deflate, gzip;q=0.5", "*"])
def test_gzip_is_accepted_when_listed_with_a_positive_q(header: str) -> None:
  assert accepts_gzip(header) is True


@pytest.mark.parametrize(
  "header",
  [
    pytest.param("", id="empty"),
    "identity",
    "br",
    "gzip;q=0",
    "gzip;q=abc",
    "*;q=0",
  ],
)
def test_gzip_is_refused_when_not_listed_or_at_q_0(header: str) -> None:
  assert accepts_gzip(header) is False


@settings(derandomize=True)
@given(header=headers)
def test_accepts_gzip_never_raises(header: str) -> None:
  assert isinstance(accepts_gzip(header), bool)


# load_tracker


def test_load_tracker_keeps_the_file_and_a_gzipped_copy(tmp_path: Path) -> None:
  tracker = load_tracker(write_script(tmp_path))
  assert tracker is not None
  assert tracker.body == SCRIPT
  assert gzip.decompress(tracker.gzipped) == SCRIPT


def test_load_tracker_compresses_at_level_9_with_no_timestamp(tmp_path: Path) -> None:
  tracker = load_tracker(write_script(tmp_path))
  assert tracker is not None
  assert tracker.gzipped == gzip.compress(SCRIPT, compresslevel=9, mtime=0)


def test_load_tracker_gives_the_same_gzipped_bytes_on_a_second_load(tmp_path: Path) -> None:
  path = write_script(tmp_path)
  # gzip writes the current time into its header unless told not to: load on two days
  with time_machine.travel(datetime(2026, 11, 26, 12, 0, tzinfo=UTC), tick=False):
    first = load_tracker(path)
  with time_machine.travel(datetime(2026, 11, 27, 12, 0, tzinfo=UTC), tick=False):
    second = load_tracker(path)
  assert first is not None
  assert second is not None
  assert first.gzipped == second.gzipped


def test_load_tracker_gives_none_for_a_missing_file(tmp_path: Path) -> None:
  assert load_tracker(tmp_path / "p.js") is None
