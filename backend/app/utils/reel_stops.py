"""Turns a spin's reel stops into the symbols that were on screen.

The game writes where each reel landed
(``ReelSet.SetStops(ReelsStopData): [25,134,11,58,163]`` --
:data:`app.utils.game_log.REEL_STOPS`), and ``math.xml`` says what is at every
stop of every strip. Between them the grid is *known*, not measured -- which is
exactly why nothing here decides what paid.

**This module names symbols; it does not recognise wins.** The winning pattern
is recognised from the picture, by cosine similarity between tiles
(:mod:`app.services.paylines`), because that is what validates the *game* rather
than restating its own arithmetic back to it -- a payline checker that read the
answer out of the log would agree with the log by construction and could never
catch a reel drawing the wrong symbol. What the log's stops are for is the
question similarity cannot answer: *which* symbol a matching run is made of, so
the run can be priced against the right combo.

Deliberately ignorant of who consumes it, like every other ``app/utils`` module:
it takes stops and strips as plain sequences and knows nothing about paytables,
screenshots or configs.

**The anchor is the one thing these files do not state.** A stop index is one
number per reel and a reel shows several rows, so whether that number is the
*top*, *middle* or *bottom* visible row is a convention -- and picking the wrong
one shifts every symbol by a row, which reads as a plausible grid of the wrong
game. Rather than assume, :func:`resolve` derives all three and scores each
against the similarity measurements already taken from the picture: the right
anchor is the one whose predicted equal-pairs match the measured ones. The
score comes back with the grid, so an ambiguous spin says so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.utils.reel_grid import position_name

__all__ = [
    "ANCHORS",
    "Anchor",
    "ReelStopError",
    "Resolution",
    "SymbolGrid",
    "build",
    "parse_stops",
    "resolve",
]

Anchor = Literal["top", "middle", "bottom"]

ANCHORS: tuple[Anchor, ...] = ("top", "middle", "bottom")
"""Every convention a stop index could follow, in the order they are tried."""


class ReelStopError(ValueError):
    """The stops and the strips do not describe a grid."""


def parse_stops(raw: str) -> tuple[int, ...]:
    """Read the bracketed list the game logs, e.g. ``25,134,11,58,163``."""
    stops: list[int] = []
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        try:
            stops.append(int(text))
        except ValueError as exc:
            raise ReelStopError(f"reel stop {text!r} is not a whole number") from exc
    if not stops:
        raise ReelStopError("the logged reel stops are empty")
    return tuple(stops)


def _offset(anchor: Anchor, rows: int) -> int:
    """How far above the stop the top visible row sits.

    ``middle`` rounds down for an even row count, which is the only choice that
    keeps a 3-row reel centred on its stop.
    """
    if anchor == "top":
        return 0
    if anchor == "bottom":
        return rows - 1
    return (rows - 1) // 2


@dataclass(frozen=True)
class SymbolGrid:
    """What was on screen, by symbol code, under one reading of the stops."""

    anchor: Anchor
    """Which row of each reel the logged stop was taken to be."""

    rows: int
    columns: int
    stops: tuple[int, ...]
    codes: Mapping[str, str]
    """Symbol code per position name (``r1c1``), in the grid's own naming."""

    def code(self, position: str) -> str | None:
        """The symbol at one position, or ``None`` if it is off the grid."""
        return self.codes.get(position)

    def along(self, positions: Sequence[str]) -> list[str | None]:
        """The symbols a line runs through, left to right."""
        return [self.codes.get(position) for position in positions]

    def leading_run(self, positions: Sequence[str]) -> int:
        """How many positions from the left carry the same code.

        The maths' own answer to what similarity measures off the picture, and
        the reason both are reported: they should agree, and a spin where they do
        not is the interesting one. Wilds are *not* substituted -- this counts
        identical codes, so that the two numbers are measuring the same thing.
        """
        codes = self.along(positions)
        if not codes or codes[0] is None:
            return 0
        first = codes[0]
        run = 1
        for code in codes[1:]:
            if code != first:
                break
            run += 1
        return run if run > 1 else 0

    def matrix(self) -> list[list[str]]:
        """The grid row-major, for showing beside the picture it describes."""
        return [
            [
                self.codes.get(position_name(row, column), "")
                for column in range(1, self.columns + 1)
            ]
            for row in range(1, self.rows + 1)
        ]


