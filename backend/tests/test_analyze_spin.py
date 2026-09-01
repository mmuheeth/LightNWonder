"""Pricing a spin: the step between "these symbols landed" and "this is owed".

Everything else about Analyze Spin is orchestration over five host-specific
integrations, so it is not what this module covers. What *is* here is the part
that turns a reading into money, because it is arithmetic and because it changed:
the run and the symbol on every line now come out of one reading of the picture
(the image classifier's codes), where they used to come out of two sources that
could disagree -- cosine similarity for the run, the game's logged reel stops for
the symbol.

Three separations the tests below exist to hold in place:

* **A run is not a win.** ``paying`` is two or more like symbols; ``awarded`` is
  whether the paytable pays for them. A run of two of a symbol paying from three
  is a real run and no credits, and it says so on ``note``.
* **A credit is not a run length.** ``pays`` is how many symbols matched and
  ``credits`` is what they earn, and one word for both is how a run of five gets
  read as five credits.
* **Nothing here reads the game's log.** The award is priced from the paytable
  and the picture. A checker that took the run from the log would agree with the
  game by construction and could never catch a reel drawing the wrong symbol.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.schemas.analyze_spin import (
    SpinMeterReading,
    SpinMeterValidation,
    SpinOutcome,
    SpinPaylineValidation,
    SpinVerdict,
)
from app.schemas.meter import MeterMode
from app.schemas.paylines import (
    PaylineCheckResult,
    PaylineLine,
    PaylineMethod,
    PaylineSource,
    PaylineStats,
    PaylineStep,
)
from app.schemas.paytable import (
    BetConfigInfo,
    DenominationInfo,
    GameMathInfo,
    MathDefaultsInfo,
    PaylineComboInfo,
    PaylinePayRow,
    PaytableSource,
    PaytableView,
    SymbolInfo,
    WinGeometryInfo,
)
from app.services import analyze_spin as spin_service
from app.services import image_classifier as classifier_service
from app.utils import denomination as denomination_util
from tests.asserts import assert_failure, assert_success

API = "/api/analyze-spin"

# Run lengths the maths pays at, longest first -- the order `pay_lengths` is
# declared in and `pay_table` rows are indexed by.
PAY_LENGTHS = [5, 4, 3, 2]

# Two symbols with different shapes of row, because the interesting cases are
# about the shape: AA pays from two, BB only from three. A run of two BB is
# therefore a real run the maths does not pay, which is the case a "count the
# matches" implementation gets wrong.
PAY_TABLE = [
    PaylinePayRow(codes=["AA"], names=["ACE"], values=[100.0, 50.0, 20.0, 5.0]),
    PaylinePayRow(codes=["BB"], names=["BELL"], values=[80.0, 40.0, 10.0, None]),
]


PAYTABLE_ID = "FortuneOx-1101YX-1c-90"


def resolved_denomination(
    value: str | None, paytable_id: str, multiplier: int | None
) -> DenominationInfo | None:
    """The denomination as the paytable service would report it.

    Built through the real parser rather than by hand, so a fixture cannot
    interpret a denomination differently from production -- which is exactly the
    mistake this module's own fixture used to encode, declaring `0.01` where the
    log writes `1.000`.
    """
    parsed = denomination_util.parse(
        value, paytable_id=paytable_id, declared_multiplier=multiplier
    )
    if parsed is None:
        return None
    return DenominationInfo(
        value=parsed.value,
        unit=parsed.unit,
        label=parsed.label,
        money_per_credit=parsed.money_per_credit,
        declared_multiplier=parsed.declared_multiplier,
        agrees=parsed.agrees,
        resolved_from=parsed.resolved_from,
    )


def paytable(
    *,
    denomination: str | None = "1.000",
    lines: int = 5,
    paytable_id: str = PAYTABLE_ID,
    declared_multiplier: int | None = 1,
    unit_cost: int | None = 88,
    ladder: list[int] | None = None,
) -> PaytableView:
    """A paytable view carrying only what pricing a line actually reads.

    The denomination defaults to `"1.000"` because that is what the game's log
    actually writes for the `-1c-` paytable named below -- a count of cents. It
    used to default to `"0.01"`, a money-per-credit value no log ever writes, and
    that is what hid a hundredfold error in the conversion.
    """
    combos = [
        PaylineComboInfo(
            combo_id=index,
            combo_set="base",
            value=value,
            symbols=[code] * length + ["ANY"] * (5 - length),
            names=[None] * 5,
            match_length=length,
        )
        for index, (code, length, value) in enumerate(
            [
                ("AA", 5, 100.0),
                ("AA", 4, 50.0),
                ("AA", 3, 20.0),
                ("AA", 2, 5.0),
                ("BB", 5, 80.0),
                ("BB", 4, 40.0),
                ("BB", 3, 10.0),
            ],
            start=1,
        )
    ]
    return PaytableView(
        game="FortuneOx",
        label="Fortune Ox",
        paytable_id=paytable_id,
        directory=f"C:/game/GameConfig/{paytable_id}",
        source=PaytableSource(origin="log", denomination=denomination),
        denomination=resolved_denomination(
            denomination, paytable_id, declared_multiplier
        ),
        available=[paytable_id],
        math=GameMathInfo(
            path="C:/game/.../math.xml",
            defaults=MathDefaultsInfo(),
            symbols=[
                SymbolInfo(
                    code="AA",
                    name="ACE",
                    role="Normal",
                    reel_stops=10,
                    total_stops=10,
                    strips=5,
                ),
                SymbolInfo(
                    code="BB",
                    name="BELL",
                    role="Normal",
                    reel_stops=10,
                    total_stops=10,
                    strips=5,
                ),
            ],
            reel_strip_sets=[],
            reel_strips=[],
            payline_combos=combos,
            pay_lengths=PAY_LENGTHS,
            pay_table=PAY_TABLE,
            scatter_combos=[],
            paytables=[],
        ),
        win_geometry=WinGeometryInfo(
            path="C:/game/GameConfig/winGeometry.xml",
            payline_set_id=str(lines),
            resolved_from="game_config",
            line_count=lines,
            sets=[],
            paylines=[],
        ),
        # 88 a spin over FortuneOx's real rungs. The cost is deliberately not
        # divisible by the line count -- 88 over 40 lines is 2.2 -- because the
        # multiplier is the bet per *unit* and never the stake on a line, and a
        # fixture where the two coincided would not tell them apart.
        bet_config=BetConfigInfo(
            ladder=[1, 2, 3, 5, 10] if ladder is None else ladder,
            unit_cost=unit_cost,
        ),
    )


def check(*lines: tuple[str, list[str | None]]):
    """A finished payline check over symbol codes, built from ``(name, codes)``.

    ``codes`` is the code at each of five positions, so a line is written the way
    it reads on the reels. The run is derived here the way
    :mod:`app.services.paylines` derives it -- leading, stopping at the first
    unequal or unnamed pair -- rather than asserted, so these tests are about the
    *pricing* of a run and not about the counting of one.
    """
    built = []
    for index, (name, codes) in enumerate(lines):
        positions = [f"r2c{column}" for column in range(1, len(codes) + 1)]
        steps = []
        running = True
        run = 0
        break_position = None
        for left in range(len(codes) - 1):
            matched = codes[left] is not None and codes[left] == codes[left + 1]
            steps.append(
                PaylineStep(
                    left=positions[left],
                    right=positions[left + 1],
                    left_symbol=codes[left],
                    right_symbol=codes[left + 1],
                    matched=matched,
                    counted=running,
                )
            )
            if running:
                if matched:
                    run += 1
                else:
                    running = False
                    break_position = positions[left + 1]
        pays = run + 1 if run else 0
        built.append(
            PaylineLine(
                name=name,
                label=f"Line {name}",
                positions=positions,
                pays=pays,
                paying=pays >= 2,
                matched_positions=positions[:pays],
                symbols=list(codes),
                color=f"#{index:06x}",
                steps=steps,
                break_position=break_position,
            )
        )
    return PaylineCheckResult(
        game="FortuneOx",
        set="geometry-5",
        method=PaylineMethod.SYMBOL,
        threshold=None,
        source=PaylineSource(
            split="screenshot-1",
            written_at=datetime(2026, 8, 28, 12, 0, 0),
            rows=3,
            columns=5,
            tile_width=40,
            tile_height=100,
            width=200,
            height=300,
        ),
        summary="",
        lines=built,
        stats=PaylineStats(
            lines=len(built),
            paying=sum(1 for one in built if one.paying),
            comparisons=0,
            matches=0,
            best_pays=max((one.pays for one in built), default=0),
        ),
        output_dir="C:/captures/grid/screenshot-1/paylines",
        output_file="geometry-5.png",
    )


def award(*lines: tuple[str, list[str | None]], bet_per_unit: int | None = 1):
    """Every line of one check, priced at ``bet_per_unit`` credits a unit.

    Defaults to 1, where an award and the paytable row behind it are the same
    number -- which is what makes the cases below about the *pricing* of a run
    rather than about the multiplier. The rung is varied where that is the
    point.
    """
    return spin_service._award(paytable(), check(*lines), {}, bet_per_unit)


def one(*codes: str | None, bet_per_unit: int | None = 1):
    """The single award of a one-line check reading ``codes``."""
    return award(("1", list(codes)), bet_per_unit=bet_per_unit)[0]


def validation_for(awards) -> SpinPaylineValidation:
    """A finished payline validation carrying those awards, as the paylines step
    leaves it for the meter step to re-price."""
    return SpinPaylineValidation(
        frame="b.png",
        paytable_id=PAYTABLE_ID,
        paytable_origin="log",
        summary=spin_service._summarise_awards(awards),
        lines=awards,
    )


# --- pricing a run --------------------------------------------------------


def test_five_of_one_symbol_is_priced_from_its_combo() -> None:
    result = one("AA", "AA", "AA", "AA", "AA")

    assert result.pays == 5
    assert result.symbol == "AA"
    assert result.symbol_name == "ACE"
    assert result.credits == 100.0
    assert result.awarded is True
    assert result.combo_symbols == ["AA"] * 5
    assert result.note is None


def test_a_shorter_run_is_priced_at_its_own_length() -> None:
    """The award is the (symbol, run length) pair and nothing else, so a run of
    three of the same symbol is that symbol's three-long value."""
    assert one("AA", "AA", "AA", "BB", "BB").credits == 20.0
    assert one("BB", "BB", "BB", "AA", "AA").credits == 10.0


