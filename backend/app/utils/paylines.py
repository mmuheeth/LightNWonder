"""Read the ``paylines`` block of a game config: named bet configurations
(``"5"``, ``"20"``, ``"40"``), each a set of lines of ``[row, column]``
positions (1-indexed, row first, same numbering as a tile's name). A set is
looked up by name since line 4 of the five-line set isn't line 4 of the
forty-line set. Columns must strictly increase along a line -- a payline is
compared left to right one adjacent pair at a time, and a repeated/backtracked
reel would otherwise compare a tile against itself and score a perfect match.
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
    """Order names numerically when they are numbers (sorting as strings would
    put 20 before 5), alphabetically after that otherwise."""
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
        """Check every position lands on a grid of this shape. Separate from
        parsing since the grid shape isn't the config block's to know -- a set
        can be correct for the game's current grid and wrong for an old split.
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
    """One coordinate: a whole number of at least 1. A whole float (``2.0``,
    as a JSON writer may emit) is accepted; ``2.5`` is not."""
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
    """The bet configurations the block declares, in numeric order. Reads only
    the keys, so a typo in the forty-line set can't stop the five-line one
    from being offered."""
    return tuple(sorted(_block(block, where=where), key=_sort_key))


def read_set(block: Any, name: str, *, where: str = "paylines") -> PaylineSet:
    """Parse one named bet configuration out of the block."""
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
