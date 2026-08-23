"""Cosine similarity between two pictures of the same size -- an angle rather
than an exact match, since a glowing/pulsing symbol scales its vector without
turning it. Not a probability, and no default threshold: pixel channels are
non-negative so same-scene pairs already sit around 0.7-0.9, with same-symbol
pairs above 0.96 -- real separation, but far above where "0.7 means similar"
would put it. The caller owns the cut.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

__all__ = [
    "SimilarityError",
    "cosine",
    "vector",
    "vector_cosine",
]

# Fraction of a picture's total width/height cropped away before comparison,
# split evenly across both edges -- 0.1 shrinks a 10px side to 9px.
_BORDER_TRIM = 0.1

# Corner-rounding radius as a fraction of the trimmed picture's shorter side.
_CORNER_RADIUS = 0.15


class SimilarityError(ValueError):
    """The two pictures are not comparable."""


def _trim(image: Image.Image) -> Image.Image:
    """Crop ``_BORDER_TRIM`` off the total width and height, evenly per edge.

    The total trimmed off each axis is rounded first and then split into a
    near and far edge -- rounding each half independently would round
    ``width * _BORDER_TRIM / 2 == 0.5`` down to ``0`` under Python's
    round-half-to-even and silently trim nothing off a 10px side.
    """
    width, height = image.size
    trim_w = round(width * _BORDER_TRIM)
    trim_h = round(height * _BORDER_TRIM)
    left, top = trim_w // 2, trim_h // 2
    right, bottom = trim_w - left, trim_h - top
    if width - left - right < 1 or height - top - bottom < 1:
        # A picture too small to trim without vanishing is left alone.
        return image
    return image.crop((left, top, width - right, height - bottom))


def _round_corners(image: Image.Image) -> Image.Image:
    """Zero out the pixels outside a rounded-rectangle mask.

    A tile's corners are the part of it least likely to hold symbol artwork
    even after :func:`_trim`, so this reaches a second, smaller slice of
    background the rectangular crop alone cannot.
    """
    width, height = image.size
    radius = round(min(width, height) * _CORNER_RADIUS)
    if radius < 1:
        return image
    rows, cols = np.ogrid[:height, :width]
    # Distance (in each axis) from the pixel to the nearest edge of its own
    # quadrant's corner box; only pixels inside that box are candidates for
    # falling outside the rounded corner.
    row_dist = np.minimum(rows, height - 1 - rows)
    col_dist = np.minimum(cols, width - 1 - cols)
    in_corner_box = (row_dist < radius) & (col_dist < radius)
    # The rounding circle is tangent to both inner edges of the corner box, so
    # its centre sits `radius` pixels in from the true corner on each axis.
    outside_circle = (radius - row_dist) ** 2 + (radius - col_dist) ** 2 > radius**2
    mask = in_corner_box & outside_circle
    pixels = np.array(image.convert("RGB"))
    pixels[mask] = 0
    return Image.fromarray(pixels, mode="RGB")


def vector(image: Image.Image) -> np.ndarray:
    """One picture as a flat vector of its RGB channels.

    RGB rather than luminance: a slot game distinguishes plenty of its symbols
    by colour alone -- a red ``A`` and a red ``Q`` differ far less in shape than
    in the strokes' hue -- and folding the channels together throws that away
    for no gain in robustness.

    Trimmed and corner-rounded first (see module docstring) so the background
    every tile at one position shares counts for less of the vector than the
    symbol in its middle does.
    """
    image = _round_corners(_trim(image))
    return np.asarray(image.convert("RGB"), dtype=np.float64).ravel()


def cosine(left: Image.Image, right: Image.Image) -> float:
    """How nearly two pictures point the same way, in ``[-1.0, 1.0]``.

    Must be the same size -- tiles of one split are, by construction, so a
    mismatch means the two came from different splits and is worth an error
    rather than a silent resize.
    """
    if left.size != right.size:
        raise SimilarityError(
            f"pictures must be the same size to compare, got {left.size} and "
            f"{right.size}"
        )
    return vector_cosine(vector(left), vector(right))


def vector_cosine(left: np.ndarray, right: np.ndarray) -> float:
    """The angle between two already-flattened pictures. Split out from
    :func:`cosine` so a caller comparing one tile against many converts each
    picture once, not once per pair."""
    left_norm = float(np.linalg.norm(left))
    right_norm = float(np.linalg.norm(right))
    if left_norm == 0.0 or right_norm == 0.0:
        # Both black is the same picture; one black and one not is as different
        # as this measure can say.
        return 1.0 if left_norm == right_norm else 0.0
    # Clamped because floating point can leave a picture compared with itself a
    # hair above 1.0, and a similarity of 1.0000000000000002 reads as a bug.
    return max(-1.0, min(1.0, float(left @ right) / (left_norm * right_norm)))