def test_the_leading_run_is_what_pays_not_the_matches_anywhere() -> None:
    """Three of a symbol at reels 3, 4 and 5 pay nothing when reels 1 and 2
    differ -- the one thing about a payline that is easy to implement as "count
    the matches" and be wrong about."""
    result = one("AA", "BB", "AA", "AA", "AA")

    assert result.pays == 0
    assert result.paying is False
    assert result.awarded is False
    assert result.credits is None


def test_a_run_the_maths_does_not_pay_is_a_run_and_not_a_win() -> None:
    """BB's row starts at three, so two of it is real and earns nothing.

    Reported as a cancelled run rather than as no run: ``paying`` stays true,
    ``awarded`` is false, and ``note`` names the length the symbol pays from --
    which is the difference between a verdict and a bare refusal.
    """
    result = one("BB", "BB", "AA", "AA", "AA")

    assert result.pays == 2
    assert result.paying is True
    assert result.awarded is False
    assert result.credits is None
    assert result.min_pay_length == 3
    assert result.note is not None
    assert "pays from 3" in result.note


def test_a_cancelled_run_keeps_its_evidence_and_loses_its_picture() -> None:
    """The codes on ``steps`` are what makes a cancelled run checkable; its own
    tracing over the reels is a claim of a win the numbers just withdrew."""
    result = one("BB", "BB", "AA", "AA", "AA")

    assert result.image_data is None
    assert [(step.left_symbol, step.right_symbol) for step in result.steps] == [
        ("BB", "BB"),
        ("BB", "AA"),
        ("AA", "AA"),
        ("AA", "AA"),
    ]
    # No score anywhere: these tiles were compared by name.
    assert all(step.similarity is None for step in result.steps)


