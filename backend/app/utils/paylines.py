"""Read the ``paylines`` block of a game config.

A game config says where the reels are, then how they divide into symbol
positions, and then which sequences of those positions pay::

    "paylines": {
      "5": {
        "1": [[2,1],[2,2],[2,3],[2,4],[2,5]],
        "4": [[1,1],[2,2],[3,3],[2,4],[1,5]]
      },
      "20": { ... },
      "40": { ... }
    }

**The outer keys are bet configurations, not indices.** A cabinet that can be
played for five lines, twenty or forty ships all three, and line 4 of the
five-line set is not line 4 of the forty-line set even where the coordinates
happen to agree. So a set is looked up by name and evaluated on its own, and
:func:`set_names` is what offers the choice.

**A position is ``[row, column]``, both 1-indexed, in the same numbering as a
tile's name.** ``[2,1]`` is ``r2c1``, the middle symbol of the leftmost reel --
which is why :func:`app.utils.reel_grid.position_name` is what turns one into a
string here rather than an f-string of its own. Row first matches how the matrix
is read and how the tiles are written; getting it the other way round on a 3x5
grid gives coordinates that are still in range, so it is worth being deliberate
about.

**Columns must strictly increase along a line.** A payline is evaluated from the
left, one adjacent pair at a time, and "adjacent" only means anything if the
positions are in reel order. Requiring it rejects a line that repeats or
backtracks a reel -- a real possibility as a typo in forty hand-written lines,
and one that would otherwise compare a tile against itself and score a perfect
match.

Nothing here knows about pictures, similarity, or the size of the grid the
coordinates land on. It takes the decoded block and hands back ordered
positions; checking they fit a particular grid is :meth:`PaylineSet.within`,
which the caller invokes once it knows what was split.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from app.utils.reel_grid import position_name

__all__ = [
    "COORDINATES",
    "MIN_POSITIONS",
    "Payline",
    "PaylineError",
    "PaylineSet",
    "Position",
    "read_set",
    "set_names",
]

# The fewest positions a line can have and still be evaluated: with one there is
# no adjacent pair to compare, so a shorter line is a config mistake rather than
# a line that never wins.
MIN_POSITIONS = 2

# How many numbers a position is written with.
COORDINATES = 2


class PaylineError(ValueError):
    """The paylines block is missing, malformed, or describes no usable line."""


def _sort_key(name: str) -> tuple[int, int, str]:
    """Order names numerically when they are numbers, alphabetically otherwise.

    The keys are written as numbers -- ``"5"``, ``"20"``, ``"40"`` for the sets
    and ``"1"`` through ``"40"`` for the lines -- and sorting those as strings
    puts 20 before 5, and line 10 before line 2. A non-numeric name sorts after
    the numbers rather than raising, because a game that names a set
    ``"lines_5"`` is unusual, not broken.
    """
    return (0, int(name), "") if name.isdigit() else (1, 0, name.casefold())


@dataclass(frozen=True)
class Position:
    """One symbol position on the grid, 1-indexed, row first."""

    row: int
    column: int

    @property
    def name(self) -> str:
        """The position as a tile name, e.g. ``r2c1``."""
        return position_name(self.row, self.column)


@dataclass(frozen=True)
class Payline:
    """One winning pattern: the positions it runs through, left to right."""

    name: str
    """The key it was declared under, e.g. ``"1"``. Kept exactly as written."""

    positions: tuple[Position, ...]
    """Ordered by reel, which :func:`read_set` has already verified."""

    @property
    def label(self) -> str:
        """How the line is spoken about: ``Line 1``."""
        return f"Line {self.name}"

    @property
    def steps(self) -> tuple[tuple[Position, Position], ...]:
        """The adjacent pairs, in the order a left-to-right read compares them."""
        return tuple(pairwise(self.positions))


@dataclass(frozen=True)
class PaylineSet:
    """One bet configuration's lines, in the order they are numbered."""

    name: str
    """The key the set was declared under, e.g. ``"5"``."""

    lines: tuple[Payline, ...]

    @property
    def length(self) -> int:
        """How many lines the set pays on, which is usually what it is named."""
        return len(self.lines)

    def within(self, rows: int, columns: int) -> None:
        """Check every position lands on a grid of this shape.

        Separate from parsing, because the shape is not the config block's to
        know: one set is correct for the grid the game declares now and wrong for
        a split written before a sixth reel was added, and only a caller holding
        both can say which of the two it is looking at.

        Raises:
            PaylineError: naming the first line and position that does not fit.
        """
        for line in self.lines:
            for position in line.positions:
                if not 1 <= position.row <= rows:
                    raise PaylineError(
                        f"line {line.name!r} position {position.name} is outside a "
                        f"{rows}x{columns} grid: row {position.row} of {rows}"
                    )
                if not 1 <= position.column <= columns:
                    raise PaylineError(
                        f"line {line.name!r} position {position.name} is outside a "
                        f"{rows}x{columns} grid: column {position.column} of {columns}"
                    )


