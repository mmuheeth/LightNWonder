"""Reading the ``reel_bounds`` block of a game config.

Two things are worth checking here and the rest is shape validation.

**The matrix positions are 1-indexed and row-major**, because they are what the
tiles get named after and a silently transposed grid would be labelled wrongly
everywhere downstream without anything failing.

**An even division is not an even pixel division.** Three rows over a 298-pixel
crop are 99, 100 and 99 tall, so the shared-size step is what keeps a split
usable -- and the tests for it deliberately use crops that do not divide evenly,
because one that does cannot catch the bug at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.utils.image_roi import Roi
from app.utils.reel_grid import Inset, ReelGrid, ReelGridError

# FortuneOx's own grid: five reels of three symbols.
FORTUNE_OX: dict[str, Any] = {"rows": 3, "columns": 5}


def edges(roi: Roi) -> tuple[float, float, float, float]:
    """A region as four numbers, for comparing against `pytest.approx`.

    An inset multiplies one fraction by another, so exact equality on the
    product is a floating-point coin flip -- 0.1 of 0.2 is not 0.02.
    """
    return (roi.left, roi.top, roi.right, roi.bottom)


# Round fractions, so a pixel box needs no rounding allowance.
FIFTHS: dict[str, Any] = {"rows": 2, "columns": 5}


# --- reading the block ----------------------------------------------------


def test_reads_the_shipped_grid() -> None:
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    assert (grid.row_count, grid.column_count) == (3, 5)


def test_the_counts_divide_the_crop_into_equal_shares() -> None:
    """Reel 1 is the first fifth of the crop, reel 5 the last."""
    grid = ReelGrid.from_mapping(FIFTHS)

    assert edges(grid.tile(1, 1).roi) == pytest.approx((0.0, 0.0, 0.2, 0.5))
    assert edges(grid.tile(2, 5).roi) == pytest.approx((0.8, 0.5, 1.0, 1.0))


def test_a_single_tile_grid_is_the_whole_crop() -> None:
    grid = ReelGrid.from_mapping({"rows": 1, "columns": 1})

    assert edges(grid.tile(1, 1).roi) == pytest.approx((0.0, 0.0, 1.0, 1.0))


# --- the matrix ------------------------------------------------------------


def test_tiles_are_row_major_and_one_indexed() -> None:
    """r1c1 is the top-left tile, and the list reads the way the matrix does."""
    grid = ReelGrid.from_mapping(FIFTHS)

    names = [tile.name for tile in grid.tiles()]

    assert names[:5] == ["r1c1", "r1c2", "r1c3", "r1c4", "r1c5"]
    assert names[-1] == "r2c5"
    assert len(names) == 10


def test_positions_are_the_same_names_as_a_matrix() -> None:
    grid = ReelGrid.from_mapping(FIFTHS)

    assert grid.positions() == [
        ["r1c1", "r1c2", "r1c3", "r1c4", "r1c5"],
        ["r2c1", "r2c2", "r2c3", "r2c4", "r2c5"],
    ]


def test_a_tile_is_a_region_of_the_crop() -> None:
    """The column gives left/right and the row gives top/bottom, not the reverse."""
    grid = ReelGrid.from_mapping(FIFTHS)

    tile = grid.tile(2, 3)

    assert edges(tile.roi) == pytest.approx((0.4, 0.5, 0.6, 1.0))
    assert (tile.row, tile.column) == (2, 3)


def test_a_tile_names_the_file_it_would_be_written_as() -> None:
    assert ReelGrid.from_mapping(FIFTHS).tile(1, 4).file_name == "r1c4.png"


def test_tiles_resolve_to_pixels_of_the_crop() -> None:
    """A tile is an `Roi`, so it inherits the rounding that keeps crops scaling."""
    grid = ReelGrid.from_mapping(FIFTHS)

    # A 200x100 crop: five 40px columns, two 50px rows.
    assert grid.tile(1, 1).roi.to_box(200, 100) == (0, 0, 40, 50)
    assert grid.tile(2, 5).roi.to_box(200, 100) == (160, 50, 200, 100)
    # Four times the crop, four times the tile, same part of the picture.
    assert grid.tile(2, 5).roi.to_box(800, 400) == (640, 200, 800, 400)


def test_a_position_outside_the_grid_is_named_not_an_index_error() -> None:
    grid = ReelGrid.from_mapping(FIFTHS)

    with pytest.raises(ReelGridError, match=r"r3c1 is outside a 2x5 grid"):
        grid.tile(3, 1)


@pytest.mark.parametrize("position", [(0, 1), (1, 0), (1, 6)])
def test_zero_and_past_the_end_are_both_outside(position: tuple[int, int]) -> None:
    """1-indexed, so r0c1 is as much a mistake as r3c1."""
    grid = ReelGrid.from_mapping(FIFTHS)

    with pytest.raises(ReelGridError):
        grid.tile(*position)


# --- malformed blocks -----------------------------------------------------


def test_a_block_that_is_not_an_object_is_rejected() -> None:
    with pytest.raises(ReelGridError, match=r"reel_bounds must be a JSON object"):
        ReelGrid.from_mapping([[0.0, 1.0]])


@pytest.mark.parametrize("missing", ["rows", "columns"])
def test_both_axes_are_required(missing: str) -> None:
    block = dict(FIFTHS)
    del block[missing]

    with pytest.raises(ReelGridError, match=rf"reel_bounds\.{missing} must say how"):
        ReelGrid.from_mapping(block)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, r"must be at least 1, got 0"),
        (-2, r"must be at least 1, got -2"),
        (2.5, r"must be a whole number, got 2.5"),
        # A count written as 3.0 is still a float, and refused rather than
        # rounded: it means the author was measuring, not counting.
        (3.0, r"must be a whole number, got 3.0"),
        (True, r"must be a whole number, got True"),
        ("5", r"must be a whole number, got '5'"),
        ([5], r"must be a whole number, got \[5\]"),
    ],
)
def test_a_malformed_count_is_rejected_with_its_location(
    value: Any, expected: str
) -> None:
    with pytest.raises(ReelGridError, match=rf"reel_bounds\.columns {expected}"):
        ReelGrid.from_mapping({**FIFTHS, "columns": value})


# --- the form this block used to take --------------------------------------
# Spans were replaced by counts. A config still carrying them has to fail: an
# ignored `col_bounds` would split evenly anyway and look entirely correct,
# which is the one outcome worse than refusing to split.


@pytest.mark.parametrize("old", ["col_bounds", "row_bounds"])
def test_the_replaced_span_keys_are_refused_not_ignored(old: str) -> None:
    with pytest.raises(ReelGridError, match=rf"reel_bounds\.{old} is no longer read"):
        ReelGrid.from_mapping({**FIFTHS, old: [[0.0, 1.0]]})


def test_the_refusal_says_what_to_write_instead() -> None:
    with pytest.raises(ReelGridError, match=r"write 'columns' as a count"):
        ReelGrid.from_mapping({"col_bounds": [[0.0, 1.0]], "rows": 3})


def test_an_unknown_key_is_refused() -> None:
    """A misspelled setting silently doing nothing is a config that reads as a lie."""
    with pytest.raises(ReelGridError, match=r"has no gutter setting"):
        ReelGrid.from_mapping({**FIFTHS, "gutter": 0.01})


# --- the border inset -----------------------------------------------------
# The grid divides the crop evenly, so a tile takes everything up to its
# neighbour -- including the gap between reel strips and the frame a game draws
# inside a reel to highlight a win. The inset is what trims that, and it is a
# fraction of the tile rather than of the crop so one number means the same
# thing for a wide reel and a narrow one.


def test_no_inset_by_default() -> None:
    grid = ReelGrid.from_mapping(FIFTHS)

    assert grid.inset == Inset()
    assert grid.inset.none
    assert edges(grid.tile(1, 1).roi) == pytest.approx((0.0, 0.0, 0.2, 0.5))


def test_one_number_trims_every_edge() -> None:
    """A tenth off each edge of a 0.2-wide, 0.5-tall tile."""
    grid = ReelGrid.from_mapping({**FIFTHS, "inset": 0.1})

    assert edges(grid.tile(1, 1).roi) == pytest.approx((0.02, 0.05, 0.18, 0.45))


def test_two_numbers_are_horizontal_then_vertical() -> None:
    grid = ReelGrid.from_mapping({**FIFTHS, "inset": [0.1, 0.2]})

    assert edges(grid.tile(1, 1).roi) == pytest.approx((0.02, 0.1, 0.18, 0.4))


def test_four_numbers_are_left_top_right_bottom() -> None:
    """The same order as an `Roi`, so the one convention covers both."""
    grid = ReelGrid.from_mapping({**FIFTHS, "inset": [0.1, 0.2, 0.0, 0.0]})

    assert edges(grid.tile(1, 1).roi) == pytest.approx((0.02, 0.1, 0.2, 0.5))


def test_the_trim_is_a_fraction_of_the_tile_not_of_the_crop() -> None:
    """The whole point: one number means the same trim for any tile size.

    Reel 1 here is 0.2 of the crop wide and row 1 is 0.5 of it tall, so a tenth
    of the tile is 0.02 horizontally and 0.05 vertically -- different fractions
    of the crop, the same fraction of the symbol.
    """
    grid = ReelGrid.from_mapping({**FIFTHS, "inset": 0.1})

    tile = grid.tile(1, 1)
    assert tile.roi.right - tile.roi.left == pytest.approx(0.2 * 0.8)
    assert tile.roi.bottom - tile.roi.top == pytest.approx(0.5 * 0.8)


def test_the_inset_shrinks_every_tile_equally_in_pixels() -> None:
    """On a 200x100 crop, a tenth of a 40x50 tile is 4px and 5px."""
    grid = ReelGrid.from_mapping({**FIFTHS, "inset": 0.1})

    assert grid.tile(1, 1).roi.to_box(200, 100) == (4, 5, 36, 45)
    assert grid.tile(2, 5).roi.to_box(200, 100) == (164, 55, 196, 95)


def test_an_inset_of_zero_is_no_inset() -> None:
    """Explicit zero and absent have to agree, or a config reads as a lie."""
    assert ReelGrid.from_mapping({**FIFTHS, "inset": 0}).tile(1, 1).roi == (
        ReelGrid.from_mapping(FIFTHS).tile(1, 1).roi
    )


def test_with_inset_replaces_the_trim_and_keeps_the_shape() -> None:
    """A request overriding the trim must not re-read the counts to do it."""
    grid = ReelGrid.from_mapping({**FIFTHS, "inset": 0.1})

    replaced = grid.with_inset(Inset(left=0.25))

    assert (replaced.row_count, replaced.column_count) == (2, 5)
    assert edges(replaced.tile(1, 1).roi) == pytest.approx((0.05, 0.0, 0.2, 0.5))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-0.1, r"left must be a fraction from 0 up to but not including 1"),
        (1.0, r"left must be a fraction from 0 up to but not including 1"),
        (float("nan"), r"left must be a fraction from 0 up to but not including 1"),
        (True, r"must be a number or an array of numbers"),
        ("0.1", r"must be a number or an array of numbers"),
        ([0.1, 0.1, 0.1], r"got 3 numbers"),
        ([0.1] * 5, r"got 5 numbers"),
        ([0.1, "x"], r"must contain only numbers"),
        ([0.6, 0.0, 0.6, 0.0], r"trim the whole width of every tile"),
        ([0.0, 0.5, 0.0, 0.5], r"trim the whole height of every tile"),
    ],
)
def test_a_malformed_inset_is_rejected_with_its_location(
    value: Any, expected: str
) -> None:
    with pytest.raises(ReelGridError, match=expected):
        ReelGrid.from_mapping({**FIFTHS, "inset": value})


def test_the_inset_error_names_the_block_it_came_from() -> None:
    with pytest.raises(ReelGridError, match=r"reel_bounds\.inset"):
        ReelGrid.from_mapping({**FIFTHS, "inset": -1})


def test_an_inset_that_leaves_nothing_is_rejected_not_clamped() -> None:
    """A sliver of the middle of a symbol is not a smaller mistake than a crash."""
    with pytest.raises(ReelGridError):
        ReelGrid.from_mapping({**FIFTHS, "inset": 0.5})


# --- one size for every tile ----------------------------------------------
# An even division is not an even pixel division: three rows over 298 pixels
# round to 99, 100, 99 and five reels over 202 to 40, 41, 40, 41, 40, purely
# from where each boundary falls. Every test here uses a crop that divides
# unevenly on both axes, because one that divides evenly cannot catch this.

# Deliberately awkward: neither axis divides evenly into the crop below.
AWKWARD_CROP = (202, 298)


def test_the_shared_tile_size_is_the_smallest_that_fits() -> None:
    """The largest would push the last reel past the crop and need a clamp."""
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    assert grid.tile_size(*AWKWARD_CROP) == (40, 99)


def test_a_division_that_would_vary_still_places_uniform_tiles() -> None:
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    natural = {
        (box[2] - box[0], box[3] - box[1])
        for box in (tile.roi.to_box(*AWKWARD_CROP) for tile in grid.tiles())
    }
    placed = {(tile.width, tile.height) for tile in grid.place(*AWKWARD_CROP)}

    # The premise of the test, then the guarantee.
    assert len(natural) > 1
    assert placed == {(40, 99)}


def test_placement_keeps_each_tile_at_its_own_position() -> None:
    """Only the size is shared. A uniform *pitch* would drift off the symbols."""
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    placed = {tile.name: tile.box for tile in grid.place(*AWKWARD_CROP)}

    # Reel 3 starts at 81, not at 2 x 40 -- its own rounded left edge. Row 3
    # starts at 199, not at 2 x 99, for the same reason.
    assert placed["r1c1"] == (0, 0, 40, 99)
    assert placed["r1c3"] == (81, 0, 121, 99)
    assert placed["r3c5"] == (162, 199, 202, 298)


def test_every_placed_tile_is_a_distinct_box() -> None:
    """Fifteen positions, fifteen boxes -- no tile silently doubled up."""
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    boxes = [tile.box for tile in grid.place(*AWKWARD_CROP)]

    assert len(set(boxes)) == len(boxes) == 15


def test_placement_is_row_major_and_named_like_the_tiles() -> None:
    grid = ReelGrid.from_mapping(FIFTHS)

    placed = grid.place(200, 100)

    assert [tile.name for tile in placed] == [tile.name for tile in grid.tiles()]
    assert placed[0].file_name == "r1c1.png"


def test_placed_tiles_stay_inside_the_crop() -> None:
    """Every box has to be croppable, or Pillow pads it instead of failing."""
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    for tile in grid.place(*AWKWARD_CROP):
        left, top, right, bottom = tile.box
        assert 0 <= left < right <= AWKWARD_CROP[0]
        assert 0 <= top < bottom <= AWKWARD_CROP[1]


def test_the_inset_does_not_reintroduce_uneven_tiles() -> None:
    """The trim shrinks fractions, so it is a second chance to round unevenly."""
    grid = ReelGrid.from_mapping({**FORTUNE_OX, "inset": 0.03})

    assert len({(t.width, t.height) for t in grid.place(*AWKWARD_CROP)}) == 1


def test_tiles_stay_uniform_at_any_crop_size() -> None:
    """One size per crop, whatever the crop -- not one size that happens to fit."""
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    for width, height in ((202, 298), (383, 189), (1532, 756), (37, 11)):
        sizes = {(t.width, t.height) for t in grid.place(width, height)}
        assert len(sizes) == 1, f"{width}x{height} gave {sizes}"


def test_a_crop_with_no_area_is_refused() -> None:
    grid = ReelGrid.from_mapping(FIFTHS)

    with pytest.raises(ReelGridError, match=r"non-zero width and height"):
        grid.tile_size(0, 100)
