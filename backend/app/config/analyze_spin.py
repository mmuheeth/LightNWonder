"""Analyze Spin runtime settings: which keys drive one spin, how long each
stage is allowed to take, and where a run's record is kept.

Every timeout here bounds a wait on the *game's own log*, not on a network
call, so they are generous: a spin that never lands should end as a named
failure on one step rather than a request that hangs. The one worth knowing is
``ANALYZE_SPIN_WIN_WAIT_SECONDS`` -- a losing spin is proven only by the win
count-up *not* arriving, so a no-win run pays that wait in full before its
final screenshot.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

__all__ = ["AnalyzeSpinSettings"]


class AnalyzeSpinSettings(BaseSettings):
    """Runtime options for the orchestrated single-spin analysis."""

    # Subdirectory of the capture root holding one folder per run, and the
    # recording subdirectory under the recording root.
    ANALYZE_SPIN_DIR_NAME: str = "analyze-spin"

    # The i-deck key that spins. A layout id from IDECK_PANEL_XML, not a game
    # concept: FortuneOx binds `Rebet` to SpinButtonMsg (see backend/README).
    ANALYZE_SPIN_SPIN_BUTTON: str = "Rebet"

    # The `button_targets` entry clicked to collect a win. Take-win is not on
    # the deck's fourteen keys, so it is a click into the game's own window.
    ANALYZE_SPIN_TAKE_WIN_TARGET: str = "take_win"

    # Whether one run also records a video of itself. Off by default; a
    # request to /start can turn it on for that run alone.
    ANALYZE_SPIN_RECORD: bool = False

    # How often the game log is re-read while waiting for the next event.
    ANALYZE_SPIN_POLL_SECONDS: float = Field(default=0.1, gt=0)

    # A confirmed i-deck press proves the panel saw it, not that the game acted,
    # so the spin is not considered started until the game publishes it.
    ANALYZE_SPIN_SPIN_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0)

    # Reel spin plus every stop delay the theme adds.
    ANALYZE_SPIN_REELS_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0)

    # How long to wait for the win meter to finish counting up before calling
    # the spin a loss. Must exceed the longest count-up the game animates: too
    # short reports a win as a loss, and the cost of too long is only that a
    # losing spin waits it out.
    ANALYZE_SPIN_WIN_WAIT_SECONDS: float = Field(default=10.0, gt=0)

    # Breathing room between the win meter settling and its screenshot.
    ANALYZE_SPIN_WIN_SETTLE_SECONDS: float = Field(default=0.5, ge=0)

    # After OBS is re-pointed at the game's window, before the first screenshot.
    # Re-pointing a window capture makes OBS render nothing for a moment, and a
    # screenshot taken inside it comes back black -- which is not an error
    # anywhere, just an empty frame that quietly invalidates both validations.
    ANALYZE_SPIN_SOURCE_SETTLE_SECONDS: float = Field(default=1.0, ge=0)

    # A screenshot with nothing in it is retried rather than accepted, since
    # every reading taken off a black frame is meaningless rather than dark.
    ANALYZE_SPIN_BLANK_RETRIES: int = Field(default=2, ge=0)
    ANALYZE_SPIN_BLANK_RETRY_SECONDS: float = Field(default=0.75, ge=0)

    # Between a confirmed take-win and the screenshot proving it landed -- the
    # click is proven by the log, the balance moving is an animation.
    ANALYZE_SPIN_COLLECT_SETTLE_SECONDS: float = Field(default=1.5, ge=0)

    # Screenshot width, or null for the OBS canvas's own. Left native by
    # default: the same frames are cropped for OCR and split into tiles, and
    # both lose more to a downscale than the file size is worth.
    ANALYZE_SPIN_SCREENSHOT_WIDTH: int | None = Field(default=None, ge=8, le=4096)

    # How far two meter readings may differ and still be called equal. Cash is
    # drawn to two decimals, so this only absorbs the OCR of the last one.
    ANALYZE_SPIN_METER_TOLERANCE: float = Field(default=0.005, ge=0)

    # Recognised log events kept on a run, so a game that logs continuously
    # cannot grow one run's record without bound.
    ANALYZE_SPIN_MAX_EVENTS: int = Field(default=200, ge=1)

    # Which visible row the game's logged reel stop refers to. `auto` builds all
    # three and keeps whichever agrees best with the similarity already measured
    # off the picture, which is the only evidence there is: the maths files state
    # the stop index and never state the convention, and the wrong one shifts
    # every symbol by a row into a plausible grid of the wrong spin. Pin it once
    # a game's alignment is known and settled.
    ANALYZE_SPIN_REEL_STOP_ANCHOR: Literal["auto", "top", "middle", "bottom"] = "auto"

    @field_validator("ANALYZE_SPIN_SCREENSHOT_WIDTH", mode="before")
    @classmethod
    def _blank_width_is_native(cls, value: object) -> object:
        """Read ``ANALYZE_SPIN_SCREENSHOT_WIDTH=`` as "the canvas's own".

        Without this an empty value in a ``.env`` is an integer parse error, and
        commenting the line out to mean "no override" is the kind of thing that
        gets lost -- the setting reads as optional, so blank has to be a way of
        saying so.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value
