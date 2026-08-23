"""Frame letterbox-detection settings, used by :mod:`app.utils.letterbox` to
find the content box (the part of a frame the game fills) that ROI, OCR and
the reel grid resolve regions against.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings

__all__ = ["FrameSettings"]


class FrameSettings(BaseSettings):
    """How a region is aligned to the game inside a captured frame."""

    # Off resolves every region against the whole canvas (pre-letterbox behavior).
    FRAME_LETTERBOX_TRIM: bool = True

    # Luminance a pixel must exceed to count as game rather than bar.
    FRAME_LETTERBOX_THRESHOLD: int = Field(default=8, ge=0, le=255)

    # Smallest believable content box per axis, as a fraction of the frame.
    # Anything smaller is read as a dark/fade frame and the whole frame is used.
    FRAME_LETTERBOX_MIN_FRACTION: float = Field(default=0.25, gt=0.0, le=1.0)
