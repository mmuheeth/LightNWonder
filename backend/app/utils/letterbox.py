"""Find the part of a captured frame the game actually fills (the "content
box"). OBS letterboxes a window capture inside its fixed canvas, and the bar
width is a property of the window's shape at capture time, not the canvas --
so regions/click targets are resolved against this box, not canvas fractions,
to survive a resize. Detection refuses (returns the whole frame) when nothing
clears the threshold or the box is under ``min_fraction`` of the frame, since
both look like a normal fade-to-black and a bad crop there beats an error.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

__all__ = [
    "DEFAULT_MIN_FRACTION",
    "DEFAULT_THRESHOLD",
    "ContentBox",
    "content_box",
]

# Luminance where a bar stops: OBS pads with pure black, game edges start above 180.
DEFAULT_THRESHOLD = 8

# Smallest content box allowed per axis, as a fraction of the frame -- smaller
# is treated as a dark game screen rather than a small window.
DEFAULT_MIN_FRACTION = 0.25


@dataclass(frozen=True)
class ContentBox:
    """The rectangle of a frame the game fills, in pixels of that frame.

    Edges are half-open like Pillow's own box. Frame size is carried along
    since "is this letterboxed" needs the pair, not just the box.
    """

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
) -> ContentBox:
    """The part of ``image`` that is game rather than letterbox.

    ``threshold`` is clamped into ``0..255`` rather than rejected, since it
    arrives from configuration and a frame is still croppable at either end.
    """
    whole = ContentBox.whole(image.width, image.height)
    if image.width <= 0 or image.height <= 0:
        return whole

    limit = min(max(int(threshold), 0), 255)
    # `getbbox` is the bounding box of the non-zero pixels, so thresholding
    # first turns "everything brighter than a bar" into exactly that box -- and
    # everything outside it is below the threshold by construction.
    mask = image.convert("L").point(lambda value: 255 if value > limit else 0)
    found = mask.getbbox()
    if found is None:
        # Nothing above the threshold: a black frame, not a zero-size window.
        return whole

    left, top, right, bottom = found
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
