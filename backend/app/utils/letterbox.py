"""Find the part of a captured frame the game actually fills (the "content box")."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

__all__ = [
    "DEFAULT_MIN_DENSITY",
    "DEFAULT_MIN_FRACTION",
    "DEFAULT_THRESHOLD",
    "ContentBox",
    "content_box",
    "is_blank",
]

# Luminance where a bar stops: OBS pads with pure black, game edges start above 180.
DEFAULT_THRESHOLD = 8

# Smallest content box allowed per axis, as a fraction of the frame -- smaller
# is treated as a dark game screen rather than a small window.
DEFAULT_MIN_FRACTION = 0.25

# Fraction of a column's (or row's) own length that must clear the threshold
# before that column counts toward the box at all. A real window edge is bright
# for a large share of its height; a stray spark, or a coin/cloud decoration
# animating outside the game's own body, lights only a small share of a column
# that is otherwise still bar, and used to drag the box out to wherever the
# furthest one landed. Measured across three dozen real captures of the same
# window: true edges never sit below 18% and the decorations drifting past them
# never reach 15%, so 0.15 is the largest floor that still clears every real
# edge measured while rejecting every decoration measured.
DEFAULT_MIN_DENSITY = 0.15


@dataclass(frozen=True)
class ContentBox:
    """The rectangle of a frame the game fills, in pixels of that frame."""

    left: int
    top: int
    right: int
    bottom: int
    frame_width: int
    frame_height: int

    @classmethod
    def whole(cls, width: int, height: int) -> ContentBox:
        """The frame itself, for a capture with no bars to trim."""
        return cls(
            left=0,
            top=0,
            right=width,
            bottom=height,
            frame_width=width,
            frame_height=height,
        )

    @property
    def box(self) -> tuple[int, int, int, int]:
        """``(left, top, right, bottom)``, in the order Pillow wants it."""
        return self.left, self.top, self.right, self.bottom

    @property
    def width(self) -> int:
        """Content width in pixels."""
        return self.right - self.left

    @property
    def height(self) -> int:
        """Content height in pixels."""
        return self.bottom - self.top

    @property
    def letterboxed(self) -> bool:
        """Whether any of the frame was bars rather than game."""
        return self.box != (0, 0, self.frame_width, self.frame_height)


def content_box(
    image: Image.Image,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    min_fraction: float = DEFAULT_MIN_FRACTION,
    min_density: float = DEFAULT_MIN_DENSITY,
) -> ContentBox:
    """The part of ``image`` that is game rather than letterbox."""
    whole = ContentBox.whole(image.width, image.height)
    if image.width <= 0 or image.height <= 0:
        return whole

    limit = min(max(int(threshold), 0), 255)
    mask = np.asarray(image.convert("L")) > limit

    raw = mask.nonzero()
    if raw[0].size == 0:
        # Nothing above the threshold: a black frame, not a zero-size window.
        return whole
    raw_top, raw_bottom = int(raw[0].min()), int(raw[0].max()) + 1
    raw_left, raw_right = int(raw[1].min()), int(raw[1].max()) + 1

    # A plain `getbbox` is the box of every lit pixel, so a handful of stray
    # sparks or a coin-burst particle drifting past the game's real edge -- lit,
    # but nowhere near enough of its column or row to be the window -- used to
    # drag that edge out to wherever the furthest one landed. A real edge is
    # bright for its whole height (a vertical bar) or width (a horizontal one),
    # so measuring each column's density *within the raw box's own row span*
    # (not the whole frame, which a tall-but-narrow window would fail even at
    # 100% lit) and each row's within the raw box's column span is what tells
    # the two apart: a spark lights a column for a pixel or two out of that
    # whole span, a real edge lights it throughout. A frame that is genuinely
    # all bar has an empty raw box already and never reaches this.
    density_cols = mask[raw_top:raw_bottom, :].mean(axis=0)
    density_rows = mask[:, raw_left:raw_right].mean(axis=1)
    cols = np.flatnonzero(density_cols >= min_density)
    rows = np.flatnonzero(density_rows >= min_density)
    if cols.size == 0 or rows.size == 0:
        return whole

    left, right = int(cols[0]), int(cols[-1]) + 1
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    if (right - left) < min_fraction * image.width or (
        bottom - top
    ) < min_fraction * image.height:
        return whole
    return ContentBox(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        frame_width=image.width,
        frame_height=image.height,
    )


def is_blank(image: Image.Image, *, threshold: int = DEFAULT_THRESHOLD) -> bool:
    """Whether nothing in ``image`` clears ``threshold`` -- an all-black capture."""
    if image.width <= 0 or image.height <= 0:
        return True
    limit = min(max(int(threshold), 0), 255)
    mask = image.convert("L").point(lambda value: 255 if value > limit else 0)
    return mask.getbbox() is None
