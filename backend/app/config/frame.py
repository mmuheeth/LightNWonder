"""Captured-frame geometry settings.

Every feature that resolves a configured region against a screenshot -- ROI,
OCR and the reel grid -- resolves it against the part of the frame the game
fills rather than against the canvas OBS wrote, so a resized simulator needs no
re-measurement. :mod:`app.utils.letterbox` finds that rectangle; these are the
three numbers it is found with.

The environment variable names stay flat (``FRAME_LETTERBOX_TRIM`` and so on).
:class:`app.config.runtime.Settings` inherits this model, so callers keep the
existing ``settings.FRAME_*`` API.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings

__all__ = ["FrameSettings"]


class FrameSettings(BaseSettings):
    """How a region is aligned to the game inside a captured frame."""

    # Off means every region resolves against the whole canvas, which is what
    # this project did before the content box existed. Kept as a switch because
    # a capture that is already edge to edge gets the same answer either way,
    # and a game that genuinely renders black to its own borders would want it.
    FRAME_LETTERBOX_TRIM: bool = True

    # Luminance a pixel must exceed to count as game rather than bar. OBS pads
    # with pure black and the games' edges come in above 180, so the default
    # sits far from both.
    FRAME_LETTERBOX_THRESHOLD: int = Field(default=8, ge=0, le=255)

    # Smallest content box that will be believed, per axis, as a fraction of the
    # frame. Anything smaller is read as a dark game screen -- a fade, a loading
    # frame -- and the whole frame is used instead.
    FRAME_LETTERBOX_MIN_FRACTION: float = Field(default=0.25, gt=0.0, le=1.0)
