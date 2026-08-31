"""Interprets the denomination a cabinet is running: its value, its unit, and
the multiplier that turns paytable credits into money on the glass.

Deliberately ignorant of where the two inputs came from -- one is the string the
game's log wrote, the other the paytable id it named -- so the one place a
denomination is *interpreted* is separate from the places it is *found*. That
matters more than usual here: the log is only the current source of the value,
and swapping it for another one should not move any of the arithmetic below.

**The value is a count of cents, and that is measured rather than assumed.** The
log writes ``current denom[2.000]`` with no unit anywhere on the line, and read as
money that would be two dollars. Three things say it is two cents:

* the paytable id on the same line is ``FortuneOx-1103AX-2c-90``, and
  ``gameConfig.cfg`` inside that folder declares ``<MinDenomMultiplier>2</...>``;
* ``BetChangeMsg ... denom: 2.000 units: 40 totalBetValue: 176.000`` pairs with
  meter strips captured in the same session reading a bet of ``$1.76`` in cash
  mode and ``88`` in credits mode -- 88 credits at 2c is 176c is $1.76 -- and that
  folder's ``<MinTotalBet>88</MinTotalBet>`` confirms 88 was the bet;
* a second session at ``denom[1.000]`` has a balance of ``99635`` credits against
  ``$996.35``.

So :data:`CENTS_PER_UNIT` is the whole conversion, and
:attr:`Denomination.money_per_credit` is the only number anything should multiply
or divide by. The raw value is carried beside it because it is what the log and
the operator both say ("a 2c game"), but it is not a rate.

**The unit comes from the paytable id, and is never guessed.** No file states it
in words. The id's trailing ``-2c-`` does, by convention, and every paytable
folder on the reference install that ships a ``gameConfig.cfg`` follows it (1c, 2c,
4c, 5c, 10c, 100c, 200c). An id that does not carry it leaves
``money_per_credit`` at ``None``, which makes a spin's award verdict
``indeterminate``. That is on purpose: the cabinet's supported ladder
(``1,2,5,10,100,200``) looks like conclusive evidence of cents, but a machine
denominated in whole currency units would print a ladder of the same shape, and a
wrong multiplier is out by a factor of a hundred while still looking like a
number -- which reads as a game bug rather than as a parsing assumption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CENTS_PER_UNIT",
    "UNIT_CENT",
    "UNIT_UNKNOWN",
    "Denomination",
    "parse",
    "value_of",
]

# Cents in one unit of currency. The one constant the conversion rests on -- see
# the module docstring for what measured it.
CENTS_PER_UNIT = 100

UNIT_CENT = "cent"
UNIT_UNKNOWN = "unknown"

RESOLVED_FROM_ID = "paytable-id"
RESOLVED_UNRESOLVED = "unresolved"

# The unit segment of a paytable id: `FortuneOx-1103AX-2c-90` -> amount 2, unit c.
# The lookahead is what keeps it off the rest of the id -- `1103AX` cannot match
# because a segment has to end after its single letter, and `-90` carries no
# letter at all. Read with `finditer` and the *last* match taken, so an id that
# grows a segment does not silently start matching an earlier one.
_ID_UNIT = re.compile(r"-(?P<amount>\d+)(?P<unit>[a-z])(?=-|$)", re.IGNORECASE)

# Unit letters a paytable id may carry. Only `c` has ever been observed; the map
# exists so a second one is an entry here rather than a branch somewhere.
_UNITS: dict[str, str] = {"c": UNIT_CENT}


@dataclass(frozen=True)
class Denomination:
    """One resolved denomination, and how it was resolved.

    Carries the evidence as well as the answer, because every field below can
    disagree with another and only saying so lets a reader tell a stale log from
    a mis-parsed id.
    """

    value: float
    """The number the source reported, in :attr:`unit` -- ``2.0`` for a 2c game.
    Not a rate: use :attr:`money_per_credit` for arithmetic."""

    unit: str
    """:data:`UNIT_CENT` or :data:`UNIT_UNKNOWN`, from the paytable id."""

    label: str
    """The denomination as an operator says it: ``2c``. Currency-agnostic on
    purpose -- which currency those cents are in is a property of the meter, not
    of the denomination, and it is read off the glass."""

    money_per_credit: float | None
    """One credit in money -- ``0.02`` for a 2c game. **The only field anything
    should multiply or divide by.** ``None`` when the unit could not be resolved,
    which is a refusal to guess rather than a missing value."""

    declared_multiplier: int | None
    """``gameConfig.cfg``'s own ``MinDenomMultiplier`` for the loaded folder, when
    it was read. The corroboration behind :attr:`agrees`."""

    agrees: bool | None
    """Whether every source that named an amount named the same one -- the
    paytable id's own suffix and the declared multiplier against
    :attr:`value`. ``None`` when nothing was available to check against.
    ``False`` does not change any number here: the reported value still wins,
    because it is the current one and the others are properties of a folder."""

    resolved_from: str
    """:data:`RESOLVED_FROM_ID` or :data:`RESOLVED_UNRESOLVED`."""


def value_of(denomination: str | None) -> float | None:
    """A denomination as its source wrote it, or ``None`` when it is unusable.

    Non-positive is unusable as well as unparseable: the value divides a bet, and
    a zero denomination would raise where a missing one reports honestly.
    """
    if denomination is None:
        return None
    try:
        parsed = float(denomination)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _unit_segment(paytable_id: str | None) -> tuple[int, str] | None:
    """The amount and unit the paytable id carries, if it carries one."""
    if not paytable_id:
        return None
    found = None
    for match in _ID_UNIT.finditer(paytable_id):
        found = match
    if found is None:
        return None
    unit = _UNITS.get(found.group("unit").lower())
    if unit is None:
        return None
    return int(found.group("amount")), unit


def _label(value: float, unit: str) -> str:
    """``2c``, or a bare number when the unit is not known.

    The value is formatted with ``%g`` so ``2.000`` reads as ``2`` -- every
    denomination observed is a whole count of cents, and a trailing ``.000`` on a
    label an operator reads is noise. A fractional one would still print.
    """
    shown = f"{value:g}"
    return f"{shown}c" if unit == UNIT_CENT else shown


def parse(
    denomination: str | None,
    *,
    paytable_id: str | None,
    declared_multiplier: int | None = None,
) -> Denomination | None:
    """Resolve a denomination, or ``None`` when there is no usable value.

    ``denomination`` is the value as its source reported it (the game log's
    ``denom[...]`` today); ``paytable_id`` is what names the unit;
    ``declared_multiplier`` is ``gameConfig.cfg``'s ``MinDenomMultiplier``, used
    only to corroborate.
    """
    value = value_of(denomination)
    if value is None:
        return None

    segment = _unit_segment(paytable_id)
    unit = UNIT_UNKNOWN if segment is None else segment[1]
    money_per_credit = value / CENTS_PER_UNIT if unit == UNIT_CENT else None

    # Every amount that was named, checked against the value. An empty list means
    # nothing was available to check, which is not agreement.
    claimed = [
        amount
        for amount in (segment[0] if segment else None, declared_multiplier)
        if amount is not None
    ]
    agrees = None if not claimed else all(amount == value for amount in claimed)

    return Denomination(
        value=value,
        unit=unit,
        label=_label(value, unit),
        money_per_credit=money_per_credit,
        declared_multiplier=declared_multiplier,
        agrees=agrees,
        resolved_from=RESOLVED_UNRESOLVED if unit == UNIT_UNKNOWN else RESOLVED_FROM_ID,
    )