# --- a tile nothing could name -------------------------------------------


def test_a_line_through_an_unnamed_tile_stops_there() -> None:
    """A classifier below its floor said nothing about that tile, and a run
    counted through it would be a pay nothing measured."""
    result = one("AA", "AA", None, "AA", "AA")

    assert result.pays == 2
    assert result.credits == 5.0
    assert result.symbols == ["AA", "AA", None, "AA", "AA"]


def test_two_unnamed_tiles_in_front_pay_nothing() -> None:
    result = one(None, None, "AA", "AA", "AA")

    assert result.pays == 0
    assert result.awarded is False
    assert result.symbol is None


# --- the sentence and the total ------------------------------------------


def test_the_summary_prices_awarded_lines_and_leaves_cancelled_ones_out() -> None:
    """ "Pays" is credits, never the run length -- and a run the maths does not
    pay is not part of what the spin owes."""
    awards = award(
        ("1", ["AA"] * 5),
        ("2", ["BB", "BB", "AA", "AA", "AA"]),
        ("3", ["BB", "BB", "BB", "AA", "AA"]),
    )

    assert spin_service._summarise_awards(awards) == "Line 1 pays 100, Line 3 pays 10"


def test_nothing_awarded_says_so() -> None:
    assert spin_service._summarise_awards(
        award(("1", ["AA", "BB", "CC", "AA", "BB"]))
    ) == ("No line pays")


def meter(
    *,
    bet: float | None,
    win: float | None,
    mode: MeterMode = MeterMode.CASH,
    rate: float | None = 0.01,
) -> SpinMeterValidation:
    """A cash-meter validation carrying only the two figures pricing reads.

    ``mode`` matters to pricing and not only to display: a credit meter is already
    counting the thing the paytable is denominated in, so it converts by not
    converting.

    The readings go through the production conversion rather than declaring their
    own ``credits``/``cash`` blocks, so a fixture cannot express a figure in both
    units differently from the way a real run would. ``rate`` defaults to the 0.01
    the default :func:`paytable` resolves to.
    """
    readings = [
        SpinMeterReading(
            frame=spin_service.FRAME_INITIAL,
            label="Before the spin",
            file_name="a.png",
            balance=100.0,
            bet=bet,
        ),
        SpinMeterReading(
            frame=spin_service.FRAME_OUTCOME,
            label="Result on screen",
            file_name="b.png",
            balance=100.0,
            win=win,
        ),
    ]
    return SpinMeterValidation(
        mode=mode,
        readings=[
            spin_service._in_both_units(reading, mode, rate) for reading in readings
        ],
        verdict=SpinVerdict.PASSED,
    )


# --- both units ------------------------------------------------------------


