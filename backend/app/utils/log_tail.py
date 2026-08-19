"""Follow a file another process is appending to.

Two layers, so a caller takes only what it needs:

:class:`LogTail` is a single read -- hand it a cursor, get back what arrived
after it. That suits proving one thing happened: a button press captures the
size first and reads only the delta, so an identical line already in the log
cannot confirm the next press.

:class:`LogFollower` is "keep reading" -- it holds the cursor itself, and
:meth:`LogFollower.follow` is the poll loop around it. That suits reacting to a
log as it is written, which is what event capture does.

Written for the OLED panel service's log and for the games' own logs, but there
is nothing specific to either here: this is a byte cursor over a growing file.
What the lines *mean* belongs to :mod:`app.utils.panel_log` and
:mod:`app.utils.game_log`.

Two properties matter to callers:

**Only what arrived after the cursor is read.** Nothing re-reads what it has
already seen, so no line is ever handled twice.

**A shrinking file is a rotation, not an error.** The panel service caps its logs
around 20 MB and starts over; the cursor restarts from the beginning of the new
file rather than waiting forever past the end of a file that no longer exists.

Reads never raise: a log that is absent, locked or unreadable yields nothing,
which callers already handle as "no evidence yet". Whether that is a failure is a
question for the service, not for a file reader.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_POLL_SECONDS",
    "LogFollower",
    "LogTail",
]

# A single read is capped in case something else wrote a burst in between. The
# whole file is several megabytes; the delta being watched for is one line.
DEFAULT_MAX_BYTES = 256 * 1024

DEFAULT_POLL_SECONDS = 0.05


class LogTail:
    """A rotation-aware read cursor over one append-only file."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.poll_seconds = poll_seconds

    def exists(self) -> bool:
        """Whether the file is there to be read at all."""
        return self.path.exists()

    def offset(self) -> int:
        """Current size, to be handed back to :meth:`read_since`. 0 when absent."""
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def read_since(self, offset: int) -> tuple[str, int]:
        """Read whatever was appended since ``offset``.

        Returns the new text and the cursor to pass in next time. A file smaller
        than the cursor has rotated, so reading restarts from its beginning.
        """
        try:
            size = self.path.stat().st_size
        except OSError:
            return "", offset
        if size < offset:
            offset = 0
        if size == offset:
            return "", offset
        try:
            with self.path.open("rb") as handle:
                handle.seek(offset)
                raw = handle.read(self.max_bytes)
        except OSError:
            return "", offset
        # These logs are plain ASCII; decode defensively so one odd byte cannot
        # break whatever is being confirmed.
        return raw.decode("utf-8", errors="replace"), offset + len(raw)

    async def wait_for(
        self, pattern: re.Pattern[str], *, offset: int, timeout: float
    ) -> str | None:
        """Wait for a line matching ``pattern`` to appear after ``offset``.

        Returns the matching line stripped of surrounding whitespace, or ``None``
        once ``timeout`` elapses. The deadline is checked after a read, so a line
        that arrives within the window is never missed by a hair.
        """
        deadline = time.monotonic() + timeout
        cursor = offset
        while True:
            chunk, cursor = self.read_since(cursor)
            for line in chunk.splitlines():
                if pattern.search(line):
                    return line.strip()
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(self.poll_seconds)


class LogFollower:
    """A cursor that remembers where it got to, for following a live log.

    :class:`LogTail` is one read; this is the "keep reading" on top of it, which
    is what anything reacting to a log as it is written actually wants. It holds
    the cursor, so :meth:`new_lines` hands back only what is new and
    :meth:`follow` is the poll loop around that.

    Following starts at the *end* of the file. A follower cares about what
    happens next, not about the history it was started after -- a run should not
    open with a screenshot of every spin since the game launched. Set
    :attr:`cursor` to 0 before the first read to take the file from its start.
    """

    def __init__(
        self, path: Path, *, poll_seconds: float = DEFAULT_POLL_SECONDS
    ) -> None:
        self.path = path
        self.poll_seconds = poll_seconds
        self._tail = LogTail(path)
        self.cursor = self._tail.offset()

    def new_lines(self) -> list[str]:
        """Every line appended since the last call, and advance the cursor.

        Never raises, for the reason the module docstring gives: an unreadable
        log is simply nothing new.
        """
        chunk, self.cursor = self._tail.read_since(self.cursor)
        return chunk.splitlines()

    async def follow(self, handle: Callable[[str], Awaitable[None]]) -> None:
        """Hand every appended line to ``handle`` until cancelled.

        Never returns on its own -- the caller owns the task and ends it by
        cancelling. ``handle`` owns its own failures: an exception raised out of
        it ends the loop, so a caller that must survive one catches it there.
        """
        while True:
            for line in self.new_lines():
                await handle(line)
            await asyncio.sleep(self.poll_seconds)
