"""Reading and resolving the ``button_targets`` block.

The point of these is that a target is only useful if it survives the round trip
from a hand-written JSON file to a pixel in a window of a size nobody measured
it at. So the numbers here are the real ones out of ``games/FortuneOx.json``, and
the sizes are the real simulator window and multiples of it.
"""

from __future__ import annotations

import math
import re

import pytest

from app.utils.click_target import (
    DEFAULT_CONFIRMATIONS,
    ClickTarget,
    ClickTargetError,
    named_target,
    target_names,
)

# Straight out of app/config/game_config/games/FortuneOx.json.
TAKE_WIN = [0.0713, 0.9724]
GAMBLE = [0.0694, 0.9383]

# The simulator's own window, from the game's launch line.
WIDTH, HEIGHT = 518, 1033


# --- reading a declared target -------------------------------------------


def test_a_bare_pair_is_a_point() -> None:
    target = ClickTarget.from_value(GAMBLE)
    assert (target.x, target.y) == (0.0694, 0.9383)


def test_a_known_name_picks_up_its_default_confirmation() -> None:
    """The two targets every game shares do not have to declare a proof."""
    assert ClickTarget.from_value(GAMBLE, name="gamble").confirm == "gamble-accepted"
    assert (
        ClickTarget.from_value(TAKE_WIN, name="take_win").confirm == "gamble-declined"
    )


def test_an_unknown_name_gets_no_confirmation() -> None:
    """Not an error: it only means a click there cannot name what it hit."""
    assert ClickTarget.from_value([0.5, 0.5], name="mystery_button").confirm is None


def test_the_object_form_carries_an_explicit_confirmation() -> None:
    target = ClickTarget.from_value(
        {"point": [0.5, 0.25], "confirm": "gamble-picked"}, name="red"
    )
    assert (target.x, target.y, target.confirm) == (0.5, 0.25, "gamble-picked")


def test_an_explicit_confirmation_overrides_the_default() -> None:
    target = ClickTarget.from_value(
        {"point": GAMBLE, "confirm": "spin-started"}, name="gamble"
    )
    assert target.confirm == "spin-started"


def test_a_null_confirmation_opts_out_of_the_default() -> None:
    """A game whose gamble button is unprovable can say so."""
    target = ClickTarget.from_value({"point": GAMBLE, "confirm": None}, name="gamble")
    assert target.confirm is None


def test_the_object_form_needs_a_point() -> None:
    with pytest.raises(ClickTargetError, match="missing its 'point'"):
        ClickTarget.from_value({"confirm": "gamble-accepted"}, where="button_targets.x")


def test_an_existing_target_passes_through() -> None:
    original = ClickTarget(x=0.1, y=0.2, confirm="gamble-accepted")
    assert ClickTarget.from_value(original) is original


# --- rejecting a target no window could be clicked at ---------------------


@pytest.mark.parametrize(
    "value",
    [
        [0.5],
        [0.5, 0.5, 0.5],
        "0.5,0.5",
        b"\x00\x01",
        42,
        None,
        {"point": [0.5]},
    ],
)
def test_a_point_that_is_not_two_numbers_is_rejected(value: object) -> None:
    with pytest.raises(ClickTargetError):
        ClickTarget.from_value(value)


@pytest.mark.parametrize("value", [[-0.01, 0.5], [0.5, 1.01], [2, 0.5]])
def test_a_fraction_outside_the_window_is_rejected(value: object) -> None:
    with pytest.raises(ClickTargetError, match="fraction between 0 and 1"):
        ClickTarget.from_value(value)


def test_nan_is_rejected() -> None:
    """Every comparison against NaN is false, so the range check has to catch it."""
    with pytest.raises(ClickTargetError, match="fraction between 0 and 1"):
        ClickTarget.from_value([math.nan, 0.5])


def test_a_boolean_is_not_a_coordinate() -> None:
    """`bool` is an `int`, and True as an x is a mistake rather than a 1."""
    with pytest.raises(ClickTargetError, match="only numbers"):
        ClickTarget.from_value([True, 0.5])


def test_a_non_string_confirmation_is_rejected() -> None:
    with pytest.raises(ClickTargetError, match="non-empty event name"):
        ClickTarget.from_value({"point": GAMBLE, "confirm": 7})


def test_a_blank_confirmation_is_rejected() -> None:
    with pytest.raises(ClickTargetError, match="non-empty event name"):
        ClickTarget.from_value({"point": GAMBLE, "confirm": "   "})


