"""Interprets the denomination a cabinet is running: its value, its unit, and the
multiplier that turns paytable credits into money on the glass."""

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
    """One resolved denomination, and how it was resolved."""

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
    """A denomination as its source wrote it, or ``None`` when it is unusable."""
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
    """``2c``, or a bare number when the unit is not known."""
    shown = f"{value:g}"
    return f"{shown}c" if unit == UNIT_CENT else shown


def parse(
    denomination: str | None,
    *,
    paytable_id: str | None,
    declared_multiplier: int | None = None,
) -> Denomination | None:
    """Resolve a denomination, or ``None`` when there is no usable value."""
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
