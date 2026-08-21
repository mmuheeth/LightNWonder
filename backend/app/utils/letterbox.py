"""Find the part of a captured frame the game actually fills.

OBS writes every frame at its canvas size -- 1280x720 here, whatever the game
window happens to be -- and fits the window capture inside that canvas with its
aspect ratio preserved. A portrait simulator in a landscape canvas therefore
arrives with black down both sides, and the width of those bars is a property of
**the window's shape at the moment the shot was taken**, nothing else. Resize the
simulator from 632x1080 to 964x1080 and the game slides outwards over the bars:
same canvas, same file size, different picture in a different place.

That is the whole problem this module exists for. A region measured as fractions
of the *canvas* only stays aimed at the same thing while the bars stay the same
width, so it survives a change of capture resolution and breaks on a resize --
which is the one of the two that happens by accident. Fractions of the **content
box** survive both, because the content box is the game window: it is the
rectangle whose edges the game's own layout is anchored to.

So the region a game config declares is resolved against the box this module
finds, and the same four numbers describe the same part of the game whether the
window is portrait, square or wide. It also puts ``roi`` and ``button_targets``
in one space at last -- a click target is a fraction of the window client area,
and the content box is that area as the canvas received it.

**A bar is a border of near-black, and nothing else counts.** The frame is
thresholded and the bounding box of what survives is taken, so everything
outside the box is dark by construction. Two things are refused rather than
believed, because both look exactly like a very letterboxed frame:

- A frame with nothing above the threshold at all -- a shot taken while the game
  was between scenes, which OBS writes as a few kilobytes of black.
- A box smaller than ``min_fraction`` of either dimension, which is a dark game
  screen rather than a small window.

Both come back as the whole frame, which is what the region was measured against
before this module existed. Refusing loudly would be worse: a fade to black is a
normal thing for a screenshot to catch, and one crop off a black frame is a
better outcome than an error on the frame after it.

Nothing here knows about game configs, OBS or regions. It takes an image and
hands back a rectangle; what gets measured against it is the caller's business.
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

# Where a pixel stops being a letterbox bar. OBS fills the canvas around a
# source with pure black and the games' own edges come in above 180, so the
# threshold sits far from both rather than close to either.
DEFAULT_THRESHOLD = 8

# The smallest a content box may be, per axis, as a fraction of the frame. A
# window smaller than a quarter of the canvas is not something anyone is trying
# to read a meter off, so a box that small is taken as a dark game screen.
DEFAULT_MIN_FRACTION = 0.25


@dataclass(frozen=True)
class ContentBox:
    """The rectangle of a frame the game fills, in pixels of that frame.

    Edges are half-open like Pillow's own box: ``right`` and ``bottom`` are one
    past the last pixel kept. The frame's own size is carried along because
    "is this letterboxed" is a question about the pair, and a caller holding
    only the box cannot answer it.
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

    Args:
        image: An open frame, of any size or mode.
        threshold: Luminance a pixel must exceed to count as content, ``0..255``.
            Clamped into that range rather than rejected: it arrives from
            configuration, and a frame is still croppable at either end of it.
        min_fraction: Smallest acceptable box per axis, as a fraction of the
            frame. A smaller one is taken as a dark game screen and the whole
            frame is returned instead.

    Returns:
        The content box, which is the whole frame when there are no bars to trim
        or when the picture is too dark to trust.
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