def test_the_error_names_the_target_it_came_from() -> None:
    """A bad number in a config is only actionable if it says which one."""
    with pytest.raises(ClickTargetError, match=re.escape("button_targets.gamble")):
        ClickTarget.from_value([5, 0.5], where="button_targets.gamble")


# --- resolving to a pixel -------------------------------------------------


def test_a_fraction_becomes_a_point_in_the_simulator_window() -> None:
    assert ClickTarget.from_value(GAMBLE).to_point(WIDTH, HEIGHT) == (36, 969)
    assert ClickTarget.from_value(TAKE_WIN).to_point(WIDTH, HEIGHT) == (37, 1004)


def test_the_point_scales_with_the_window() -> None:
    """The whole reason the config holds fractions rather than pixels.

    Within a pixel rather than exactly double: each axis is rounded
    independently, so doubling the window can land either side of twice the
    original. Staying within one pixel is what keeps a target on its button.
    """
    target = ClickTarget.from_value(GAMBLE)
    single = target.to_point(WIDTH, HEIGHT)
    doubled = target.to_point(WIDTH * 2, HEIGHT * 2)
    assert abs(doubled[0] - single[0] * 2) <= 1
    assert abs(doubled[1] - single[1] * 2) <= 1
    # And it really did scale, rather than being clamped or ignored.
    assert doubled[1] > single[1] * 1.9


def test_the_point_stays_inside_the_window() -> None:
    """A fraction of exactly 1 would round to one pixel past the last one."""
    assert ClickTarget(x=1.0, y=1.0).to_point(100, 200) == (99, 199)
    assert ClickTarget(x=0.0, y=0.0).to_point(100, 200) == (0, 0)


def test_rounding_rather_than_truncating_keeps_a_point_proportional() -> None:
    """Truncation drifts a point up and left by up to a pixel at every size.

    0.6 of 101 is 60.6: rounded it is 61, truncated it would be 60. Matches how
    :mod:`app.utils.image_roi` resolves a region's edges, ties included -- both
    use ``round``, so both round a half to even.
    """
    assert ClickTarget(x=0.6, y=0.6).to_point(101, 101) == (61, 61)
    assert ClickTarget(x=0.5, y=0.5).to_point(101, 101) == (50, 50)


@pytest.mark.parametrize(("width", "height"), [(0, 100), (100, 0), (-1, 100)])
def test_a_window_with_no_area_has_nowhere_to_aim(width: int, height: int) -> None:
    with pytest.raises(ClickTargetError, match="non-zero width and height"):
        ClickTarget(x=0.5, y=0.5).to_point(width, height)


def test_a_pixel_measurement_converts_to_fractions() -> None:
    """How a new target gets measured: read pixels off one screenshot."""
    target = ClickTarget.from_pixels([36, 969], width=WIDTH, height=HEIGHT)
    assert target.to_point(WIDTH, HEIGHT) == (36, 969)


def test_a_pixel_measurement_outside_the_frame_is_rejected() -> None:
    with pytest.raises(ClickTargetError, match="does not fit a 518x1033 frame"):
        ClickTarget.from_pixels([600, 969], width=WIDTH, height=HEIGHT)


# --- looking one up by name ----------------------------------------------


def test_a_target_is_found_by_name() -> None:
    block = {"take_win": TAKE_WIN, "gamble": GAMBLE}
    assert named_target(block, "gamble").to_point(WIDTH, HEIGHT) == (36, 969)


def test_a_name_is_matched_case_insensitively_and_trimmed() -> None:
    """The block is hand-written; a caller should not have to match its casing."""
    block = {"take_win": TAKE_WIN}
    assert named_target(block, "  TAKE_WIN ").confirm == "gamble-declined"


def test_an_unknown_name_lists_what_is_configured() -> None:
    """So a typo is self-correcting rather than a guessing game."""
    block = {"take_win": TAKE_WIN, "gamble": GAMBLE}
    with pytest.raises(ClickTargetError) as caught:
        named_target(block, "collect")
    assert "gamble, take_win" in str(caught.value)


def test_an_empty_block_says_so_rather_than_listing_nothing() -> None:
    with pytest.raises(ClickTargetError, match="configured targets: none"):
        named_target({}, "gamble")


def test_target_names_are_sorted() -> None:
    assert target_names({"gamble": GAMBLE, "take_win": TAKE_WIN}) == [
        "gamble",
        "take_win",
    ]


def test_the_default_confirmations_name_real_game_log_events() -> None:
    """A default that named a non-existent event would silently never confirm."""
    from app.utils.game_log import DEFAULT_RULES

    known = {rule.event for rule in DEFAULT_RULES}
    assert set(DEFAULT_CONFIRMATIONS.values()) <= known