def build(
    stops: Sequence[int],
    strips: Sequence[Sequence[str]],
    *,
    rows: int,
    anchor: Anchor,
) -> SymbolGrid:
    """Place the symbols one reading of the stops puts on screen.

    A strip is a loop, so the index wraps -- a stop near the end of a strip shows
    its first symbols underneath. A negative offset wraps the same way, which is
    what makes ``middle`` and ``bottom`` work at stop 0.
    """
    if rows < 1:
        raise ReelStopError(f"a grid needs at least one row, got {rows}")
    if len(stops) != len(strips):
        raise ReelStopError(
            f"the log reported {len(stops)} reel stop(s) but the maths declares "
            f"{len(strips)} reel(s)"
        )
    if not strips:
        raise ReelStopError("the maths declares no reels")

    offset = _offset(anchor, rows)
    codes: dict[str, str] = {}
    for reel, (stop, strip) in enumerate(zip(stops, strips, strict=True), start=1):
        length = len(strip)
        if length == 0:
            raise ReelStopError(f"reel {reel}'s strip carries no symbols")
        for row in range(1, rows + 1):
            index = (stop - offset + row - 1) % length
            codes[position_name(row, reel)] = strip[index]

    return SymbolGrid(
        anchor=anchor,
        rows=rows,
        columns=len(strips),
        stops=tuple(stops),
        codes=codes,
    )


@dataclass(frozen=True)
class Resolution:
    """One grid, and how well it agreed with what the picture measured."""

    grid: SymbolGrid
    agreed: int
    """Pairs whose predicted sameness matched the measured sameness."""

    compared: int
    """Pairs there were to agree about. 0 when nothing was measured."""

    scores: tuple[tuple[Anchor, int], ...]
    """Every anchor considered and what it scored, so a near-tie is visible."""

    @property
    def decided(self) -> bool:
        """Whether the anchor was chosen by evidence rather than by falling back.

        False when nothing was measured to compare against, or when a second
        anchor scored just as well -- in which case the grid is the first of the
        equals and its row alignment is a guess.
        """
        if self.compared == 0:
            return False
        best = max(score for _, score in self.scores)
        return sum(1 for _, score in self.scores if score == best) == 1


def agreement(grid: SymbolGrid, pairs: Sequence[tuple[str, str, bool]]) -> int:
    """How many measured pairs this grid predicts correctly.

    ``pairs`` is ``(left, right, measured_same)`` as similarity found them. A
    pair either grid position is missing from is skipped rather than counted
    against -- it is not evidence either way.
    """
    score = 0
    for left, right, measured in pairs:
        first, second = grid.code(left), grid.code(right)
        if first is None or second is None:
            continue
        if (first == second) == measured:
            score += 1
    return score


def resolve(
    stops: Sequence[int],
    strips: Sequence[Sequence[str]],
    *,
    rows: int,
    pairs: Sequence[tuple[str, str, bool]] = (),
    anchor: Anchor | None = None,
) -> Resolution:
    """Read the stops into a grid, choosing the anchor the picture agrees with.

    ``anchor`` pins the convention and skips the choosing, for a game whose
    alignment is known. Otherwise every anchor is built and scored against
    ``pairs``; ties keep the first, and :attr:`Resolution.decided` says so.
    """
    if anchor is not None:
        grid = build(stops, strips, rows=rows, anchor=anchor)
        return Resolution(
            grid=grid,
            agreed=agreement(grid, pairs),
            compared=len(pairs),
            scores=((anchor, agreement(grid, pairs)),),
        )

    built = [
        (candidate, build(stops, strips, rows=rows, anchor=candidate))
        for candidate in ANCHORS
    ]
    scores = tuple((candidate, agreement(grid, pairs)) for candidate, grid in built)
    best = max(score for _, score in scores)
    chosen = next(
        grid for (candidate, grid) in built if dict(scores)[candidate] == best
    )
    return Resolution(grid=chosen, agreed=best, compared=len(pairs), scores=scores)
