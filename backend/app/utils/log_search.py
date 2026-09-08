"""Ask a log what it *last* said."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["DEFAULT_WINDOW_BYTES", "LogMatch", "last_match"]

# How much is pulled back per step. Big enough that a recent line is normally
# found in one read, small enough that the usual case doesn't decode megabytes.
DEFAULT_WINDOW_BYTES = 256 * 1024


@dataclass(frozen=True)
class LogMatch:
    """The newest line matching a pattern, and the match that found it."""

    line: str
    """The whole line, stripped. Parse it with :func:`app.utils.game_log.parse_line`."""

    match: re.Match[str]
    """So named groups in the pattern are available without searching twice."""

    offset: int
    """Byte offset the line starts at, for reporting how far back it was."""


def last_match(
    path: Path,
    pattern: re.Pattern[str],
    *,
    window_bytes: int = DEFAULT_WINDOW_BYTES,
    max_bytes: int | None = None,
) -> LogMatch | None:
    """Newest line in ``path`` matching ``pattern``, or ``None``."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None

    floor = 0 if max_bytes is None else max(0, size - max_bytes)
    try:
        with path.open("rb") as handle:
            end = size
            # The head of a window is only half a line unless the window began
            # at the floor, so it is carried back into the next (earlier) read.
            carry = b""
            while end > floor:
                start = max(floor, end - window_bytes)
                handle.seek(start)
                raw = handle.read(end - start) + carry
                lines = raw.split(b"\n")
                carry = lines.pop(0) if start > floor else b""

                # Backwards within the window too: the newest match wins.
                offset = start + len(raw)
                for chunk in reversed(lines):
                    offset -= len(chunk)
                    # These logs are ASCII; decode defensively so one odd byte
                    # cannot hide the line after it.
                    text = chunk.decode("utf-8", errors="replace").strip()
                    found = pattern.search(text)
                    if found is not None:
                        return LogMatch(line=text, match=found, offset=offset)
                    offset -= 1  # the newline this chunk was split on
                end = start
    except OSError:
        return None
    return None
