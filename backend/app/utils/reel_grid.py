"""Read the ``reel_bounds`` block of a game config: ``rows``/``columns`` divide
the ``roi.reels`` crop evenly (a count, not a fraction of the frame), which keeps
the two blocks independent -- moving the reel window changes only ``roi.reels``,
adding a reel changes only ``columns``. Optional ``inset`` trims each tile's
border (as a fraction of that tile) to remove the reel-strip gap and any win-
highlight frame the game draws inside a reel. Because an even split isn't an
even *pixel* split, :meth:`ReelGrid.place` rounds each tile's position
independently, then gives every tile the smallest natural size so all tiles
match -- required since unequal tiles can't be stacked or diffed.
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

# Named here so the service, tests and error messages spell them the same way.
COLUMNS_KEY = "columns"
ROWS_KEY = "rows"
INSET_KEY = "inset"

# The old per-span form. Kept only to be rejected -- a config still carrying it
# would otherwise split evenly and silently, which is worse than failing loudly.
_REPLACED_KEYS = {"col_bounds": COLUMNS_KEY, "row_bounds": ROWS_KEY}


class ReelGridError(ValueError):
    """The reel bounds are missing, malformed, or describe no usable grid."""


def position_name(row: int, column: int) -> str:
    """A matrix position as a name: ``r1c1`` is the top-left tile. One function
    rather than an f-string in two dataclasses, so a filename and an API field
    can't drift apart."""
    return f"r{row}c{column}"


@dataclass(frozen=True)
class Inset:
    """How much of each tile edge to trim, as fractions of that tile (not the
    crop), so the trim reads the same for a wide reel or a narrow one. Nothing
    is clamped: an inset that would leave no tile is rejected outright.
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
        """Read an inset from decoded JSON: one number (all edges), two
        (``[horizontal, vertical]``), or four (``[left, top, right, bottom]``,
        same order as :class:`~app.utils.image_roi.Roi`)."""
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
        """Shrink one tile's edges inwards, using that tile's own width and
        height as the trim's basis."""
        width, height = right - left, bottom - top
        return Roi(
            left=left + self.left * width,
            top=top + self.top * height,
            right=right - self.right * width,
            bottom=bottom - self.bottom * height,
        )


@dataclass(frozen=True)
class Tile:
    """One symbol position, and where it is in the matrix. :attr:`row` and
    :attr:`column` are 1-indexed, the way a paytable is read."""

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
    """One tile resolved to the pixels of a crop of a known size. Separate from
    :class:`Tile` since a tile is resolution-independent and a placement isn't.
    """

    row: int
    column: int

    roi: Roi
    """The tile as the grid divides it, inset included. Fractions of the crop."""

    box: tuple[int, int, int, int]
    """``(left, top, right, bottom)`` in pixels of the crop. Width/height are
    shared across the grid, so this can be up to a pixel short of what
    :attr:`roi` alone would give at the right/bottom edges."""

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
        """Read a grid from a decoded ``reel_bounds`` object."""
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
        """The same grid, trimmed differently -- for a caller overriding the
        configured inset for one split without re-validating the counts."""
        return ReelGrid(
            row_count=self.row_count, column_count=self.column_count, inset=inset
        )

    def tile(self, row: int, column: int) -> Tile:
        """One tile by its 1-indexed matrix position. Raises ``ReelGridError``
        (not ``IndexError``) if the position is outside the grid."""
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
        """The one pixel size every tile gets on a crop this size -- the
        smallest natural size, so a tile anchored at its rounded start always
        still fits (the largest would push the last reel past the edge)."""
        if width <= 0 or height <= 0:
            raise ReelGridError("the crop must have a non-zero width and height")
        boxes = [tile.roi.to_box(width, height) for tile in self.tiles()]
        return (
            min(box[2] - box[0] for box in boxes),
            min(box[3] - box[1] for box in boxes),
        )

    def place(self, width: int, height: int) -> list[PlacedTile]:
        """Every tile as a pixel box on a crop this size, row-major. Each box
        keeps its own rounded left/top but takes the grid's shared width and
        height, so every tile comes out the same size."""
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
    """Narrow a decoded JSON value to one axis's number of tiles. Floats are
    refused rather than rounded -- ``2.5`` reels means the author meant
    something else."""
    if value is None:
        raise ReelGridError(f"{where} must say how many tiles the crop divides into")
    # `bool` is an `int`, and `true` as a count is a mistake, not a 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReelGridError(f"{where} must be a whole number, got {value!r}")
    if value < 1:
        raise ReelGridError(f"{where} must be at least 1, got {value!r}")
    return value