def test_a_cash_meter_reports_its_figures_in_credits_too() -> None:
    """The strip drew money; the paytable speaks credits. Both are on the
    reading, so nobody downstream has to do the division -- which is the one that
    was got wrong."""
    validation = meter(bet=1.76, win=1.00, mode=MeterMode.CASH, rate=0.02)
    initial, outcome = validation.readings

    assert initial.cash.bet == pytest.approx(1.76)
    assert initial.credits.bet == pytest.approx(88)
    assert initial.cash.balance == pytest.approx(100.0)
    assert initial.credits.balance == pytest.approx(5000)
    assert outcome.cash.win == pytest.approx(1.00)
    assert outcome.credits.win == pytest.approx(50)


def test_a_credit_meter_reports_its_figures_in_cash_too() -> None:
    validation = meter(bet=88.0, win=50.0, mode=MeterMode.CREDITS, rate=0.02)
    initial, outcome = validation.readings

    assert initial.credits.bet == pytest.approx(88)
    assert initial.cash.bet == pytest.approx(1.76)
    assert outcome.credits.win == pytest.approx(50)
    assert outcome.cash.win == pytest.approx(1.00)


def test_without_a_denomination_only_the_unit_that_was_read_is_filled() -> None:
    """Nothing to convert with, so the other side stays empty rather than
    repeating the figures under a label that would be wrong."""
    validation = meter(bet=1.76, win=1.00, mode=MeterMode.CASH, rate=None)
    initial = validation.readings[0]

    assert initial.cash.bet == pytest.approx(1.76)
    assert initial.credits.bet is None
    assert initial.bet == pytest.approx(1.76)


def test_an_unknown_mode_fills_neither_unit() -> None:
    """Without knowing which side was read there is nothing to convert *from*,
    and filling either would be a guess about what the raw figures mean."""
    validation = meter(bet=1.76, win=1.00, mode=MeterMode.UNKNOWN, rate=0.02)
    initial = validation.readings[0]

    assert initial.credits.bet is None
    assert initial.cash.bet is None
    assert initial.bet == pytest.approx(1.76)


def test_the_balance_arithmetic_is_checked_in_both_units() -> None:
    """A cabinet draws one unit and the paytable speaks the other, so the
    relations are stated in both -- and `unit` says which each one is."""
    run = run_for(meter(bet=1.76, win=1.00, rate=0.02), SpinOutcome.WIN)
    readings = run.meter.readings

    checks = spin_service._meter_checks(run, readings)
    units = {check.unit for check in checks}

    assert spin_service.SpinMeterUnit.CREDITS in units
    assert spin_service.SpinMeterUnit.CASH in units
    # The one relation that is not about an amount is asked once, not twice.
    assert [check.key for check in checks].count("win-registered") == 1
    assert {check.key for check in checks} >= {
        "bet-deducted-credits",
        "bet-deducted-cash",
    }


def test_a_unit_with_no_figures_contributes_no_checks() -> None:
    """A table of indeterminate rows about numbers that were never going to
    exist says less than its absence."""
    run = run_for(meter(bet=1.76, win=1.00, rate=None), SpinOutcome.WIN)

    checks = spin_service._meter_checks(run, run.meter.readings)

    assert all(check.unit is not spin_service.SpinMeterUnit.CREDITS for check in checks)
    assert any(check.unit is spin_service.SpinMeterUnit.CASH for check in checks)


def test_credits_are_checked_to_a_tighter_tolerance_than_money() -> None:
    """A credit is a whole number, so the credits tolerance only has a converted
    figure's rounding to absorb -- it cannot hide a real discrepancy, since the
    smallest real one is a whole credit."""
    assert spin_service._tolerance(spin_service.SpinMeterUnit.CASH) == pytest.approx(
        settings.ANALYZE_SPIN_METER_TOLERANCE
    )
    assert spin_service._tolerance(spin_service.SpinMeterUnit.CREDITS) == pytest.approx(
        settings.ANALYZE_SPIN_METER_CREDIT_TOLERANCE
    )
    assert settings.ANALYZE_SPIN_METER_CREDIT_TOLERANCE < 1.0


def run_for(
    validation: SpinMeterValidation | None,
    outcome: SpinOutcome,
    *,
    bet_per_unit: int | None = 1,
    view: PaytableView | None = None,
) -> spin_service._ActiveRun:
    """The little of a run that pricing looks at: its meter, its outcome, and
    the bet per unit every award was multiplied by.

    The paytable is carried too, unlike the pricing helpers that take it as an
    argument: the bet check reads the unit cost off the *run*, since it is
    asking whether the rung this run was given matches the machine.
    """
    started = datetime(2026, 8, 28, 12, 0, 0)
    return spin_service._ActiveRun(
        run_id="2026-08-28_12-00-00",
        game="FortuneOx",
        label="Fortune Ox",
        directory=Path("C:/captures/analyze-spin/2026-08-28_12-00-00"),
        recording_dir="analyze-spin/2026-08-28_12-00-00",
        log_path=Path("C:/game/game.log"),
        rules=(),
        started_at=started,
        architecture="resnet34",
        bet_per_unit=bet_per_unit,
        record=False,
        steps={},
        outcome=outcome,
        meter=validation,
        paytable=paytable() if view is None else view,
    )


