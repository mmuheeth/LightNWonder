"""Reading the ``reel_bounds`` block of a game config.

Two things are worth checking here and the rest is shape validation.

**The matrix positions are 1-indexed and row-major**, because they are what the
tiles get named after and a silently transposed grid would be labelled wrongly
everywhere downstream without anything failing.

**The order of the bounds is enforced.** A list of spans that is out of order
still splits into the right number of tiles, and every one of them is labelled
wrong -- so it is rejected where it is read rather than a few files later.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.utils.image_roi import Roi
from app.utils.reel_grid import Inset, ReelGrid, ReelGridError, Span

# FortuneOx's own bounds: five reels with gaps between them, three equal rows.
FORTUNE_OX: dict[str, Any] = {
    "col_bounds": [
        [0.0, 0.193089],
        [0.20122, 0.395325],
        [0.403455, 0.596545],
        [0.604675, 0.79878],
        [0.806911, 1.0],
    ],
    "row_bounds": [[0.0, 0.333333], [0.333333, 0.666667], [0.666667, 1.0]],
}


def edges(roi: Roi) -> tuple[float, float, float, float]:
    """A region as four numbers, for comparing against `pytest.approx`.

    An inset multiplies one fraction by another, so exact equality on the
    product is a floating-point coin flip -- 0.1 of 0.2 is not 0.02.
    """
    return (roi.left, roi.top, roi.right, roi.bottom)


# Round fractions, so a pixel box needs no rounding allowance.
FIFTHS: dict[str, Any] = {
    "col_bounds": [[0.0, 0.2], [0.2, 0.4], [0.4, 0.6], [0.6, 0.8], [0.8, 1.0]],
    "row_bounds": [[0.0, 0.5], [0.5, 1.0]],
}


# --- reading the block ----------------------------------------------------


def test_reads_the_shipped_bounds() -> None:
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    assert (grid.row_count, grid.column_count) == (3, 5)
    assert grid.columns[0] == Span(start=0.0, end=0.193089)
    assert grid.rows[-1] == Span(start=0.666667, end=1.0)


def test_a_span_is_the_pair_it_was_declared_as() -> None:
    grid = ReelGrid.from_mapping(FIFTHS)

    assert grid.columns == (
        Span(0.0, 0.2),
        Span(0.2, 0.4),
        Span(0.4, 0.6),
        Span(0.6, 0.8),
        Span(0.8, 1.0),
    )


def test_integers_are_accepted_as_bounds() -> None:
    """``[0, 1]`` in JSON decodes to ints, and means what ``[0.0, 1.0]`` means."""
    grid = ReelGrid.from_mapping({"col_bounds": [[0, 1]], "row_bounds": [[0, 1]]})

    assert grid.columns == (Span(0.0, 1.0),)


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

    assert tile.roi == Roi(left=0.4, top=0.5, right=0.6, bottom=1.0)
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


# --- order ----------------------------------------------------------------


def test_bounds_out_of_order_are_rejected() -> None:
    """The index is the reel number, so a shuffled list mislabels every tile."""
    with pytest.raises(ReelGridError, match=r"does not come after"):
        ReelGrid.from_mapping(
            {
                "col_bounds": [[0.4, 0.6], [0.0, 0.2]],
                "row_bounds": [[0.0, 1.0]],
            }
        )


def test_overlapping_bounds_are_allowed() -> None:
    """A tile may share a few pixels with its neighbour; only order is enforced."""
    grid = ReelGrid.from_mapping(
        {
            "col_bounds": [[0.0, 0.55], [0.45, 1.0]],
            "row_bounds": [[0.0, 1.0]],
        }
    )

    assert grid.column_count == 2


def test_duplicate_bounds_are_rejected() -> None:
    """Two identical spans are two tiles that cannot both be reel 1."""
    with pytest.raises(ReelGridError, match=r"does not come after"):
        ReelGrid.from_mapping(
            {"col_bounds": [[0.0, 0.5], [0.0, 0.5]], "row_bounds": [[0.0, 1.0]]}
        )


# --- malformed blocks -----------------------------------------------------


def test_a_block_that_is_not_an_object_is_rejected() -> None:
    with pytest.raises(ReelGridError, match=r"reel_bounds must be a JSON object"):
        ReelGrid.from_mapping([[0.0, 1.0]])


@pytest.mark.parametrize("missing", ["col_bounds", "row_bounds"])
def test_both_axes_are_required(missing: str) -> None:
    block = dict(FIFTHS)
    del block[missing]

    with pytest.raises(ReelGridError, match=rf"reel_bounds\.{missing}"):
        ReelGrid.from_mapping(block)


def test_an_empty_axis_is_rejected() -> None:
    """No reels is not a grid with nothing in it; it is an unusable block."""
    with pytest.raises(ReelGridError, match=r"at least one \[start, end\] pair"):
        ReelGrid.from_mapping({"col_bounds": [], "row_bounds": [[0.0, 1.0]]})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([[0.0]], r"must be \[start, end\], got 1 numbers"),
        ([[0.0, 0.5, 1.0]], r"must be \[start, end\], got 3 numbers"),
        (["0,1"], r"must be an array of two numbers"),
        ([[0.0, "1"]], r"must contain only numbers"),
        ([[0.0, True]], r"must contain only numbers"),
        ([[-0.1, 0.5]], r"start must be a fraction between 0 and 1"),
        ([[0.5, 1.5]], r"end must be a fraction between 0 and 1"),
        ([[0.6, 0.4]], r"start \(0.6\) must be less than end \(0.4\)"),
        ([[0.5, 0.5]], r"start \(0.5\) must be less than end \(0.5\)"),
        ([[float("nan"), 1.0]], r"start must be a fraction between 0 and 1"),
        ("0.0,1.0", r"must be an array of \[start, end\] pairs"),
    ],
)
def test_malformed_bounds_are_rejected_with_their_location(
    value: Any, expected: str
) -> None:
    with pytest.raises(ReelGridError, match=expected):
        ReelGrid.from_mapping({"col_bounds": value, "row_bounds": [[0.0, 1.0]]})


def test_the_error_names_which_pair_was_wrong() -> None:
    """Which index, not only which axis -- five reels is five places to look."""
    with pytest.raises(ReelGridError, match=r"reel_bounds\.col_bounds\[2\]"):
        ReelGrid.from_mapping(
            {
                "col_bounds": [[0.0, 0.2], [0.3, 0.5], [0.9, 0.6]],
                "row_bounds": [[0.0, 1.0]],
            }
        )


# --- the border inset -----------------------------------------------------
# The bounds divide the crop edge to edge, so a tile takes everything between
# its neighbours -- including the frame a game draws inside a reel to highlight
# a win. The inset is what trims that, and it is a fraction of the tile rather
# than of the crop so one number means the same thing for a wide reel and a
# narrow one.


def test_no_inset_by_default() -> None:
    grid = ReelGrid.from_mapping(FIFTHS)

    assert grid.inset == Inset()
    assert grid.inset.none
    assert grid.tile(1, 1).roi == Roi(left=0.0, top=0.0, right=0.2, bottom=0.5)


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


def test_with_inset_replaces_the_trim_and_keeps_the_bounds() -> None:
    """A request overriding the trim must not re-read the bounds to do it."""
    grid = ReelGrid.from_mapping({**FIFTHS, "inset": 0.1})

    replaced = grid.with_inset(Inset(left=0.25))

    assert replaced.columns == grid.columns
    assert replaced.rows == grid.rows
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
# Rounding each tile's edges on its own is right for a single region and wrong
# for a grid: FortuneOx's five reels over a 200-pixel crop come out 39, 39, 38,
# 39, 39 purely from where the boundaries fall. Every test here uses bounds that
# would vary, because bounds that divide evenly cannot catch this at all.

# Deliberately awkward: neither axis divides evenly into the crop below.
AWKWARD_CROP = (200, 298)


def test_the_shared_tile_size_is_the_smallest_that_fits() -> None:
    """The largest would push the last reel past the crop and need a clamp."""
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    assert grid.tile_size(*AWKWARD_CROP) == (38, 99)


def test_bounds_that_would_vary_still_place_uniform_tiles() -> None:
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    natural = {
        (
            tile.roi.to_box(*AWKWARD_CROP)[2] - tile.roi.to_box(*AWKWARD_CROP)[0],
            tile.roi.to_box(*AWKWARD_CROP)[3] - tile.roi.to_box(*AWKWARD_CROP)[1],
        )
        for tile in grid.tiles()
    }
    placed = {(tile.width, tile.height) for tile in grid.place(*AWKWARD_CROP)}

    # The premise of the test, then the guarantee.
    assert len(natural) > 1
    assert placed == {(38, 99)}


def test_placement_keeps_each_tile_at_its_own_position() -> None:
    """Only the size is shared. A uniform *pitch* would drift off the symbols."""
    grid = ReelGrid.from_mapping(FORTUNE_OX)

    placed = {tile.name: tile.box for tile in grid.place(*AWKWARD_CROP)}

    # Reel 3 starts at 81, not at 2 x 40 -- its own rounded left edge.
    assert placed["r1c1"] == (0, 0, 38, 99)
    assert placed["r1c3"] == (81, 0, 119, 99)
    assert placed["r3c5"] == (161, 199, 199, 298)


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

    for width, height in ((200, 298), (383, 189), (1532, 756), (37, 11)):
        sizes = {(t.width, t.height) for t in grid.place(width, height)}
        assert len(sizes) == 1, f"{width}x{height} gave {sizes}"


def test_a_crop_with_no_area_is_refused() -> None:
    grid = ReelGrid.from_mapping(FIFTHS)

    with pytest.raises(ReelGridError, match=r"non-zero width and height"):
        grid.tile_size(0, 100)