def _block(value: Any, *, where: str) -> Mapping[str, Any]:
    """Narrow a decoded JSON value to a non-empty object of named entries."""
    if not isinstance(value, Mapping):
        raise PaylineError(f"{where} must be a JSON object")
    if not value:
        raise PaylineError(f"{where} declares no entries")
    for key in value:
        if not isinstance(key, str):
            raise PaylineError(f"{where} must be keyed by name, got {key!r}")
    return value


def _coordinate(value: Any, *, where: str) -> int:
    """One coordinate: a whole number of at least 1.

    ``bool`` is an ``int`` in Python and ``true`` as a row is a mistake rather
    than a 1, so it is rejected the way the rest of this project rejects it. A
    float that is whole (``2.0``, which is what a JSON writer may emit) is
    accepted; ``2.5`` is not.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaylineError(f"{where} must contain only numbers, got {value!r}")
    if float(value) != int(value):
        raise PaylineError(f"{where} must contain whole numbers, got {value!r}")
    number = int(value)
    if number < 1:
        raise PaylineError(f"{where} is 1-indexed, got {number}")
    return number


def _position(value: Any, *, where: str) -> Position:
    """One ``[row, column]`` pair."""
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) != COORDINATES
    ):
        raise PaylineError(f"{where} must be an array of [row, column]")
    return Position(
        row=_coordinate(value[0], where=where),
        column=_coordinate(value[1], where=where),
    )


def _line(name: str, value: Any, *, where: str) -> Payline:
    """One line: its positions, checked to be in reel order."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PaylineError(f"{where} must be an array of [row, column] positions")
    if len(value) < MIN_POSITIONS:
        raise PaylineError(
            f"{where} needs at least {MIN_POSITIONS} positions to be compared, "
            f"got {len(value)}"
        )
    positions = tuple(
        _position(item, where=f"{where}[{index}]") for index, item in enumerate(value)
    )
    for index, (left, right) in enumerate(pairwise(positions)):
        if right.column <= left.column:
            raise PaylineError(
                f"{where} must run left to right: position {index + 2} is "
                f"{right.name}, which is not right of {left.name}"
            )
    return Payline(name=name, positions=positions)


def set_names(block: Any, *, where: str = "paylines") -> tuple[str, ...]:
    """The bet configurations the block declares, in numeric order.

    Reads only the keys, so listing the choices for a panel does not depend on
    all forty lines of every set being well-formed -- a typo in the forty-line
    set should not stop the five-line one being offered.

    Raises:
        PaylineError: if the block is not an object, or declares no sets.
    """
    return tuple(sorted(_block(block, where=where), key=_sort_key))


def read_set(block: Any, name: str, *, where: str = "paylines") -> PaylineSet:
    """Parse one named bet configuration out of the block.

    Raises:
        PaylineError: if the block or the named set is malformed, if there is no
            set by that name, or if any line is not a left-to-right sequence of
            at least two 1-indexed positions.
    """
    sets = _block(block, where=where)
    if name not in sets:
        offered = ", ".join(sorted(sets, key=_sort_key))
        raise PaylineError(f"{where} declares no {name!r} set (it declares: {offered})")

    entries = _block(sets[name], where=f"{where}.{name}")
    lines = tuple(
        _line(line, entries[line], where=f"{where}.{name}.{line}")
        for line in sorted(entries, key=_sort_key)
    )
    return PaylineSet(name=name, lines=lines)
