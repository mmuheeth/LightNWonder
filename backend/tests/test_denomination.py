"""Interpreting a denomination: the value, its unit, and the rate.

The one thing these tests exist to hold in place is that **the value and the rate
are different numbers**. ``denom[2.000]`` is two cents, so the number an award is
priced with is ``0.02`` -- and reading the value as the rate is out by a factor of
a hundred while still looking like a plausible figure.

The ids and values below are real: they come off the reference install's own
paytable folders and its game log.
"""

from __future__ import annotations

import pytest

from app.utils import denomination as denom


def test_the_rate_is_the_value_in_cents_not_the_value() -> None:
    """The real line: `current denom[2.000] current paytableId[FortuneOx-1103AX-2c-90]`.

    `gameConfig.cfg` in that folder declares `MinDenomMultiplier` 2 and
    `MinTotalBet` 88, and the meter strips of the same session read a bet of
    $1.76 -- 88 credits at 0.02.
    """
    resolved = denom.parse(
        "2.000", paytable_id="FortuneOx-1103AX-2c-90", declared_multiplier=2
    )

    assert resolved is not None
    assert resolved.value == 2.0
    assert resolved.money_per_credit == pytest.approx(0.02)
    assert resolved.unit == denom.UNIT_CENT
    assert resolved.label == "2c"
    assert resolved.agrees is True
    assert resolved.resolved_from == denom.RESOLVED_FROM_ID

    # The figure the fix exists for.
    assert 1.76 / resolved.money_per_credit == pytest.approx(88)


@pytest.mark.parametrize(
    ("value", "paytable_id", "multiplier", "rate", "label"),
    [
        ("1.000", "FortuneOx-1101YX-1c-90", 1, 0.01, "1c"),
        ("2.000", "FortuneOx-1103AX-2c-90", 2, 0.02, "2c"),
        ("4.000", "FortuneOx-11037X-4c-90", 4, 0.04, "4c"),
        ("5.000", "FortuneOx-1102ZX-5c-89", 5, 0.05, "5c"),
        ("10.000", "FortuneOx-11033X-10c-90", 10, 0.10, "10c"),
        ("100.000", "FortuneOx-1102RX-100c-90", 100, 1.00, "100c"),
        ("200.000", "FortuneOx-1101LX-200c-90", 200, 2.00, "200c"),
    ],
)
def test_every_denomination_the_install_ships(
    value: str, paytable_id: str, multiplier: int, rate: float, label: str
) -> None:
    """The whole ladder, because the two-digit and three-digit amounts are where
    a suffix parser goes wrong: `100c` must not read as `10c` or `0c`."""
    resolved = denom.parse(
        value, paytable_id=paytable_id, declared_multiplier=multiplier
    )

    assert resolved is not None
    assert resolved.money_per_credit == pytest.approx(rate)
    assert resolved.label == label
    assert resolved.agrees is True


def test_credits_convert_to_the_cash_the_meter_actually_showed() -> None:
    """Both directions, against balances read off real saved strips: 99635
    credits at 1c is $996.35, and 49883 at 2c is $997.66."""
    one_cent = denom.parse("1.000", paytable_id="FortuneOx-1101YX-1c-90")
    two_cent = denom.parse("2.000", paytable_id="FortuneOx-1103AX-2c-90")
    assert one_cent is not None and one_cent.money_per_credit is not None
    assert two_cent is not None and two_cent.money_per_credit is not None

    assert 99635 * one_cent.money_per_credit == pytest.approx(996.35)
    assert 49883 * two_cent.money_per_credit == pytest.approx(997.66)
    assert 996.35 / one_cent.money_per_credit == pytest.approx(99635)


def test_an_id_with_no_unit_leaves_the_rate_unresolved_rather_than_guessed() -> None:
    """The cabinet's ladder (1,2,5,10,100,200) looks like proof of cents, but a
    machine denominated in whole currency units prints the same shape -- and a
    multiplier wrong by 100x reads as a game bug, not a parsing assumption."""
    resolved = denom.parse("2.000", paytable_id="FortuneOx-1106HX-LATAM")

    assert resolved is not None
    assert resolved.value == 2.0
    assert resolved.money_per_credit is None
    assert resolved.unit == denom.UNIT_UNKNOWN
    assert resolved.label == "2"
    assert resolved.resolved_from == denom.RESOLVED_UNRESOLVED


def test_a_disagreement_is_reported_and_the_reported_value_still_wins() -> None:
    """The value is the *current* denomination; the id and the declared
    multiplier are properties of a folder. So a mismatch is a stale reading
    somewhere and is worth saying, but it does not get to change the rate."""
    resolved = denom.parse(
        "1.000", paytable_id="FortuneOx-1103AX-2c-90", declared_multiplier=2
    )

    assert resolved is not None
    assert resolved.agrees is False
    assert resolved.value == 1.0
    assert resolved.money_per_credit == pytest.approx(0.01)


def test_nothing_to_corroborate_with_is_not_agreement() -> None:
    resolved = denom.parse("1.000", paytable_id=None)

    assert resolved is not None
    assert resolved.agrees is None
    assert resolved.money_per_credit is None


def test_the_id_alone_can_corroborate_without_a_declared_multiplier() -> None:
    """A folder shipping no gameConfig.cfg still has its own name."""
    resolved = denom.parse("2.000", paytable_id="FortuneOx-1103AX-2c-90")

    assert resolved is not None
    assert resolved.agrees is True
    assert resolved.declared_multiplier is None


@pytest.mark.parametrize("value", [None, "", "abc", "0", "0.000", "-1.000"])
def test_an_unusable_value_is_no_denomination_at_all(value: str | None) -> None:
    """Zero and negative are unusable as well as unparseable -- the rate divides
    a bet, so a zero would raise where a missing one reports honestly."""
    assert denom.parse(value, paytable_id="FortuneOx-1103AX-2c-90") is None
    assert denom.value_of(value) is None


def test_the_unit_segment_is_taken_from_the_end_not_the_middle() -> None:
    """`1103AX` is a segment ending in a letter too. Only the amount+letter form
    followed by a separator counts, and the last one wins."""
    resolved = denom.parse("2.000", paytable_id="FortuneOx-1103AX-2c-90")
    assert resolved is not None
    assert resolved.label == "2c"
    assert resolved.agrees is True