def test_the_total_is_one_number_and_the_conversion_is_spelled_out() -> None:
    """Every awarded line names its symbol, so the total is a number rather than
    a span. The span this replaced was never a claim about the maths -- it was the
    width of what the picture had failed to identify.

    50 credits, at a 5-credit bet over 5 lines (1 credit a line) and denom 0.01,
    is 0.50 on the glass.
    """
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]))
    run = run_for(meter(bet=0.05, win=0.50), SpinOutcome.WIN)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.paying_lines == 1
    assert expected.credits == 50.0
    assert expected.bet_credits == 5.0
    assert expected.credits_per_line == 1.0
    assert expected.cash == pytest.approx(0.50)
    assert expected.observed_win == pytest.approx(0.50)
    assert expected.verdict is SpinVerdict.PASSED


def test_a_meter_showing_something_else_fails_rather_than_rounding() -> None:
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]))
    run = run_for(meter(bet=0.05, win=0.20), SpinOutcome.WIN)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.verdict is SpinVerdict.FAILED
    assert "0.50" in expected.detail
    assert "0.20" in expected.detail


def test_no_line_paying_and_an_empty_win_meter_agree_without_the_conversion() -> None:
    """A losing spin needs neither the denomination nor the line count to be
    graded, so it is not left indeterminate for want of them."""
    awards = award(("1", ["AA", "BB", "AA", "BB", "AA"]))
    run = run_for(meter(bet=0.05, win=None), SpinOutcome.NO_WIN)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.credits == 0.0
    assert expected.verdict is SpinVerdict.PASSED
    assert expected.detail == "No line pays, and the win meter is empty"


def test_a_missing_denomination_leaves_the_verdict_indeterminate() -> None:
    """Never a guess: the conversion runs through the denomination, and a wrong
    verdict is nearly always one of its inputs rather than the pay itself."""
    awards = award(("1", ["AA"] * 5))
    run = run_for(meter(bet=0.05, win=1.00), SpinOutcome.WIN)

    expected = spin_service._expected(run, paytable(denomination=None), awards)

    assert expected.money_per_credit is None
    assert expected.denomination_label is None
    assert expected.cash is None
    assert expected.verdict is SpinVerdict.INDETERMINATE


def test_the_logged_denomination_is_cents_so_the_bet_is_not_divided_by_it() -> None:
    """The regression this exists for, with the numbers off the real cabinet.

    The log writes `denom[2.000]` for the `-2c-` paytable and the meter draws a
    bet of $1.76. Dividing the bet by the logged value gives 0.88 credits;
    the folder's own `MinTotalBet` says 88. The rate is 0.02, not 2.
    """
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]))
    # The rate is passed to both halves because a real run derives it once, from
    # the paytable, and hands it to the meter step -- see `_validate_meter`.
    run = run_for(meter(bet=1.76, win=1.00, rate=0.02), SpinOutcome.WIN)
    view = paytable(
        denomination="2.000",
        paytable_id="FortuneOx-1103AX-2c-90",
        declared_multiplier=2,
        lines=40,
    )

    expected = spin_service._expected(run, view, awards)

    assert expected.money_per_credit == pytest.approx(0.02)
    assert expected.denomination_label == "2c"
    # The bet reads back as the cabinet's own declared MinTotalBet, which is what
    # makes this figure worth carrying even though it prices nothing.
    assert expected.bet_credits == pytest.approx(88)
    assert expected.credits_per_line == pytest.approx(2.2)
    # 50 credits at 0.02 -- and *not* scaled by the 2.2 staked on the line.
    assert expected.credits == pytest.approx(50)
    assert expected.cash == pytest.approx(1.00)
    assert expected.verdict is SpinVerdict.PASSED


def test_the_award_is_scaled_by_the_bet_per_unit() -> None:
    """The correction, pinned on its own.

    A paytable combo's value is a rate *per bet unit*, so the same run pays a
    different number of credits depending on what the player staked. Here a
    4-long run of AA is the paytable's 50, and at 10 credits a unit the spin owes
    500 -- $5.00 at 1c.

    The bet is where the rung comes from: $8.80 at 1c is 880 credits, which is
    FortuneOx's 88-credit spin at its top rung of 10.
    """
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]), bet_per_unit=10)
    run = run_for(meter(bet=8.80, win=5.00), SpinOutcome.WIN, bet_per_unit=10)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.bet_per_unit == 10
    assert expected.credits == pytest.approx(500)
    assert expected.cash == pytest.approx(5.00)
    assert expected.verdict is SpinVerdict.PASSED


def test_the_same_run_at_one_credit_a_unit_pays_the_paytable_row() -> None:
    """The other half of the pair above, and the reason the multiplier went
    unnoticed: at the minimum bet an award and its paytable row are the same
    number, so every measurement taken there is consistent with both rules."""
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]), bet_per_unit=1)
    run = run_for(meter(bet=0.88, win=0.50), SpinOutcome.WIN)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.credits == pytest.approx(50)
    assert expected.cash == pytest.approx(0.50)
    assert expected.verdict is SpinVerdict.PASSED


