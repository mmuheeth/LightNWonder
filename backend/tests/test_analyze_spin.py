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
    SpinVerdict,
)
from app.schemas.paylines import (
    PaylineCheckResult,
    PaylineLine,
    PaylineMethod,
    PaylineSource,
    PaylineStats,
    PaylineStep,
)
from app.schemas.paytable import (
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


def paytable(*, denomination: str | None = "0.01", lines: int = 5) -> PaytableView:
    """A paytable view carrying only what pricing a line actually reads."""
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
        paytable_id="FortuneOx-1101YX-1c-90",
        directory="C:/game/GameConfig/FortuneOx-1101YX-1c-90",
        source=PaytableSource(origin="log", denomination=denomination),
        available=["FortuneOx-1101YX-1c-90"],
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


def award(*lines: tuple[str, list[str | None]]):
    """Every line of one check, priced."""
    return spin_service._award(paytable(), check(*lines), {})


def one(*codes: str | None):
    """The single award of a one-line check reading ``codes``."""
    return award(("1", list(codes)))[0]


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


def meter(*, bet: float | None, win: float | None) -> SpinMeterValidation:
    """A cash-meter validation carrying only the two figures pricing reads."""
    return SpinMeterValidation(
        readings=[
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
        ],
        verdict=SpinVerdict.PASSED,
    )


def run_for(
    validation: SpinMeterValidation | None, outcome: SpinOutcome
) -> spin_service._ActiveRun:
    """The little of a run that pricing looks at: its meter and its outcome."""
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
        record=False,
        steps={},
        outcome=outcome,
        meter=validation,
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

    assert expected.denomination is None
    assert expected.cash is None
    assert expected.verdict is SpinVerdict.INDETERMINATE


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
