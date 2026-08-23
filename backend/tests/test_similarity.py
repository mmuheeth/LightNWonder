"""Cosine similarity between two pictures.

The one property the payline check depends on is that **brightness does not
count**: a game glows and pulses its symbols, so the same symbol on two reels
arrives at two brightnesses, and a measure that reads those as different is
useless here. A uniformly brighter copy is exactly parallel to its original, so
these tests assert 1.0 for it -- which is also what makes the measure worth
choosing over a pixel difference, and so worth pinning.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from app.utils import similarity


def solid(colour: tuple[int, int, int], size: tuple[int, int] = (8, 8)) -> Image.Image:
    """One flat picture, which is the simplest thing with a direction."""
    return Image.new("RGB", size, colour)


def tile(
    background: tuple[int, int, int],
    centre: tuple[int, int, int],
    size: tuple[int, int] = (20, 20),
    centre_size: tuple[int, int] = (10, 10),
) -> Image.Image:
    """A picture shaped like a symbol: one colour framed by another."""
    picture = Image.new("RGB", size, background)
    left = (size[0] - centre_size[0]) // 2
    top = (size[1] - centre_size[1]) // 2
    picture.paste(Image.new("RGB", centre_size, centre), (left, top))
    return picture


def raw_vector(image: Image.Image) -> np.ndarray:
    """The flat RGB vector :func:`similarity.vector` used to build before this
    change trimmed and corner-rounded its input -- used to show the trim
    actually moves the score, not just that nothing crashed."""
    return np.asarray(image.convert("RGB"), dtype=np.float64).ravel()


# --- what the measure is for ----------------------------------------------


def test_a_picture_is_identical_to_itself() -> None:
    picture = solid((200, 10, 10))

    assert similarity.cosine(picture, picture) == pytest.approx(1.0)


def test_a_brighter_copy_of_one_symbol_still_scores_one() -> None:
    """The whole reason cosine and not a difference: a glow is a scalar."""
    dim = solid((100, 20, 40))
    lit = solid((200, 40, 80))

    assert similarity.cosine(dim, lit) == pytest.approx(1.0)


def test_two_different_symbols_score_far_below_one() -> None:
    red = solid((200, 10, 10))
    green = solid((10, 200, 10))

    assert similarity.cosine(red, green) < 0.2


def test_the_score_never_exceeds_one() -> None:
    """Floating point can push a self-comparison over 1.0, which reads as a bug."""
    noisy = Image.new("RGB", (3, 3))
    noisy.putdata([(i * 7 % 256, i * 13 % 256, i * 29 % 256) for i in range(9)])

    assert similarity.cosine(noisy, noisy) <= 1.0


# --- the degenerate cases -------------------------------------------------


def test_two_black_pictures_are_the_same_picture() -> None:
    """A fade to black is a normal thing for a screenshot to catch."""
    assert similarity.cosine(solid((0, 0, 0)), solid((0, 0, 0))) == 1.0


def test_black_against_anything_else_is_as_different_as_it_gets() -> None:
    assert similarity.cosine(solid((0, 0, 0)), solid((10, 20, 30))) == 0.0


def test_different_sizes_are_refused_rather_than_resampled() -> None:
    """Tiles of one split are the same size by construction, so this is a bug."""
    with pytest.raises(similarity.SimilarityError) as exc:
        similarity.cosine(solid((1, 2, 3), (8, 8)), solid((1, 2, 3), (8, 9)))

    assert "same size" in str(exc.value)


# --- the border trim -------------------------------------------------------


def test_trimming_widens_separation_between_symbols_sharing_a_background() -> None:
    """The shared background is exactly what the trim is meant to discount."""
    background = (120, 120, 120)
    left = tile(background, (200, 10, 10))
    right = tile(background, (10, 200, 10))

    raw = similarity.vector_cosine(raw_vector(left), raw_vector(right))
    trimmed = similarity.cosine(left, right)

    assert trimmed < raw


def test_identical_symbols_still_score_one_after_trim() -> None:
    """The trim must not break the case it is not meant to change."""
    picture = tile((120, 120, 120), (200, 10, 10))

    assert similarity.cosine(picture, picture) == pytest.approx(1.0)