def test_the_award_is_not_scaled_by_the_stake_on_a_line() -> None:
    """The rule this is *not*, kept pinned because it was tried.

    The multiplier is the bet per unit, never the bet spread over a line.
    FortuneOx stakes 88 credits across 40 lines, which is 2.2 -- not a rung of
    any ladder, and not a whole number. Scaling by it inflates a 50-credit award
    to 110 and fails a correct spin.
    """
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]), bet_per_unit=1)
    run = run_for(meter(bet=1.76, win=1.00, rate=0.02), SpinOutcome.WIN)
    view = paytable(denomination="2.000", declared_multiplier=2, lines=40)

    expected = spin_service._expected(run, view, awards)

    assert expected.credits_per_line == pytest.approx(2.2)
    assert expected.credits == pytest.approx(50), "not 110"
    assert expected.cash == pytest.approx(1.00)
    assert expected.verdict is SpinVerdict.PASSED


def test_an_unreadable_bet_still_prices_the_award() -> None:
    """The bet is not where the multiplier comes from -- it is given per run --
    so a meter that could not read the BET cell costs the corroboration and not
    the verdict."""
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]))
    run = run_for(meter(bet=None, win=0.50), SpinOutcome.WIN)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.bet_credits is None
    assert expected.credits_per_line is None
    assert expected.cash == pytest.approx(0.50)
    assert expected.verdict is SpinVerdict.PASSED


def test_a_credits_strip_misread_as_cash_is_put_right_by_the_bet() -> None:
    """The regression this exists for, with the symptoms it actually produced.

    `meter.combine` reads the *shape* of the numbers -- a symbol or a fraction
    means money -- so a credits cabinet that OCRs one stray decimal reads as
    cash. Every figure is then divided by the denomination a second time: an 88
    credit bet becomes 8800, which is no rung of any ladder, so the stake is
    refused and the award falls to nothing while the table files the readings
    under cash.

    The paytable settles it. 88 is a bet this cabinet takes in credits and not
    in money, and those two sets never overlap.
    """
    run = run_for(
        meter(bet=88.0, win=50.0, mode=MeterMode.CASH, rate=0.01),
        SpinOutcome.WIN,
        bet_per_unit=None,
    )

    mode, note = spin_service._settle_mode(run, MeterMode.CASH, run.meter.readings)

    assert mode is MeterMode.CREDITS
    assert note is not None and "drawing credits" in note


def test_a_cash_strip_is_left_alone() -> None:
    """$0.88 is a bet this cabinet takes in money and not in credits, which is
    the same evidence pointing the other way -- so the classification stands and
    there is nothing to say."""
    run = run_for(meter(bet=0.88, win=0.50, mode=MeterMode.CASH), SpinOutcome.WIN)

    mode, note = spin_service._settle_mode(run, MeterMode.CASH, run.meter.readings)

    assert mode is MeterMode.CASH
    assert note is None


def test_a_bet_that_reads_the_same_in_both_units_settles_nothing() -> None:
    """At a $1 denomination 88 credits *is* $88, so the bet is no evidence about
    which unit was drawn and the classification is left to stand."""
    run = run_for(
        meter(bet=88.0, win=50.0, mode=MeterMode.CASH, rate=1.0), SpinOutcome.WIN
    )
    view = paytable(denomination="100.000", paytable_id="FortuneOx-1102RX-100c-90")

    run.paytable = view
    mode, note = spin_service._settle_mode(run, MeterMode.CASH, run.meter.readings)

    assert mode is MeterMode.CASH
    assert note is None


def test_a_bet_matching_no_legal_total_settles_nothing() -> None:
    """A misread BET cell is not evidence about the strip either -- 300 is not a
    bet this cabinet takes in either unit."""
    run = run_for(meter(bet=300.0, win=50.0, mode=MeterMode.CASH), SpinOutcome.WIN)

    mode, note = spin_service._settle_mode(run, MeterMode.CASH, run.meter.readings)

    assert mode is MeterMode.CASH
    assert note is None


def test_a_credits_spin_prices_end_to_end_once_the_mode_is_settled() -> None:
    """The whole bug, end to end: the run that reported 0 credits won and filed
    its readings under cash now prices exactly as the same spin does in cash
    mode."""
    run = run_for(
        meter(bet=88.0, win=50.0, mode=MeterMode.CASH, rate=0.01),
        SpinOutcome.WIN,
        bet_per_unit=None,
    )
    mode, _ = spin_service._settle_mode(run, MeterMode.CASH, run.meter.readings)
    readings = [
        spin_service._in_both_units(reading, mode, 0.01)
        for reading in run.meter.readings
    ]
    run.meter = run.meter.model_copy(update={"mode": mode, "readings": readings})
    run.bet_per_unit = spin_service._derive_bet_per_unit(run, readings)
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]), bet_per_unit=run.bet_per_unit)

    expected = spin_service._expected(run, paytable(), awards)

    assert run.bet_per_unit == 1
    assert expected.credits == pytest.approx(50)
    assert expected.unit is spin_service.SpinMeterUnit.CREDITS
    assert expected.observed_win == pytest.approx(50)
    assert expected.verdict is SpinVerdict.PASSED


