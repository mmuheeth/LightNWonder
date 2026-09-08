"""Follow a file another process is appending to."""

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
        """Read whatever was appended since ``offset``, returning the new text and the
        next cursor."""
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
        """Wait for a line matching ``pattern`` after ``offset``, returning it
        stripped, or ``None`` once ``timeout`` elapses."""
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
    """A cursor that remembers where it got to, for following a live log."""

    def __init__(
        self, path: Path, *, poll_seconds: float = DEFAULT_POLL_SECONDS
    ) -> None:
        self.path = path
        self.poll_seconds = poll_seconds
        self._tail = LogTail(path)
        self.cursor = self._tail.offset()

    def new_lines(self) -> list[str]:
        """Every line appended since the last call, advancing the cursor.
        Never raises -- an unreadable log is simply nothing new."""
        chunk, self.cursor = self._tail.read_since(self.cursor)
        return chunk.splitlines()

    async def follow(self, handle: Callable[[str], Awaitable[None]]) -> None:
        """Hand every appended line to ``handle`` until cancelled. Never returns
        on its own; an exception out of ``handle`` ends the loop."""
        while True:
            for line in self.new_lines():
                await handle(line)
            await asyncio.sleep(self.poll_seconds)
