"""Payline check settings.

Which of a game's bet configurations is checked, how alike two tiles have to be
to count as the same symbol, and how large the annotated picture comes back.

**The threshold is deployment configuration rather than per-game data**, which is
why it is here and not in the ``paylines`` block. That block is keyed by bet
configuration -- ``"5"``, ``"20"``, ``"40"`` -- so a setting written beside those
keys would be indistinguishable from a set named after a number, and the number
that actually wants tuning is one a person finds by trying it against a frame
rather than one that ships with the game.

The environment variable names stay flat (``PAYLINE_MATCH_THRESHOLD`` and so
on). :class:`app.config.runtime.Settings` inherits this model, so callers keep
the existing ``settings.PAYLINE_*`` API.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings

__all__ = ["PaylineSettings"]


class PaylineSettings(BaseSettings):
    """How a payline is decided to have paid, and how the result is drawn."""

    # Cosine similarity two tiles must reach to be read as the same symbol.
    #
    # Pixel channels are non-negative, so this measure does not start at zero for
    # unrelated pictures: two different symbols sharing one reel background score
    # around 0.6 to 0.9, and two crops of the *same* symbol -- which differ only
    # by the glow and the slight scaling a game animates them with -- score above
    # 0.96. The gap between those clusters is wide, but it is much higher up the
    # range than "0.7 means similar" suggests, so tune this against a frame that
    # does not move rather than by intuition.
    PAYLINE_MATCH_THRESHOLD: float = Field(default=0.85, ge=-1.0, le=1.0)

    # Bet configuration to check when a request names none. Empty means the
    # numerically smallest one the game declares, which is the five-line set for
    # every game shipped so far -- and the one to look at first, since its lines
    # are the straight rows and the two obvious diagonals.
    PAYLINE_DEFAULT_SET: str = ""

    # Width the reels crop is enlarged to before the lines are drawn on it. A
    # 1280x720 capture leaves reels around 455 pixels wide, on which a stroke
    # thick enough to see covers the symbol it is pointing at.
    PAYLINE_OVERLAY_MIN_WIDTH: int = Field(default=960, ge=1)
