"""Paytable lookup settings: how far back to read the log for the loaded paytable id,
and how much of a reel strip to send."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings

__all__ = ["PaytableSettings"]


class PaytableSettings(BaseSettings):
    """Reading the loaded paytable id out of a running game's log."""

    # How far back from the end of the game log to look for the last
    # `paytableId[...]` line. The game writes one on every denomination change,
    # so the newest is normally in the final few kilobytes; a session that has
    # not changed denomination in a long while is what the rest of this is for.
    # 0 scans the whole file.
    PAYTABLE_LOG_SCAN_BYTES: int = Field(default=8 * 1024 * 1024, ge=0)

    # Cap on the stops sent back per reel strip. A strip is 100-200 long and
    # every strip of every set travels together, so this is a backstop against
    # a pathological file, not a page size -- a truncated strip is reported as
    # truncated rather than silently shortened.
    PAYTABLE_MAX_STRIP_STOPS: int = Field(default=1000, ge=1)
