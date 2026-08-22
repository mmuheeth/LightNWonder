"""Read the ``reel_bounds`` block of a game config.

A game config says where the reels are, and then how many symbol positions that
rectangle divides into::

    "roi":  { "reels": [0.35, 0.559722, 0.649219, 0.822222] },
    "reel_bounds": { "rows": 3, "columns": 5, "inset": 0.03 }

**The grid divides its crop evenly, and the two blocks are measured against
different rectangles.** ``roi.reels`` is fractions of the *frame*, like every
other region and click target in this project; the grid is a count of tiles
across the *reels crop* -- the rectangle ``roi.reels`` cut out. The reels fill
their own crop by definition, so reel ``c`` is the ``c``-th fifth of it and no
fractions have to be written down at all.

Counting rather than measuring is what keeps the two blocks independent. Move
the reel window on screen and only ``roi.reels`` changes; add a sixth reel and
only ``columns`` does. It is also what makes a new game a two-integer job: the
gaps between reel strips are a few pixels of background either side of a
boundary, not something a split has to be told about.

**``inset`` trims the borders the division cannot.** An even split gives a tile
everything up to its neighbour -- including the gap between the reel strips, and
including the frame the game draws *inside* a reel when it highlights a win,
which is a line of gold across the symbol's own edge. An inset shrinks every
tile towards its centre by a fraction **of that tile**, so the trim scales with
the symbol rather than with the frame. It is optional and defaults to nothing.

A tile is a rectangle of the crop, so :class:`ReelGrid` hands back
:class:`~app.utils.image_roi.Roi` objects rather than pixel boxes of its own --
the rounding that keeps a crop proportional across resolutions is already
written once, in the sibling module, and a tile is a region of the crop in
exactly the sense that a region is a region of the frame.

**Every tile gets the same pixel size, and that needs one deliberate step.**
An even division is not an even *pixel* division: three rows over a 298-pixel
crop round to 99, 100 and 99, and tiles that differ by a pixel cannot be
stacked, diffed, or fed to anything that expects one input size.
:meth:`ReelGrid.place` therefore rounds every tile's *position* independently --
so no tile drifts from the symbol it is aimed at -- and then gives all of them
the one size that fits every tile, which is the smallest of them. The pixel a
tall tile gives up comes off its bottom edge, where the gap between rows
already is.

Nothing here knows about game configs, screenshots, or where a tile gets
written. It takes the decoded block and hands back positioned rectangles; what
they are cut out of is the caller's business.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.utils.image_roi import Roi, RoiError

__all__ = [
    "COLUMNS_KEY",
    "INSET_KEY",
    "ROWS_KEY",
    "Inset",
    "PlacedTile",
    "ReelGrid",
    "ReelGridError",
    "Tile",
    "position_name",
]

# The keys the block is written with. Named here so the service, the tests and
# the error messages all spell them the same way.
COLUMNS_KEY = "columns"
ROWS_KEY = "rows"
INSET_KEY = "inset"

# The per-span form this block used to be written in. Kept only to be rejected:
# a config still carrying them would otherwise be split evenly and silently,
# which is the one outcome worse than failing to split at all.
_REPLACED_KEYS = {"col_bounds": COLUMNS_KEY, "row_bounds": ROWS_KEY}


class ReelGridError(ValueError):
    """The reel bounds are missing, malformed, or describe no usable grid."""


def position_name(row: int, column: int) -> str:
    """A matrix position as a name: ``r1c1`` is the top-left tile.

    One function rather than an f-string in two dataclasses, because this name
    ends up as a filename and as an API field, and the two must never drift.
    """
    return f"r{row}c{column}"


@dataclass(frozen=True)
class Inset:
    """How much of each tile edge to trim, as fractions of that tile.

    Fractions of the tile rather than of the crop, so one number keeps trimming
    the same proportion of a symbol whether the capture is 720p or 4K -- and so
    the same number reads the same for a wide reel and a narrow one.

    Zero on every edge is the default and means the tile is exactly its share of
    the crop. Nothing here is clamped: an inset that would leave no tile is
    rejected, because a one-pixel sliver of the middle of a symbol is not a
    smaller mistake than a crash.
    """

    left: float = 0.0
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0

    def __post_init__(self) -> None:
        """Reject a trim that would leave a tile with nothing in it."""
        for name, value in (
            ("left", self.left),
            ("top", self.top),
            ("right", self.right),
            ("bottom", self.bottom),
        ):
            # Also catches NaN, for which every comparison is false.
            if not 0.0 <= value < 1.0:
                raise ReelGridError(
                    f"{name} must be a fraction from 0 up to but not including "
                    f"1, got {value!r}"
                )
        if self.left + self.right >= 1.0:
            raise ReelGridError(
                f"left ({self.left}) and right ({self.right}) trim the whole "
                "width of every tile"
            )
        if self.top + self.bottom >= 1.0:
            raise ReelGridError(
                f"top ({self.top}) and bottom ({self.bottom}) trim the whole "
                "height of every tile"
            )

    @property
    def none(self) -> bool:
        """Whether this trims anything at all."""
        return not (self.left or self.top or self.right or self.bottom)

    @classmethod
    def from_value(cls, value: Any, *, where: str = INSET_KEY) -> Inset:
        """Read an inset from decoded JSON, in any of the three forms.

        One number trims every edge equally, which is the usual case. Two are
        ``[horizontal, vertical]``, for a game whose reels are framed on the
        sides but not top and bottom. Four are
        ``[left, top, right, bottom]`` -- the same order and meaning as a
        :class:`~app.utils.image_roi.Roi`, so the one convention covers both.

        Args:
            value: The decoded value. Untrusted -- it may be any JSON shape.
            where: What is being read, used to make the error locatable.
        """
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if isinstance(value, bool):
            # `bool` is an `int`, and `True` as a trim is a mistake, not a 1.
            raise ReelGridError(f"{where} must be a number or an array of numbers")
        if isinstance(value, (int, float)):
            edge = float(value)
            return cls._build(edge, edge, edge, edge, where=where)

        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ReelGridError(f"{where} must be a number or an array of numbers")
        edges: list[float] = []
        for edge in value:
            if isinstance(edge, bool) or not isinstance(edge, (int, float)):
                raise ReelGridError(f"{where} must contain only numbers, got {edge!r}")
            edges.append(float(edge))

        if len(edges) == 1:
            return cls._build(edges[0], edges[0], edges[0], edges[0], where=where)
        if len(edges) == 2:
            horizontal, vertical = edges
            return cls._build(horizontal, vertical, horizontal, vertical, where=where)
        if len(edges) == 4:
            return cls._build(*edges, where=where)
        raise ReelGridError(
            f"{where} must be one number, [horizontal, vertical] or "
            f"[left, top, right, bottom], got {len(edges)} numbers"
        )

    @classmethod
    def _build(
        cls,
        left: float,
        top: float,
        right: float,
        bottom: float,
        *,
        where: str,
    ) -> Inset:
        """Construct, turning the range errors into locatable ones."""
        try:
            return cls(left=left, top=top, right=right, bottom=bottom)
        except ReelGridError as exc:
            raise ReelGridError(f"{where}: {exc}") from exc

    def applied(self, left: float, top: float, right: float, bottom: float) -> Roi:
        """Shrink one tile's edges inwards and hand back the region.

        The trim is a fraction of the tile being shrunk, so it is computed from
        that tile's own width and height rather than from the crop's.
        """
        width, height = right - left, bottom - top
        return Roi(
            left=left + self.left * width,
            top=top + self.top * height,
            right=right - self.right * width,
            bottom=bottom - self.bottom * height,
        )


@dataclass(frozen=True)
class Tile:
    """One symbol position, and where it is in the matrix.

    :attr:`row` and :attr:`column` are 1-indexed, because they are read the way
    a paytable is read -- ``r1c1`` is the top symbol of reel 1.
    """

    row: int
    column: int

    roi: Roi
    """The tile, as fractions of the **reels crop** rather than of the frame."""

    @property
    def name(self) -> str:
        """Matrix position as a name: ``r1c1`` for the top-left tile."""
        return position_name(self.row, self.column)

    @property
    def file_name(self) -> str:
        """What the tile is written as, when it is written at all."""
        return f"{self.name}.png"


@dataclass(frozen=True)
class PlacedTile:
    """One tile resolved to the pixels of a crop of a known size.

    Separate from :class:`Tile` because a tile is resolution-independent and a
    placement is not: the same tile placed on a 383-pixel crop and on a
    1532-pixel one is two different boxes, and only the second kind can promise
    that every tile is the same size.
    """

    row: int
    column: int

    roi: Roi
    """The tile as the grid divides it, inset included. Fractions of the crop."""

    box: tuple[int, int, int, int]
    """``(left, top, right, bottom)`` in pixels of the crop.

    The width and height are the same for every tile of the grid, so this can be
    up to a pixel short of what :attr:`roi` alone would give at the right and
    bottom edges. The position is not rounded away -- only the size is shared.
    """

    @property
    def name(self) -> str:
        """Matrix position as a name: ``r1c1`` for the top-left tile."""
        return position_name(self.row, self.column)

    @property
    def file_name(self) -> str:
        """What the tile is written as, when it is written at all."""
        return f"{self.name}.png"

    @property
    def width(self) -> int:
        """Tile width in pixels."""
        return self.box[2] - self.box[0]

    @property
    def height(self) -> int:
        """Tile height in pixels."""
        return self.box[3] - self.box[1]


@dataclass(frozen=True)
class ReelGrid:
    """The matrix of symbol positions one crop divides into."""

    row_count: int
    """Symbol positions per reel -- the height of the matrix."""

    column_count: int
    """Reels -- the width of the matrix."""

    inset: Inset = Inset()
    """Border trim applied to every tile. Nothing, unless the config asks."""

    def __post_init__(self) -> None:
        """Reject a matrix with no tiles in it."""
        for key, value in (
            (ROWS_KEY, self.row_count),
            (COLUMNS_KEY, self.column_count),
        ):
            if value < 1:
                raise ReelGridError(f"{key} must be at least 1, got {value!r}")

    @classmethod
    def from_mapping(cls, block: Any, *, where: str = "reel_bounds") -> ReelGrid:
        """Read a grid from a decoded ``reel_bounds`` object.

        Args:
            block: The decoded value. Untrusted -- it may be any JSON shape.
            where: What is being read, used to make the error locatable.

        Raises:
            ReelGridError: if the block is not an object, either count is
                missing or is not a positive integer, the inset would leave no
                tile, or the block carries a key this does not read.
        """
        if not isinstance(block, Mapping):
            raise ReelGridError(f"{where} must be a JSON object")

        for old, new in _REPLACED_KEYS.items():
            if old in block:
                raise ReelGridError(
                    f"{where}.{old} is no longer read: the grid divides its crop "
                    f"evenly, so write {new!r} as a count of tiles instead. Any "
                    "gap between the reel strips is what 'inset' trims."
                )
        unknown = set(block) - {ROWS_KEY, COLUMNS_KEY, INSET_KEY}
        if unknown:
            known = ", ".join((ROWS_KEY, COLUMNS_KEY, INSET_KEY))
            raise ReelGridError(
                f"{where} has no {', '.join(sorted(unknown))} setting "
                f"(settings are: {known})"
            )

        return cls(
            row_count=_count(block.get(ROWS_KEY), where=f"{where}.{ROWS_KEY}"),
            column_count=_count(block.get(COLUMNS_KEY), where=f"{where}.{COLUMNS_KEY}"),
            inset=Inset.from_value(block.get(INSET_KEY), where=f"{where}.{INSET_KEY}"),
        )

    def with_inset(self, inset: Inset) -> ReelGrid:
        """The same grid, trimmed differently.

        For a caller overriding the configured trim for one split: the counts
        were already validated when the block was read, and re-reading them to
        change one number would be a second chance to reject them.
        """
        return ReelGrid(
            row_count=self.row_count, column_count=self.column_count, inset=inset
        )

    def tile(self, row: int, column: int) -> Tile:
        """One tile by its 1-indexed matrix position.

        Raises:
            ReelGridError: if the position is outside the grid. Named rather
                than an ``IndexError``, because a position usually arrives from
                a caller that was told the grid's shape and got it wrong.
        """
        if not 1 <= row <= self.row_count or not 1 <= column <= self.column_count:
            raise ReelGridError(
                f"r{row}c{column} is outside a "
                f"{self.row_count}x{self.column_count} grid"
            )
        try:
            roi = self.inset.applied(
                (column - 1) / self.column_count,
                (row - 1) / self.row_count,
                column / self.column_count,
                row / self.row_count,
            )
        except RoiError as exc:  # pragma: no cover - Inset rejects first
            raise ReelGridError(f"r{row}c{column} is not a usable tile: {exc}") from exc
        return Tile(row=row, column=column, roi=roi)

    def tiles(self) -> list[Tile]:
        """Every tile, row-major: the order the matrix is read in."""
        return [
            self.tile(row, column)
            for row in range(1, self.row_count + 1)
            for column in range(1, self.column_count + 1)
        ]

    def positions(self) -> list[list[str]]:
        """The tile names as a matrix, so the shape needs no re-deriving."""
        return [
            [position_name(row, column) for column in range(1, self.column_count + 1)]
            for row in range(1, self.row_count + 1)
        ]

    def tile_size(self, width: int, height: int) -> tuple[int, int]:
        """The one pixel size every tile gets on a crop this size.

        The smallest of the natural sizes, so a tile anchored at its own rounded
        start always still fits inside the crop -- taking the largest would push
        the last reel past the right edge and need a clamp, which is the
        uneven-tiles problem again wearing a different hat.

        Raises:
            ReelGridError: if the crop has no area to place tiles on.
        """
        if width <= 0 or height <= 0:
            raise ReelGridError("the crop must have a non-zero width and height")
        boxes = [tile.roi.to_box(width, height) for tile in self.tiles()]
        return (
            min(box[2] - box[0] for box in boxes),
            min(box[3] - box[1] for box in boxes),
        )

    def place(self, width: int, height: int) -> list[PlacedTile]:
        """Every tile as a pixel box on a crop this size, row-major.

        Each box keeps its own rounded left and top, so a tile stays aimed at
        the symbol its share of the crop covers, and takes the grid's shared
        width and height -- which is what makes fifteen tiles fifteen images of
        one size.

        Raises:
            ReelGridError: if the crop has no area to place tiles on.
        """
        tile_width, tile_height = self.tile_size(width, height)
        placed: list[PlacedTile] = []
        for tile in self.tiles():
            left, top, _, _ = tile.roi.to_box(width, height)
            # The shared size is the smallest natural one, so this never has to
            # pull a box inwards. Clamped anyway: a silently out-of-range box
            # would come back from Pillow as a padded image rather than an error.
            left = min(left, width - tile_width)
            top = min(top, height - tile_height)
            placed.append(
                PlacedTile(
                    row=tile.row,
                    column=tile.column,
                    roi=tile.roi,
                    box=(left, top, left + tile_width, top + tile_height),
                )
            )
        return placed


def _count(value: Any, *, where: str) -> int:
    """Narrow a decoded JSON value to one axis's number of tiles.

    A count, not a measurement: the crop divides into this many equal shares.
    Floats are refused rather than rounded, because ``2.5`` reels is a config
    the author meant something else by.
    """
    if value is None:
        raise ReelGridError(f"{where} must say how many tiles the crop divides into")
    # `bool` is an `int`, and `true` as a count is a mistake, not a 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReelGridError(f"{where} must be a whole number, got {value!r}")
    if value < 1:
        raise ReelGridError(f"{where} must be at least 1, got {value!r}")
    return value
