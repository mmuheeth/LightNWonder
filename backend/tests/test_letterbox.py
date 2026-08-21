"""Finding the game inside a letterboxed canvas.

The detection itself is three lines; what needs proving is the judgement around
it. Two frames of the same canvas size with the game window at two different
shapes must give two different content boxes -- that is the bug this module was
written for, a region measured on one and used on the other. And the three ways
detection can be wrong must all come back as the whole frame rather than as a
confident, tiny rectangle: a black frame, a nearly-black frame, and a game that
happens to be dark at one edge.

The shapes here are the real ones. A FortuneOx window at 632x1080 lands as a
421-pixel-wide strip of a 1280x720 canvas and the same window widened lands as
643; those two numbers are what the tests are built from.
"""

from __future__ import annotations

import pytest
from PIL import Image

from app.utils.letterbox import (
    DEFAULT_MIN_FRACTION,
    DEFAULT_THRESHOLD,
    ContentBox,
    content_box,
)

CANVAS = (1280, 720)

# The two real captures: one window shape, then the same window made wider.
NARROW = (429, 0, 850, 720)
WIDE = (318, 0, 961, 720)


def canvas(
    box: tuple[int, int, int, int],
    *,
    size: tuple[int, int] = CANVAS,
    fill: tuple[int, int, int] = (200, 180, 40),
    bars: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """A frame of ``size`` that is ``fill`` inside ``box`` and ``bars`` outside."""
    frame = Image.new("RGB", size, bars)
    left, top, right, bottom = box
    frame.paste(Image.new("RGB", (right - left, bottom - top), fill), (left, top))
    return frame


# --- Finding the content ----------------------------------------------------


def test_finds_the_game_inside_the_bars() -> None:
    found = content_box(canvas(NARROW))

    assert found.box == NARROW
    assert (found.width, found.height) == (421, 720)
    assert found.letterboxed


def test_the_same_canvas_with_a_wider_window_gives_a_wider_box() -> None:
    """The bug in one assertion: same file size, different picture, different box."""
    narrow = content_box(canvas(NARROW))
    wide = content_box(canvas(WIDE))

    assert narrow.box != wide.box
    assert wide.width > narrow.width
    assert narrow.frame_width == wide.frame_width == CANVAS[0]


def test_bars_top_and_bottom_are_found_too() -> None:
    """A window wider than the canvas's shape letterboxes on the other axis."""
    found = content_box(canvas((0, 140, 1280, 580)))

    assert found.box == (0, 140, 1280, 580)
    assert (found.width, found.height) == (1280, 440)


def test_a_frame_that_is_all_game_is_not_letterboxed() -> None:
    found = content_box(canvas((0, 0, 1280, 720)))

    assert found.box == (0, 0, 1280, 720)
    assert not found.letterboxed


def test_a_dark_interior_is_kept() -> None:
    """Only the border is trimmed, so a black patch mid-picture stays inside."""
    frame = canvas(NARROW)
    frame.paste(Image.new("RGB", (100, 100), (0, 0, 0)), (500, 300))

    assert content_box(frame).box == NARROW


def test_a_bar_that_is_not_quite_black_is_still_a_bar() -> None:
    frame = canvas(NARROW, bars=(4, 4, 4))

    assert content_box(frame).box == NARROW


# --- Refusing to guess ------------------------------------------------------


def test_a_black_frame_comes_back_whole() -> None:
    """OBS writes a few kilobytes of black between scenes; that is not a window."""
    found = content_box(Image.new("RGB", CANVAS, (0, 0, 0)))

    assert found.box == (0, 0, *CANVAS)
    assert not found.letterboxed


def test_a_box_below_the_minimum_fraction_comes_back_whole() -> None:
    """A dark game screen with one bright corner is not a tiny window."""
    small = 100  # well under a quarter of 1280
    found = content_box(canvas((0, 0, small, small)))

    assert small < DEFAULT_MIN_FRACTION * CANVAS[0]
    assert found.box == (0, 0, *CANVAS)


def test_min_fraction_is_per_axis() -> None:
    """Wide enough but far too short is still refused."""
    found = content_box(canvas((0, 0, 1280, 20)))

    assert found.box == (0, 0, *CANVAS)


def test_a_box_just_over_the_minimum_fraction_is_believed() -> None:
    box = (0, 0, 400, 200)  # both axes over a quarter of 1280x720

    assert content_box(canvas(box)).box == box


# --- The threshold ----------------------------------------------------------


def test_the_threshold_decides_what_counts_as_a_bar() -> None:
    """Bars brighter than the default are content until the threshold is raised."""
    frame = canvas(NARROW, bars=(30, 30, 30))

    assert content_box(frame).box == (0, 0, *CANVAS)
    assert content_box(frame, threshold=40).box == NARROW


def test_a_threshold_outside_the_byte_range_is_clamped_not_rejected() -> None:
    """It arrives from configuration, and a frame is croppable at either end."""
    frame = canvas(NARROW)

    assert content_box(frame, threshold=-5).box == NARROW
    assert content_box(frame, threshold=999).box == (0, 0, *CANVAS)


def test_the_default_threshold_sits_between_a_bar_and_a_game() -> None:
    assert 0 < DEFAULT_THRESHOLD < 180


# --- The box itself ---------------------------------------------------------


def test_whole_is_the_frame_and_says_so() -> None:
    found = ContentBox.whole(400, 200)

    assert found.box == (0, 0, 400, 200)
    assert (found.width, found.height) == (400, 200)
    assert not found.letterboxed


@pytest.mark.parametrize("mode", ["L", "RGBA", "P"])
def test_any_image_mode_is_read(mode: str) -> None:
    found = content_box(canvas(NARROW).convert(mode))

    assert found.box == NARROW
