"""Read the ``paylines`` block of a game config, and read one line of symbol codes by
the rule a game pays it."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from app.utils.reel_grid import position_name

__all__ = [
    "COORDINATES",
    "MIN_POSITIONS",
    "NO_WILDS",
    "WILD_SYMBOL",
    "Payline",
    "PaylineError",
    "PaylineSet",
    "Position",
    "SymbolRun",
    "WildRule",
    "read_run",
    "read_set",
    "set_names",
]

# The fewest positions a line can have and still be evaluated: with one there is
# no adjacent pair to compare, so a shorter line is a config mistake rather than
# a line that never wins.
MIN_POSITIONS = 2

# How many numbers a position is written with.
COORDINATES = 2

# The wild's own symbol code. One constant rather than a per-game setting: every
# cabinet in this family writes its wild as `WC` (FortuneOx's `math.xml` names it
# in `WildSymbolList`, and the config's `wild_card_replacement` list is the same
# nine codes). What a game *does* declare is what it stands in for, since that is
# the half that differs -- see :class:`WildRule`.
WILD_SYMBOL = "WC"


class PaylineError(ValueError):
    """The paylines block is missing, malformed, or describes no usable line."""


# --- wilds ----------------------------------------------------------------


@dataclass(frozen=True)
class WildRule:
    """Which code substitutes for which when a line is read."""

    code: str
    replaces: frozenset[str]

    @classmethod
    def of(cls, replaces: Iterable[str], *, code: str = WILD_SYMBOL) -> WildRule:
        """The rule for a wild standing in for these codes, upper-cased."""
        return cls(
            code=code.strip().upper(),
            replaces=frozenset(entry.strip().upper() for entry in replaces if entry),
        )

    @property
    def active(self) -> bool:
        """Whether this game substitutes at all."""
        return bool(self.replaces)

    def is_wild(self, symbol: str | None) -> bool:
        """Whether a code read off a tile is the wild."""
        return self.active and symbol == self.code

    def stands_in_for(self, symbol: str | None) -> bool:
        """Whether the wild may be read as this code."""
        return symbol is not None and symbol in self.replaces


NO_WILDS = WildRule(code=WILD_SYMBOL, replaces=frozenset())
"""No substitution: every code is compared by equality, wild included."""


@dataclass(frozen=True)
class SymbolRun:
    """One line's leading run, read left to right with wilds substituted."""

    covered: int
    """Positions from the left the run reached -- 1 for a line that got nowhere,
    0 only for a line with no positions at all. Not the pay count: a run of one
    position pays nothing, which is the caller's floor to apply."""

    symbol: str | None
    """The code the run pays as: the symbol the wilds stood in for, the wild's
    own code when every covered position was wild, or ``None`` when the first
    tile was never named and there was nothing to carry."""

    leading_wilds: int
    """How many of the run's *leading* positions were the wild itself, which is a
    second combo the caller's paytable may price -- four wilds then an Ox is five
    Ox or four wilds, and the two are not the same money."""

    line_symbols: tuple[str | None, ...]
    """What the run was paying as when it reached each position, ``None`` past
    where it stopped. A wild-led run reports the wild here until the symbol it
    stood in for lands, because until then that is genuinely what it is."""


def read_run(codes: Sequence[str | None], wilds: WildRule = NO_WILDS) -> SymbolRun:
    """How far one line's leading run reaches, and what it pays as."""
    if not codes:
        return SymbolRun(covered=0, symbol=None, leading_wilds=0, line_symbols=())

    line_symbols: list[str | None] = [None] * len(codes)
    chosen: str | None = None  # the non-wild code the run pays as, once one lands
    wild_run = 0  # leading positions held by the wild itself
    covered = 1  # position 0 always starts the read, named or not

    first = codes[0]
    if first is None:
        # Nothing to carry, so nothing can join it: the read starts and stops.
        return SymbolRun(
            covered=1, symbol=None, leading_wilds=0, line_symbols=tuple(line_symbols)
        )
    if wilds.is_wild(first):
        wild_run = 1
    else:
        chosen = first

    def paying_as() -> str | None:
        """What the run is worth reading as at this point in the line."""
        return chosen if chosen is not None else (wilds.code if wild_run else None)

    line_symbols[0] = paying_as()

    for index in range(1, len(codes)):
        code = codes[index]
        if code is None:
            break
        if wilds.is_wild(code):
            if chosen is None:
                # Still wild all the way, so this simply extends the wild run.
                wild_run += 1
            elif not wilds.stands_in_for(chosen):
                # A wild does not stand in for a scatter or a feature symbol, so
                # a line of them stops at one rather than being extended by it.
                break
        elif chosen is None:
            # Wild all the way here, so this code is what the run has been
            # paying as -- provided the wild is allowed to have been it.
            if not wilds.stands_in_for(code):
                break
            chosen = code
        elif code != chosen:
            break
        covered += 1
        line_symbols[index] = paying_as()

    return SymbolRun(
        covered=covered,
        symbol=paying_as(),
        leading_wilds=wild_run,
        line_symbols=tuple(line_symbols),
    )


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
    def names(self) -> tuple[str, ...]:
        """The positions as tile names, which is what a read is given."""
        return tuple(position.name for position in self.positions)


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
        """Check every position lands on a grid of this shape."""
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
    """The bet configurations the block declares, in numeric order. Reads only the keys,
    so a typo in the forty-line set can't stop the five-line one from being offered."""
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
