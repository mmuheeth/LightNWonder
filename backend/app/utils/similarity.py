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


class SimilarityError(ValueError):
    """The two pictures are not comparable."""


def vector(image: Image.Image) -> np.ndarray:
    """One picture as a flat vector of its RGB channels -- RGB, not luminance,
    since a red ``A`` and a red ``Q`` differ more in hue than in shape."""
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
