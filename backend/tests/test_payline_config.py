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


def test_a_line_reports_the_tile_names_a_read_is_given() -> None:
    line = paylines.read_set({"5": {"1": [[2, 1], [2, 2], [3, 3]]}}, "5").lines[0]

    assert line.names == ("r2c1", "r2c2", "r3c3")


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


# --- reading a line with wilds --------------------------------------------
#
# The rule that cannot be decomposed into pairs, and the reason `read_run` is a
# function here rather than a loop in the service. A wild stands in for whatever
# the *run* is paying as, so `AA WC BB` is a run of two -- while every adjacent
# pair in it matches on its own. Any test below that could be satisfied by
# comparing neighbours is not testing this.

# FortuneOx's own list: the nine picture and card symbols, and deliberately not
# the scatters (`SC`) or feature symbols (`FG`) it pays by counting.
WILDS = paylines.WildRule.of(["AA", "BB", "CC", "DD", "EE", "FF", "GG", "HH", "JJ"])


def run(*codes: str | None, wilds: paylines.WildRule = WILDS) -> paylines.SymbolRun:
    """One line of codes, read left to right."""
    return paylines.read_run(list(codes), wilds)


def test_a_wild_stands_in_for_the_symbol_the_run_is_paying_as() -> None:
    read = run("AA", "WC", "AA", "AA", "AA")

    assert read.covered == 5
    assert read.symbol == "AA"
    assert read.leading_wilds == 0


def test_a_wild_cannot_be_two_symbols_at_once() -> None:
    """The whole reason a line is read as a line.

    Every adjacent pair here matches: the wild is an Ox beside the Ox and a
    Pisces beside the Pisces. Counting matched neighbours gives a run of three
    and a payout the cabinet never made.
    """
    read = run("AA", "WC", "BB", "BB", "BB")

    assert read.covered == 2
    assert read.symbol == "AA"


def test_a_run_of_only_wilds_pays_as_the_wild() -> None:
    """It has its own paytable row -- `WC WC WC ANY ANY` on FortuneOx."""
    read = run("WC", "WC", "WC", "SC", "AA")

    assert read.covered == 3
    assert read.symbol == "WC"
    assert read.leading_wilds == 3


def test_a_wild_on_reel_one_is_named_by_the_symbol_that_follows_it() -> None:
    read = run("WC", "WC", "AA", "AA", "AA")

    assert read.covered == 5
    assert read.symbol == "AA"
    # Both readings are reported, because both are combos and only a paytable
    # can say which is worth more.
    assert read.leading_wilds == 2


def test_the_run_reports_what_it_was_paying_as_at_each_position() -> None:
    """A wild-led run is genuinely the wild until the symbol it stood in for
    lands, and saying so is what makes a break explainable."""
    read = run("WC", "WC", "AA", "BB", "AA")

    assert read.covered == 3
    assert read.line_symbols == ("WC", "WC", "AA", None, None)


def test_a_wild_does_not_stand_in_for_a_scatter() -> None:
    """The list is closed on purpose: a scatter is paid by counting it across the
    grid, so a wild beside two of them is not three of them."""
    read = run("SC", "WC", "SC", "SC", "SC")

    assert read.covered == 1
    assert read.symbol == "SC"


def test_a_scatter_after_a_run_of_wilds_does_not_name_it() -> None:
    read = run("WC", "WC", "SC", "SC", "SC")

    assert read.covered == 2
    assert read.symbol == "WC"


def test_a_wild_beyond_a_run_it_cannot_join_stops_there() -> None:
    read = run("SC", "SC", "WC", "SC", "SC")

    assert read.covered == 2
    assert read.symbol == "SC"


def test_an_unnamed_tile_stops_the_run_wild_or_not() -> None:
    read = run("AA", "WC", None, "AA", "AA")

    assert read.covered == 2
    assert read.symbol == "AA"


def test_an_unnamed_first_tile_covers_only_itself() -> None:
    """Nothing to carry, so nothing can join -- and a run of one pays nothing."""
    read = run(None, "AA", "AA", "AA", "AA")

    assert read.covered == 1
    assert read.symbol is None


def test_a_game_declaring_no_substitution_compares_by_equality() -> None:
    """Which is every config written before the block existed. The wild is then
    an ordinary symbol, and `WC WC WC` is still a run of three of it."""
    read = run("WC", "WC", "WC", "AA", "AA", wilds=paylines.NO_WILDS)

    assert read.covered == 3
    assert read.symbol == "WC"
    # Nothing was substituted, so nothing is reported as having been.
    assert read.leading_wilds == 0


def test_a_wild_beside_a_symbol_is_not_a_run_without_the_rule() -> None:
    read = run("AA", "WC", "AA", "AA", "AA", wilds=paylines.NO_WILDS)

    assert read.covered == 1
    assert read.symbol == "AA"


def test_a_line_with_no_positions_covers_nothing() -> None:
    assert paylines.read_run([], WILDS).covered == 0


# --- the substitution rule itself -----------------------------------------


def test_a_rule_upper_cases_what_it_was_given() -> None:
    """A config written in lower case still matches the codes the maths writes."""
    rule = paylines.WildRule.of(["aa", " bb "])

    assert rule.replaces == frozenset({"AA", "BB"})
    assert rule.stands_in_for("AA") is True


def test_a_rule_standing_in_for_nothing_recognises_no_wild() -> None:
    """Otherwise a run would stop for a reason nothing measured: a wild that
    substitutes for nothing behaves exactly like the symbol it is."""
    assert paylines.NO_WILDS.active is False
    assert paylines.NO_WILDS.is_wild("WC") is False


def test_the_wild_recognises_itself_once_it_substitutes() -> None:
    assert WILDS.is_wild("WC") is True
    assert WILDS.is_wild("AA") is False
    assert WILDS.is_wild(None) is False


def test_the_wild_does_not_stand_in_for_itself() -> None:
    """`WC WC` is a pair of wilds by equality, never by substitution."""
    assert WILDS.stands_in_for("WC") is False
    assert WILDS.stands_in_for(None) is False
