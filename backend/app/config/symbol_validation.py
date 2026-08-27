"""Symbol validation settings: where the reference symbol pictures live, how
much of one comparison may be read, and how hard the padding around a source
picture is trimmed.

The trim numbers are this feature's own rather than ``FRAME_LETTERBOX_*``: those
describe an OBS frame, where nothing above the threshold means "a fade to black,
do not trim" and a small box is refused. A source symbol is artwork on padding,
so a *small* content box is the normal case and the floor is correspondingly low.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

from app.config.ideck import PACKAGE_ROOT

__all__ = ["BACKEND_ROOT", "REPO_ROOT", "SymbolValidationSettings"]

# The directory `python -m app` is run from, and the repository above it. Both
# are search roots for a relative path typed into the panel, so that
# `backend/assets/...` and `assets/...` name the same file.
BACKEND_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = BACKEND_ROOT.parent


class SymbolValidationSettings(BaseSettings):
    """How a candidate tile is compared against a folder of symbol pictures."""

    # Reference symbols, one folder per symbol code. Anchored to the backend
    # directory (they ship with the code), not the working directory. Only used
    # when a request names no folder of its own.
    SYMBOL_VALIDATION_SOURCE_DIR: Path = (
        BACKEND_ROOT / "assets" / "FortuneOx" / "Symbols"
    )

    # Ceiling on one comparison. Exceeded, the request fails rather than being
    # quietly cut short: a truncated sweep looks exactly like a complete one.
    SYMBOL_VALIDATION_MAX_SOURCES: int = Field(default=1000, ge=1)

    # Luminance a pixel must clear to count as artwork rather than padding.
    # Symbol assets pad with transparent black, which flattens to 0.
    SYMBOL_VALIDATION_TRIM_THRESHOLD: int = Field(default=8, ge=0, le=255)

    # Smallest share of a source picture, per axis, its trimmed box may be --
    # anything smaller is left untrimmed. Low, because a symbol occupying a
    # tenth of its own canvas is normal here; the floor only catches a picture
    # with nothing in it at all.
    SYMBOL_VALIDATION_TRIM_MIN_FRACTION: float = Field(default=0.02, ge=0.0, le=1.0)
