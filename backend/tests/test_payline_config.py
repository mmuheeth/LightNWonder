"""Reading the ``paylines`` block of a game config.

Two things here are worth more than the rest. **A position is row-first**, and on
a 3x5 grid the transposed version of a coordinate is often still in range -- so
the only test that catches a transposition is one that asserts the name a
coordinate turns into. And **columns must strictly increase**, because a line
that backtracks a reel would compare a tile against itself and score a perfect
match, which is a config typo that would otherwise read as a win.
"""

from __future__ import annotations

import pytest

from app.utils import paylines

FIVE = {
    "1": [[2, 1], [2, 2], [2, 3], [2, 4], [2, 5]],
    "2": [[1, 1], [1, 2], [1, 3], [1, 4], [1, 5]],
    "4": [[1, 1], [2, 2], [3, 3], [2, 4], [1, 5]],
}
BLOCK = {"5": FIVE, "20": {"1": [[2, 1], [2, 2]]}}


# --- the sets -------------------------------------------------------------


def test_the_sets_are_offered_in_numeric_order() -> None:
    """Sorted as strings, "20" would come before "5"."""
    assert paylines.set_names({"5": {}, "20": {}, "40": {}}) == ("5", "20", "40")


def test_a_set_named_something_other_than_a_number_sorts_last() -> None:
    assert paylines.set_names({"bonus": {}, "5": {}}) == ("5", "bonus")


def test_listing_the_sets_does_not_parse_their_lines() -> None:
    """A typo in the forty-line set must not stop the five-line one being offered."""
    assert paylines.set_names({"5": FIVE, "40": "nonsense"}) == ("5", "40")


def test_a_block_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(paylines.PaylineError, match="must be a JSON object"):
        paylines.set_names([[2, 1]])


def test_a_block_with_no_sets_is_refused() -> None:
    with pytest.raises(paylines.PaylineError, match="declares no entries"):
        paylines.set_names({})


# --- one set --------------------------------------------------------------


def test_a_set_reads_its_lines_in_numeric_order() -> None:
    """Line 10 must not sort before line 2."""
    block = {"5": {"10": [[1, 1], [1, 2]], "2": [[2, 1], [2, 2]]}}

    assert [line.name for line in paylines.read_set(block, "5").lines] == ["2", "10"]


def test_a_position_is_row_first() -> None:
    """[2, 1] is the middle symbol of the leftmost reel, not the top of reel 2."""
    line = paylines.read_set(BLOCK, "5").lines[0]

    assert line.positions[0].row == 2
    assert line.positions[0].column == 1
    assert line.positions[0].name == "r2c1"


def test_a_line_reports_the_adjacent_pairs_a_read_compares() -> None:
    line = paylines.read_set({"5": {"1": [[2, 1], [2, 2], [3, 3]]}}, "5").lines[0]

    assert [(left.name, right.name) for left, right in line.steps] == [
        ("r2c1", "r2c2"),
        ("r2c2", "r3c3"),
    ]


def test_a_line_is_labelled_by_the_key_it_was_declared_under() -> None:
    assert paylines.read_set(BLOCK, "5").lines[2].label == "Line 4"


def test_a_set_that_is_not_declared_says_which_are() -> None:
    with pytest.raises(paylines.PaylineError) as exc:
        paylines.read_set(BLOCK, "40")

    assert "declares no '40' set" in str(exc.value)
    assert "5, 20" in str(exc.value)


# --- what a line may not be -----------------------------------------------


def test_a_line_that_backtracks_a_reel_is_refused() -> None:
    """Otherwise r2c2 is compared against itself and scores a perfect match."""
    with pytest.raises(paylines.PaylineError) as exc:
        paylines.read_set({"5": {"1": [[2, 1], [2, 2], [3, 2]]}}, "5")

    assert "must run left to right" in str(exc.value)
    assert "r3c2" in str(exc.value)


def test_a_line_with_one_position_is_refused() -> None:
    with pytest.raises(paylines.PaylineError, match="at least 2 positions"):
        paylines.read_set({"5": {"1": [[2, 1]]}}, "5")


def test_a_position_of_three_numbers_is_refused() -> None:
    with pytest.raises(paylines.PaylineError, match=r"\[row, column\]"):
        paylines.read_set({"5": {"1": [[2, 1, 1], [2, 2]]}}, "5")


def test_a_zero_coordinate_is_refused_because_positions_are_1_indexed() -> None:
    with pytest.raises(paylines.PaylineError, match="1-indexed"):
        paylines.read_set({"5": {"1": [[0, 1], [2, 2]]}}, "5")


def test_a_fractional_coordinate_is_refused() -> None:
    with pytest.raises(paylines.PaylineError, match="whole numbers"):
        paylines.read_set({"5": {"1": [[2.5, 1], [2, 2]]}}, "5")


def test_a_whole_float_coordinate_is_accepted() -> None:
    """What a JSON writer may emit for 2, and it means 2."""
    line = paylines.read_set({"5": {"1": [[2.0, 1.0], [2, 2]]}}, "5").lines[0]

    assert line.positions[0].name == "r2c1"


def test_true_is_not_a_row() -> None:
    """`bool` is an `int` in Python, and this project refuses that everywhere."""
    with pytest.raises(paylines.PaylineError, match="only numbers"):
        paylines.read_set({"5": {"1": [[True, 1], [2, 2]]}}, "5")


# --- fitting a grid -------------------------------------------------------


def test_a_set_that_fits_the_grid_raises_nothing() -> None:
    paylines.read_set(BLOCK, "5").within(3, 5)


def test_a_position_past_the_last_reel_names_the_line_and_the_position() -> None:
    line_set = paylines.read_set({"5": {"3": [[1, 1], [1, 6]]}}, "5")

    with pytest.raises(paylines.PaylineError) as exc:
        line_set.within(3, 5)

    assert "line '3' position r1c6" in str(exc.value)
    assert "column 6 of 5" in str(exc.value)


def test_a_position_below_the_last_row_is_caught_too() -> None:
    line_set = paylines.read_set({"5": {"1": [[4, 1], [4, 2]]}}, "5")

    with pytest.raises(paylines.PaylineError, match="row 4 of 3"):
        line_set.within(3, 5)
