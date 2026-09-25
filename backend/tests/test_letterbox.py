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


def test_a_stray_spark_in_the_bar_does_not_drag_the_box_out() -> None:
    """A win-celebration particle effect can drift a few lit pixels well past the
    real window edge; the box must still land on the window, not on the spark.

    This is the bug a drifting reels ROI was traced to: `getbbox()` alone finds
    the box of every lit pixel, spark included, so one frame's content box
    landed 90px wider than the next frame of the identical window because a
    single particle happened to be further out that time.
    """
    frame = canvas(NARROW)
    for x, y in [(900, 50), (920, 200), (940, 400)]:
        frame.putpixel((x, y), (200, 180, 40))

    assert content_box(frame).box == NARROW


def test_a_column_lit_for_its_whole_height_is_never_filtered() -> None:
    """The density floor must not eat a real window edge -- only a spark spread
    over almost none of its own column's height is meant to be rejected."""
    frame = canvas(NARROW)
    # One column of the real window, confirmed lit its entire height: at 100%
    # density it must clear any reasonable min_density floor, including one
    # far stricter than the default.
    found = content_box(frame, min_density=0.9)

    assert found.box == NARROW


def test_a_drifting_decoration_far_taller_than_a_spark_still_does_not_count() -> None:
    """The bug this was measured against: not a one-pixel spark but a coin or
    cloud sprite tens of pixels tall, animating well outside the real window,
    lighting a real chunk of its column and still nowhere near a window edge's
    own density. A frame with sparks scattered across a wide vertical range (so
    the naive `raw` box is nearly the whole frame, diluting a plain full-frame
    density check) must still land on the window, not the decoration."""
    frame = canvas(NARROW)
    decoration = Image.new("RGB", (12, 40), (200, 180, 40))
    frame.paste(decoration, (950, 300))
    for x, y in [(1000, 20), (1010, 400), (1020, 700)]:
        frame.putpixel((x, y), (200, 180, 40))

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