def test_the_stake_is_worked_out_from_the_bet_when_nobody_says() -> None:
    """The ordinary case, and the one that has to keep working without anybody
    configuring anything.

    Nothing is written down that says what the player staked -- but the meter
    read the total bet and the paytable says a spin costs 88 at one credit a
    unit, so a bet of 880 is the top rung of ten.
    """
    run = run_for(meter(bet=8.80, win=5.00), SpinOutcome.WIN, bet_per_unit=None)

    assert spin_service._derive_bet_per_unit(run, run.meter.readings) == 10


def test_the_stake_at_the_minimum_bet_is_one() -> None:
    """Which is why the multiplication went unnoticed: at 88 credits the rung is
    1, and an award is its paytable row unchanged."""
    run = run_for(meter(bet=0.88, win=0.50), SpinOutcome.WIN, bet_per_unit=None)

    assert spin_service._derive_bet_per_unit(run, run.meter.readings) == 1


def test_a_bet_that_is_not_a_rung_is_not_guessed_at() -> None:
    """A misread BET cell should cost the pricing, not misprice every line on
    the run.

    300 credits over an 88-credit spin *rounds* to 3, and 3 is a real rung --
    which is exactly why rounding alone is not enough. 88 x 3 is 264, not 300,
    so nothing here says what was staked.
    """
    run = run_for(meter(bet=3.00, win=0.50), SpinOutcome.WIN, bet_per_unit=None)

    assert spin_service._derive_bet_per_unit(run, run.meter.readings) is None


def test_a_bet_matching_no_rung_at_all_is_refused() -> None:
    """4 is not on FortuneOx's ladder, so 352 credits is not a bet it offers
    even though the arithmetic is exact."""
    run = run_for(meter(bet=3.52, win=0.50), SpinOutcome.WIN, bet_per_unit=None)

    assert spin_service._derive_bet_per_unit(run, run.meter.readings) is None


def test_an_unreadable_bet_leaves_the_stake_unknown() -> None:
    run = run_for(meter(bet=None, win=0.50), SpinOutcome.WIN, bet_per_unit=None)

    assert spin_service._derive_bet_per_unit(run, run.meter.readings) is None


def test_the_meter_reads_before_the_reels_so_the_stake_prices_them_first() -> None:
    """The ordering the stake depends on.

    The meter is the only one of the three closing steps that produces an *input*
    to another: which unit the strip drew and what a bet unit cost are what turn
    a line's paytable rate into an award. It used to read last, so the lines were
    priced before the stake was known and had to be re-priced afterwards.
    """
    order = [key for key, _ in spin_service._SEQUENCE]

    assert order.index(spin_service.STEP_METER) < order.index(
        spin_service.STEP_CLASSIFY
    )
    assert order.index(spin_service.STEP_CLASSIFY) < order.index(
        spin_service.STEP_PAYLINES
    )


def test_a_stake_neither_given_nor_derivable_leaves_the_verdict_indeterminate() -> None:
    """Every line still knows what it landed and what the maths pays for it, and
    none of them knows what that came to."""
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]), bet_per_unit=None)
    run = run_for(meter(bet=None, win=0.50), SpinOutcome.WIN, bet_per_unit=None)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.bet_per_unit is None
    assert expected.verdict is SpinVerdict.INDETERMINATE
    assert "could not be read off the BET cell" in expected.detail
    # The award survives as the rate it always was, on the line itself.
    assert awards[0].awarded is True
    assert awards[0].combo_value == 50.0
    assert awards[0].credits is None


def test_a_paytable_naming_no_unit_says_so_rather_than_pricing_a_guess() -> None:
    """A different failure from a denomination that was never reported, and a
    different fix -- so a different sentence, naming the paytable."""
    awards = award(("1", ["AA"] * 5))
    run = run_for(meter(bet=0.05, win=1.00), SpinOutcome.WIN)
    view = paytable(paytable_id="FortuneOx-1106HX-LATAM", declared_multiplier=None)

    expected = spin_service._expected(run, view, awards)

    assert expected.money_per_credit is None
    assert expected.denomination_label == "1"
    assert expected.verdict is SpinVerdict.INDETERMINATE
    assert "FortuneOx-1106HX-LATAM" in expected.detail
    assert "names no unit" in expected.detail


def test_a_credit_meter_converts_by_not_converting() -> None:
    """In credits mode the bet is already the thing the paytable is denominated
    in, so no rate takes part -- and the win it drew is compared against the
    award in credits rather than against the same award in money."""
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]))
    run = run_for(meter(bet=5.0, win=50.0, mode=MeterMode.CREDITS), SpinOutcome.WIN)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.bet_credits == pytest.approx(5)
    assert expected.credits == pytest.approx(50)
    assert expected.observed_win == pytest.approx(50)
    assert expected.verdict is SpinVerdict.PASSED
    assert "already counting them" in expected.detail


