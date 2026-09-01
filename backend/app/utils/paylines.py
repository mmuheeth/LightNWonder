"""Read the ``paylines`` block of a game config, and read one line of symbol
codes by the rule a game pays it.

The block is named bet configurations (``"5"``, ``"20"``, ``"40"``), each a set
of lines of ``[row, column]`` positions (1-indexed, row first, same numbering as
a tile's name). A set is looked up by name since line 4 of the five-line set
isn't line 4 of the forty-line set. Columns must strictly increase along a line
-- a payline is compared left to right one adjacent pair at a time, and a
repeated/backtracked reel would otherwise compare a tile against itself and
score a perfect match.

:func:`read_run` is the other half: given the codes a classifier read off one
line's tiles, how far the leading run reaches and what it pays as. **It is not a
pairwise comparison, and that is the whole point of it being here.** A wild
stands in for whatever the *run* is paying as, so ``AA WC BB`` is a run of two
(the wild is an Ox) and never a run of three -- yet every adjacent pair in it
"matches" when each is judged on its own. A line is therefore read with the run's
symbol carried along it, which is what :class:`WildRule` and :class:`SymbolRun`
exist to express.
"""

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
    """Which code substitutes for which when a line is read.

    Two things, kept apart because they fail differently: :attr:`code` is the
    wild itself and :attr:`replaces` is the *closed* list of symbols it stands in
    for. The list is closed on purpose -- a game's scatters and feature symbols
    are paid by counting them anywhere on the grid, not along a line, so a wild
    landing beside two orbs is a wild beside two orbs and not three orbs. Reading
    the wild as "matches anything" is the mistake this shape prevents.

    An empty :attr:`replaces` is :data:`NO_WILDS`: the game declared no
    substitution, so the wild is an ordinary symbol compared by equality. That is
    also the behaviour of every game config written before the block existed,
    which is why nothing here has to be conditional at the call site.
    """

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
        """Whether a code read off a tile is the wild.

        False for every code when the game declares no substitution: a wild that
        stands in for nothing behaves exactly like the symbol it is, and saying
        otherwise would only make a run stop for a reason nothing measured.
        """
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
    """How far one line's leading run reaches, and what it pays as.

    ``codes`` is the code a classifier read off each tile of the line, left to
    right, ``None`` for a tile it was not sure enough of to name. The run stops
    at the first position that cannot join it, and every position after that is
    ignored -- a line pays its *leading* run, so three alike on reels 3, 4 and 5
    pay nothing when reels 1 and 2 differ.

    A position joins when:

    * it is the code the run is already paying as; or
    * it is the wild, and the wild may stand in for that code; or
    * the run has been wild all the way here and the wild may stand in for this
      code, which is the position that *names* the run.

    An unnamed tile joins nothing, in either direction: a classifier below its
    floor said "I could not tell", and twice over that is not a run. So a line
    whose first tile is unnamed covers exactly that one position and pays
    nothing, rather than being credited with a run nothing measured.

    The rule is stateful along the line and cannot be decomposed into pairs. With
    ``AA WC BB`` every adjacent pair is a match on its own -- the wild is an Ox
    beside the Ox and a Pisces beside the Pisces -- and the line is still a run of
    two, because a wild is *one* symbol and cannot be both.
    """
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
        """The positions as tile names, which is what a read is given.

        A line's *pairs* are deliberately not offered here. Since the wild, a
        read is not a sequence of independent pair comparisons -- it carries the
        run's symbol along the line -- so a property handing out adjacent pairs
        would invite exactly the pairwise implementation :func:`read_run` exists
        to replace.
        """
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
