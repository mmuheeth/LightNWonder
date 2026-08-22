"""Cosine similarity between two pictures of the same size.

The reel grid writes fifteen equally-sized tiles per split, and the question
after that is which of them hold the same symbol. Two crops of one symbol are
never identical -- a game glows, pulses and scales its symbols to draw the eye,
so the same pot of gold arrives a shade brighter and a percent larger on one
reel than on the next -- and an exact comparison answers "different" for every
pair. Cosine similarity answers it as an angle instead: brightening a symbol
lengthens its vector without turning it, so a scale or a glow moves the score by
much less than a different symbol does.

**The score is not a probability, and its useful threshold is per-game.** Pixel
channels are non-negative, so every pair of pictures of the same scene starts out
with a high cosine -- two symbols sharing one reel background sit around 0.7 to
0.9 whether or not they are the same symbol, and two crops of one symbol sit
above 0.96. The separation is real and wide, but it is not where a naive reading
of "0.7 means similar" would put it. That is why nothing here has a default
threshold: the caller owns the cut.

Nothing here knows about tiles, reels or paylines. It takes two pictures and
returns a number.
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
    """One picture as a flat vector of its RGB channels.

    RGB rather than luminance: a slot game distinguishes plenty of its symbols
    by colour alone -- a red ``A`` and a red ``Q`` differ far less in shape than
    in the strokes' hue -- and folding the channels together throws that away
    for no gain in robustness.
    """
    return np.asarray(image.convert("RGB"), dtype=np.float64).ravel()


def cosine(left: Image.Image, right: Image.Image) -> float:
    """How nearly two pictures point the same way, in ``[-1.0, 1.0]``.

    Both pictures must be the same size, which for tiles of one split they are
    by construction -- :meth:`app.utils.reel_grid.ReelGrid.place` gives every
    tile one shared width and height precisely so that comparisons like this one
    need no resampling. A mismatch here therefore means the two came from
    different splits, which is worth an error rather than a silent resize.

    Two black pictures score 1.0 and a black one against anything else scores
    0.0: a fade to black is a normal thing for a screenshot to catch, and
    dividing by a zero-length vector is not.

    Raises:
        SimilarityError: if the two pictures are different sizes.
    """
    if left.size != right.size:
        raise SimilarityError(
            f"pictures must be the same size to compare, got {left.size} and "
            f"{right.size}"
        )
    return vector_cosine(vector(left), vector(right))


def vector_cosine(left: np.ndarray, right: np.ndarray) -> float:
    """The angle between two already-flattened pictures.

    Split out from :func:`cosine` so a caller comparing one tile against many
    converts each picture once instead of once per pair, which is the difference
    between fifteen conversions and a hundred and five.
    """
    left_norm = float(np.linalg.norm(left))
    right_norm = float(np.linalg.norm(right))
    if left_norm == 0.0 or right_norm == 0.0:
        # Both black is the same picture; one black and one not is as different
        # as this measure can say.
        return 1.0 if left_norm == right_norm else 0.0
    # Clamped because floating point can leave a picture compared with itself a
    # hair above 1.0, and a similarity of 1.0000000000000002 reads as a bug.
    return max(-1.0, min(1.0, float(left @ right) / (left_norm * right_norm)))