def test_a_credit_meter_is_not_priced_as_money() -> None:
    """The bug this branch prevents: 50 credits at a 1c denomination is 0.50 in
    money, and comparing a credit meter's 50 against that would fail a correct
    spin."""
    awards = award(("1", ["AA", "AA", "AA", "AA", "BB"]))
    run = run_for(meter(bet=5.0, win=0.50, mode=MeterMode.CREDITS), SpinOutcome.WIN)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.verdict is SpinVerdict.FAILED


def test_an_unreadable_meter_leaves_the_verdict_indeterminate() -> None:
    awards = award(("1", ["AA"] * 5))
    run = run_for(None, SpinOutcome.WIN)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.total_bet is None
    assert expected.verdict is SpinVerdict.INDETERMINATE


def test_several_awarded_lines_add_up() -> None:
    awards = award(("1", ["AA"] * 5), ("2", ["BB", "BB", "BB", "AA", "AA"]))
    run = run_for(meter(bet=0.05, win=1.10), SpinOutcome.WIN)

    expected = spin_service._expected(run, paytable(), awards)

    assert expected.paying_lines == 2
    assert expected.credits == 110.0
    assert expected.cash == pytest.approx(1.10)
    assert expected.verdict is SpinVerdict.PASSED


# --- the sequence ---------------------------------------------------------


def test_naming_the_symbols_is_a_step_of_its_own_before_the_paylines() -> None:
    """Its own step because it fails for its own reasons -- no torch, no trained
    checkpoint -- and "the model is not there" is a different fact from "the
    lines do not pay"."""
    keys = [key for key, _ in spin_service._SEQUENCE]

    assert keys.index(spin_service.STEP_CLASSIFY) < keys.index(
        spin_service.STEP_PAYLINES
    )


# --- which network, and how sure it has to be ----------------------------


def test_the_pass_threshold_is_lower_than_the_classifier_pages_own() -> None:
    """0.85 here against 0.90 there, and the gap is the point.

    That floor sits far above where the classes separate, so a correct reading is
    rejected whenever the model is only fairly sure. Safe on a page that shows
    the ranked candidates beside every rejected tile; costly here, because a
    payline through an unnamed tile stops there and the spin looks like it paid
    less than it did.
    """
    assert settings.ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE == 0.85
    assert settings.CLASSIFIER_MIN_CONFIDENCE == 0.90


def test_a_blank_floor_opts_back_into_the_classifiers_own() -> None:
    """Blank is not the same as absent: absent means 0.85, blank means "theirs"."""
    from app.config.analyze_spin import AnalyzeSpinSettings

    assert (
        AnalyzeSpinSettings(
            ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE=""  # type: ignore[arg-type]
        ).ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE
        is None
    )


def test_the_architecture_setting_only_supplies_a_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank hands the choice back to the classifier's own default, which is what
    lets the dashboard's dropdown open on EfficientNet-B0 without the visible
    default and the configured one being two separate facts."""
    monkeypatch.setattr(settings, "ANALYZE_SPIN_CLASSIFIER_ARCHITECTURE", "")
    assert settings.analyze_spin_classifier_architecture is None

    monkeypatch.setattr(settings, "ANALYZE_SPIN_CLASSIFIER_ARCHITECTURE", " resnet34 ")
    assert settings.analyze_spin_classifier_architecture == "resnet34"


def test_a_request_naming_no_network_gets_the_configured_one() -> None:
    """ResNet34 by default: it is the one that reads a real split better, and the
    dashboard's dropdown opens on the same entry so the visible default and the
    configured one cannot drift."""
    assert settings.CLASSIFIER_ARCHITECTURE == "resnet34"
    assert classifier_service.resolve_architecture(None) == "resnet34"


def test_either_network_can_be_asked_for_by_name() -> None:
    """Both stay trained at once, and grading a spin with each in turn is the
    reason this is a per-run choice rather than a setting."""
    for name in ("efficientnet_b0", "resnet34"):
        assert classifier_service.resolve_architecture(name) == name


async def test_an_unknown_network_is_refused_before_the_spin(
    client: AsyncClient,
) -> None:
    """A 400 on the request, not a failed step twelve steps in.

    Checked before the game config is even read, because a spin driven all the
    way to its result and then graded by nothing is the worst way to find out
    about a typo.
    """
    response = await client.post(f"{API}/start", params={"architecture": "mobilenet"})

    assert response.status_code == 400
    payload = response.json()
    assert_failure(payload, code="BAD_REQUEST")
    # The message names both networks, so the typo is fixable from the error.
    assert "efficientnet_b0" in payload["message"]
    assert "resnet34" in payload["message"]
    # And nothing was started.
    state = assert_success((await client.get(f"{API}/status")).json())
    assert state["active"] is False


# --- what a bet unit cost -------------------------------------------------
