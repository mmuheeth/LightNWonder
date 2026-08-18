"""Follow a file another process is appending to.

Written for the OLED panel service's log, which is how a button press is proven
to have landed, but there is nothing panel-specific here: it is a read cursor
over a growing file, plus a poll-until-it-appears wait.

Two properties matter to callers:

**Only what arrived after the cursor is read.** A press captures the size first
and reads the delta, so an identical line already in the log cannot confirm the
next press.

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
from pathlib import Path

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
