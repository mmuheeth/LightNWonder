"""Reading a log backwards for the last thing it said.

The interesting cases are all about the window boundary: the answer sitting in
the newest bytes is the easy path, and everything that can go wrong -- a line
straddling two windows, an older match winning over a newer one, a match older
than the scan limit -- only shows up when the window is small enough to force
several reads. So most tests here set ``window_bytes`` to something absurd.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.utils.log_search import last_match

PATTERN = re.compile(r"paytable\[(?P<paytable>[^\]]+)\]")


def write(path: Path, lines: list[str], *, newline_at_end: bool = True) -> Path:
    """Write a log, optionally without the trailing newline a live log lacks.

    ``newline=""`` so the file is byte-for-byte what it says here: Windows would
    otherwise translate every ``\\n`` to ``\\r\\n`` and shift every offset an
    assertion below counts. CRLF itself is covered by its own test.
    """
    text = "\n".join(lines)
    path.write_text(
        text + "\n" if newline_at_end else text, encoding="utf-8", newline=""
    )
    return path


def test_finds_the_newest_match_not_the_first(tmp_path: Path) -> None:
    """The whole point: an older line naming another paytable does not win."""
    log = write(
        tmp_path / "game.log",
        ["paytable[OLD]", "something else", "paytable[NEW]", "trailing noise"],
    )

    found = last_match(log, PATTERN)

    assert found is not None
    assert found.match.group("paytable") == "NEW"
    assert found.line == "paytable[NEW]"


def test_reads_a_line_that_straddles_two_windows(tmp_path: Path) -> None:
    """A window boundary must not cut a line in half and hide it.

    The match is deliberately in the *first* line of the file, so the search has
    to walk all the way back and stitch a fragment onto an earlier read.
    """
    log = write(tmp_path / "game.log", ["paytable[FIRST]"] + ["x" * 40] * 20)

    found = last_match(log, PATTERN, window_bytes=7)

    assert found is not None
    assert found.match.group("paytable") == "FIRST"


def test_the_newest_match_still_wins_across_windows(tmp_path: Path) -> None:
    """Walking backwards a window at a time must not reorder the file."""
    log = write(
        tmp_path / "game.log",
        ["paytable[OLD]"] + ["filler line"] * 30 + ["paytable[NEW]"] + ["tail"] * 30,
    )

    found = last_match(log, PATTERN, window_bytes=16)

    assert found is not None
    assert found.match.group("paytable") == "NEW"


def test_reports_where_the_line_starts(tmp_path: Path) -> None:
    """The offset names the line's own first byte, whatever the window size."""
    log = write(tmp_path / "game.log", ["first", "paytable[HERE]", "last"])
    expected = len("first\n")

    for window in (4, 9, 1024):
        found = last_match(log, PATTERN, window_bytes=window)
        assert found is not None, window
        assert found.offset == expected, window
        raw = log.read_bytes()
        assert raw[found.offset :].startswith(b"paytable[HERE]"), window


def test_reads_a_final_line_with_no_newline(tmp_path: Path) -> None:
    """A live log's last line has not been terminated yet."""
    log = write(
        tmp_path / "game.log", ["noise", "paytable[LAST]"], newline_at_end=False
    )

    found = last_match(log, PATTERN)

    assert found is not None
    assert found.match.group("paytable") == "LAST"


def test_stops_at_the_scan_limit(tmp_path: Path) -> None:
    """``max_bytes`` bounds the read, so an ancient match is simply not found.

    Not an error: the caller asked to look at the recent past, and a match
    outside it is not evidence about the present.
    """
    log = write(tmp_path / "game.log", ["paytable[ANCIENT]"] + ["x" * 50] * 40)

    assert last_match(log, PATTERN, max_bytes=100) is None
    assert last_match(log, PATTERN, max_bytes=None) is not None


def test_a_match_inside_the_limit_is_found(tmp_path: Path) -> None:
    """The limit only excludes what is genuinely older than it."""
    log = write(tmp_path / "game.log", ["x" * 500, "paytable[RECENT]", "tail"])

    found = last_match(log, PATTERN, max_bytes=64)

    assert found is not None
    assert found.match.group("paytable") == "RECENT"


def test_lines_come_back_stripped(tmp_path: Path) -> None:
    """A log written with CRLF must not leave a carriage return on the line."""
    (tmp_path / "game.log").write_bytes(b"noise\r\npaytable[CRLF]\r\ntail\r\n")

    found = last_match(tmp_path / "game.log", PATTERN)

    assert found is not None
    assert found.line == "paytable[CRLF]"


def test_an_odd_byte_does_not_hide_the_line(tmp_path: Path) -> None:
    """Decoding is defensive, like :class:`app.utils.log_tail.LogTail`'s."""
    (tmp_path / "game.log").write_bytes(b"\xff\xfe garbage\npaytable[OK]\n")

    found = last_match(tmp_path / "game.log", PATTERN)

    assert found is not None
    assert found.match.group("paytable") == "OK"


@pytest.mark.parametrize(
    ("name", "contents"),
    [("missing.log", None), ("empty.log", "")],
    ids=["absent", "empty"],
)
def test_an_unreadable_log_has_no_last_line(
    tmp_path: Path, name: str, contents: str | None
) -> None:
    """Reads never raise -- there is simply no answer."""
    log = tmp_path / name
    if contents is not None:
        log.write_text(contents, encoding="utf-8")

    assert last_match(log, PATTERN) is None


def test_no_match_in_a_populated_log_is_none(tmp_path: Path) -> None:
    """A log that never said it is not an error either."""
    log = write(tmp_path / "game.log", ["nothing", "of", "interest"])

    assert last_match(log, PATTERN) is None
