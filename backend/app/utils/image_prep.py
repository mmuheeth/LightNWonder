"""Makes a rendered reference image comparable to a captured tile.

A reference exported from the game's own assets is not shaped like a capture:
the symbol sits on a transparent field with a margin around it, at whatever
size the artist worked at. Dropping the alpha channel keeps whatever colours
sat *under* the transparent pixels, and stretching the square field onto a
capture's rectangle distorts the artwork -- both read as "different" to a
measure that compares pictures, however alike the symbols are. This module is
the preparation between reading such a file and comparing it: transparency is
composited onto black, the transparent margin is cropped, and a size mismatch
*fills* the target rectangle -- scaled with its shape kept and the overflow
centre-cropped, never stretched and never padded. Losing a sliver of artwork
is deliberate: a black bar would be measured (and shown) as picture.

Black is the flattening colour deliberately: a black pixel contributes nothing
to a cosine comparison (zero in every channel), so what remains of the vector
is the artwork rather than a guessed background.

Deliberately ignorant of who consumes it, like the rest of :mod:`app.utils`.
"""

from __future__ import annotations

from typing import NamedTuple

from PIL import Image

__all__ = [
    "PreparedImage",
    "fill",
    "flatten",
    "prepare",
    "trim_transparent",
]

# Alpha below this counts as background when the margin is cropped, so a faint
# glow spilling past the artwork is cut rather than keeping the whole margin.
# Content that faint contributes almost nothing to a comparison; losing it is
# the point of the crop.
_ALPHA_CUT = 32


def _has_alpha(image: Image.Image) -> bool:
    """Whether the picture carries transparency in any of PIL's spellings."""
    return image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )


def trim_transparent(image: Image.Image) -> tuple[Image.Image, bool]:
    """Crop to the box where alpha reaches ``_ALPHA_CUT``, and say whether it did.

    An opaque picture is returned untouched -- a captured tile's dark border is
    background the game drew, not a margin to remove. A fully transparent
    picture is also returned untouched: there is no box to crop to, and
    :func:`flatten` will make what it is (all black) visible.
    """
    if not _has_alpha(image):
        return image, False
    rgba = image.convert("RGBA")
    mask = rgba.getchannel("A").point(lambda alpha: 255 if alpha >= _ALPHA_CUT else 0)
    box = mask.getbbox()
    if box is None or box == (0, 0, rgba.width, rgba.height):
        return rgba, False
    return rgba.crop(box), True


def flatten(image: Image.Image) -> Image.Image:
    """The picture as RGB, transparency composited onto black rather than
    dropped -- ``convert("RGB")`` alone keeps whatever colours sat under the
    transparent pixels."""
    if not _has_alpha(image):
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def fill(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize to fill ``size`` without changing shape: scaled to cover both
    axes and centre-cropped along the one that overflows. Never a stretch --
    distorted artwork reads as a different symbol -- and never a pad, because
    a black bar would be measured as picture. The overflow is artwork
    deliberately lost."""
    if image.size == size:
        return image
    scale = max(size[0] / image.width, size[1] / image.height)
    scaled = (
        max(size[0], round(image.width * scale)),
        max(size[1], round(image.height * scale)),
    )
    resized = image.resize(scaled, Image.Resampling.LANCZOS)
    if scaled == size:
        return resized
    left = (scaled[0] - size[0]) // 2
    top = (scaled[1] - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


class PreparedImage(NamedTuple):
    """One picture made comparable, and what that took."""

    image: Image.Image
    trimmed: bool
    resized: bool


def prepare(image: Image.Image, size: tuple[int, int]) -> PreparedImage:
    """The whole preparation, in the only order that works: the margin is
    cropped while the alpha channel still exists, the result is flattened,
    and only then does a size mismatch fill the target."""
    cropped, trimmed = trim_transparent(image)
    flattened = flatten(cropped)
    resized = flattened.size != size
    return PreparedImage(fill(flattened, size), trimmed, resized)
