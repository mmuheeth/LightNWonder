"""Payline check settings: default bet set, match threshold, overlay sizing.

The threshold lives here rather than in the per-game ``paylines`` block because
it's tuned by trying it against a frame, not something that ships with the game.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings

__all__ = ["PaylineSettings"]


class PaylineSettings(BaseSettings):
    """How a payline is decided to have paid, and how the result is drawn."""

    # Cosine similarity two tiles must reach to count as the same symbol.
    # Pixel channels are non-negative, so unrelated symbols already score
    # 0.6-0.9; same-symbol crops score 0.96+. Tune against a still frame, not
    # by intuition -- "0.7 means similar" is much too low here.
    PAYLINE_MATCH_THRESHOLD: float = Field(default=0.85, ge=-1.0, le=1.0)

    # Bet config to check when a request names none; empty = numerically
    # smallest the game declares (the five-line set for every game so far).
    PAYLINE_DEFAULT_SET: str = ""

    # Width the reels crop is enlarged to before drawing lines, so a stroke
    # thick enough to see doesn't cover the symbol it's pointing at.
    PAYLINE_OVERLAY_MIN_WIDTH: int = Field(default=960, ge=1)
