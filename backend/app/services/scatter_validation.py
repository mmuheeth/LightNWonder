"""Scatter Value Validation: read one screen and judge each landed scatter's OCR
figure against the range ``math.xml`` declares for it -- the one new judgement
here. Reuses Evaluate Screen's private capture/grid helpers (not its public
``evaluate()``, which also reads the meter) and ``paytable.view()`` unchanged
for the live bet."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.core.logging import get_logger
from app.exceptions.base import AppException
from app.schemas.analyze_spin import SpinScatterReading
from app.schemas.paytable import PaytableView
from app.schemas.scatter_validation import (
    ScatterValidationBetInfo,
    ScatterValidationRequest,
    ScatterValidationResult,
    ScatterValueCheck,
)
from app.services import analyze_spin as analyze_spin_service
from app.services import evaluate_screen as evaluate_screen_service
from app.services import paytable as paytable_service
from app.services import roi as roi_service
from app.utils.game_math import GameMath, GameMathError, OrbValueTable, load_game_math

logger = get_logger("scatter_validation")

# FG is a free-games trigger with no figure and no orb value table row, so it
# doesn't belong in the value check at all rather than reporting an empty
# match every time.
_EXCLUDED_FROM_CHECK = {"FG"}

# The only context checked today: nothing yet signals whether a screenshot was
# taken during Hold-and-Spin or Free Games rather than the base game, so every
# check judges against BG's own tables -- see `_table_for`.
_DEFAULT_CONTEXT = "BG"


def _symbol_kind(symbol: str) -> str:
    """Which orb value table a scatter code is judged against. ``SC`` is the
    literal orb kind math.xml's own tables name; every other scatter code (a
    feature trigger like ``FG`` or a splittable orb like ``SF``) is priced under
    the same ``NonSC`` table as any other landed orb."""
    return "SC" if symbol == "SC" else "NonSC"


def _table_for(
    math: GameMath, symbol_kind: str, bet: int | None, context: str = _DEFAULT_CONTEXT
) -> tuple[OrbValueTable | None, int | None]:
    """The declared value table for a symbol kind, at ``bet`` when one is live.
    ``context`` picks the game state's tables -- only ``BG`` is resolved today,
    since nothing yet signals a screenshot came from Hold-and-Spin or Free Games
    instead. Falls back to the first table of that kind/context when no bet
    matches, the same fallback :func:`app.services.paytable._orb_values` makes."""
    if bet is not None:
        table = math.orb_values(symbol_kind, context=context, bet=bet)
        if table is not None:
            return table, bet
    tables = [
        t
        for t in math.orb_value_tables
        if t.symbol_kind == symbol_kind and t.context == context
    ]
    return (tables[0], tables[0].bet) if tables else (None, None)


# The $2 denomination's own money_per_credit (200 cents/credit). math.xml's
# orb tables declare a bet-unit multiplier, but the glass shows it already
# converted to money -- verified true only at $2, so this constant scopes the
# fix rather than assuming other denominations scale the same way. The
# *table* is scaled up to match the glass, never the OCR reading divided back
# down: ocr_value must stay exactly what PaddleOCR read, while expected_values
# is what a user should see as "what this game displays".
_TWO_DOLLAR_MONEY_PER_CREDIT = 2.0


def _check_scatter(
    scatter: SpinScatterReading,
    math: GameMath,
    live_bet: int | None,
    money_per_credit: float | None = None,
    context: str = _DEFAULT_CONTEXT,
) -> ScatterValueCheck:
    """Judge one landed scatter against its declared value range."""
    symbol_kind = _symbol_kind(scatter.symbol)
    table, bet_used = _table_for(math, symbol_kind, live_bet, context)

    base = {
        "name": scatter.name,
        "row": scatter.row,
        "column": scatter.column,
        "symbol": scatter.symbol,
        "label": scatter.label,
        "ocr_value": scatter.value,
        "ocr_prize_label": scatter.prize_label,
        "ocr_text": scatter.text,
        "ocr_confidence": scatter.ocr_confidence,
        "symbol_kind": symbol_kind,
        "bet": bet_used,
    }

    if table is None:
        return ScatterValueCheck(
            **base,
            status="no_table",
            reason=f"The loaded maths declares no {symbol_kind} value table",
        )

    tiers = {tier.code: tier for tier in math.jackpot_tiers}
    raw_expected_values = sorted(
        {item.value for item in table.weights if not item.is_jackpot}
    )
    expected_label_set: set[str] = set()
    for item in table.weights:
        if not item.is_jackpot:
            continue
        tier = tiers.get(item.value)
        if tier is not None and tier.type_label is not None:
            expected_label_set.add(tier.type_label)
    expected_labels = sorted(expected_label_set)

    # Scale the table up to match the glass, never divide the OCR reading back
    # down -- see _TWO_DOLLAR_MONEY_PER_CREDIT. Every other denomination scales
    # by 1.0 (a no-op); jackpot labels are words, never scaled.
    scale = (
        money_per_credit if money_per_credit == _TWO_DOLLAR_MONEY_PER_CREDIT else 1.0
    )
    expected_values = sorted({value * scale for value in raw_expected_values})

    if scatter.value is None and scatter.prize_label is None:
        # A feature scatter (e.g. FG) is drawn without a figure, and that is a
        # correct reading rather than a failure -- but an orb whose table
        # promises plain credit rows or jackpot tiers and read neither is
        # worth flagging rather than silently passing.
        status = "unreadable" if (expected_values or expected_labels) else "matched"
        return ScatterValueCheck(
            **base,
            money_per_credit=money_per_credit,
            raw_expected_values=raw_expected_values,
            expected_values=expected_values,
            expected_jackpot_labels=expected_labels,
            status=status,
            reason=(
                "This tile should carry a figure or jackpot label, but OCR read neither"
                if status == "unreadable"
                else None
            ),
        )

    if scatter.prize_label is not None:
        matched = scatter.prize_label in expected_labels
        return ScatterValueCheck(
            **base,
            money_per_credit=money_per_credit,
            raw_expected_values=raw_expected_values,
            expected_values=expected_values,
            expected_jackpot_labels=expected_labels,
            status="matched" if matched else "not_matched",
            reason=(
                None
                if matched
                else f"{scatter.prize_label!r} is not a jackpot tier {symbol_kind} declares at bet {bet_used}"
            ),
        )

    # ocr_value is compared exactly as PaddleOCR read it -- never divided --
    # against expected_values, which is already in the same (possibly scaled)
    # units.
    matched = scatter.value in expected_values
    return ScatterValueCheck(
        **base,
        money_per_credit=money_per_credit,
        raw_expected_values=raw_expected_values,
        expected_values=expected_values,
        expected_jackpot_labels=expected_labels,
        status="matched" if matched else "not_matched",
        reason=(
            None
            if matched
            else f"{scatter.value!r} is not a declared value for {symbol_kind} at bet {bet_used}"
        ),
    )


def _bet_info(view: PaytableView) -> ScatterValidationBetInfo:
    """Narrow a :class:`~app.schemas.paytable.PaytableView` to the bet/denomination panel."""
    bet_config = view.bet_config
    denomination = view.denomination
    return ScatterValidationBetInfo(
        paytable_id=view.paytable_id,
        source_origin=view.source.origin,
        current_bet=bet_config.current_bet if bet_config else None,
        current_bet_source=bet_config.current_bet_source if bet_config else None,
        ladder=list(bet_config.ladder) if bet_config else [],
        unit_cost=bet_config.unit_cost if bet_config else None,
        denomination_value=denomination.value if denomination else None,
        denomination_label=denomination.label if denomination else None,
        money_per_credit=denomination.money_per_credit if denomination else None,
    )


async def validate(
    request: ScatterValidationRequest | None = None,
) -> ScatterValidationResult:
    """Read one screen and judge every landed scatter against the value range the
    loaded maths declares. Grid and bet info are independent readings of one
    picture -- a screen whose maths can't be found still returns every
    scatter's OCR figure, just with no verdict on it."""
    started = time.perf_counter()
    payload = request or ScatterValidationRequest()
    name, config = evaluate_screen_service._active_config()
    errors: list[str] = []

    if payload.file_name is None:
        path, blank = await evaluate_screen_service._capture()
        captured = True
    else:
        path = await asyncio.to_thread(roi_service.resolve_frame, payload.file_name)
        blank = await asyncio.to_thread(roi_service.is_blank, path)
        captured = False

    source = await asyncio.to_thread(
        evaluate_screen_service._source, path, captured=captured, blank=blank
    )
    if blank:
        errors.append(
            f"{source.file_name} has nothing in it. Check that OBS's "
            "window-capture source is pointed at the game and showing it."
        )

    bet_info: ScatterValidationBetInfo | None = None
    bet_info_error: str | None = None
    math: GameMath | None = None

    try:
        view = await paytable_service.view()
        math = await asyncio.to_thread(load_game_math, Path(view.math.path))
    except AppException as exc:
        bet_info_error = exc.message
        errors.append(f"Bet info: {exc.message}")
    except GameMathError as exc:
        bet_info_error = str(exc)
        errors.append(f"Bet info: {bet_info_error}")
    except Exception as exc:
        bet_info_error = f"{type(exc).__name__}: {exc}"
        errors.append(f"Bet info: {bet_info_error}")
        logger.exception("Reading the paytable for %s failed", name)
    else:
        bet_info = _bet_info(view)

    reels = None
    reels_error: str | None = None
    grid_image: str | None = None
    try:
        reels, result, split_dir = await evaluate_screen_service._read_grid(
            source.file_name,
            include_images=payload.include_images,
        )
    except AppException as exc:
        reels_error = exc.message
        errors.append(f"Grid: {exc.message}")
    except Exception as exc:
        reels_error = f"{type(exc).__name__}: {exc}"
        errors.append(f"Grid: {reels_error}")
        logger.exception("Reading the grid of %s failed", name)
    else:
        grid_image = result.overlay_image
        scatters, summary = await analyze_spin_service.read_scatters(
            config, split_dir, reels
        )
        reels = reels.model_copy(
            update={"scatters": scatters, "scatter_summary": summary}
        )

    checks: list[ScatterValueCheck] = []
    checkable = [
        s
        for s in (reels.scatters if reels else [])
        if s.symbol not in _EXCLUDED_FROM_CHECK
    ]
    if checkable:
        if math is not None:
            live_bet = bet_info.current_bet if bet_info else None
            money_per_credit = bet_info.money_per_credit if bet_info else None
            checks = [
                _check_scatter(s, math, live_bet, money_per_credit) for s in checkable
            ]
        else:
            checks = [
                ScatterValueCheck(
                    name=s.name,
                    row=s.row,
                    column=s.column,
                    symbol=s.symbol,
                    label=s.label,
                    ocr_value=s.value,
                    ocr_prize_label=s.prize_label,
                    ocr_text=s.text,
                    ocr_confidence=s.ocr_confidence,
                    status="no_table",
                    reason="The loaded maths could not be read, so nothing could be checked",
                )
                for s in checkable
            ]

    duration_ms = max(0, round((time.perf_counter() - started) * 1000))
    logger.info(
        "Validated %d scatter(s) on %s from %s in %dms (%d not matched)",
        len(checks),
        name,
        source.file_name,
        duration_ms,
        sum(1 for c in checks if c.status == "not_matched"),
    )

    return ScatterValidationResult(
        game=name,
        label=config.name,
        source=source,
        duration_ms=duration_ms,
        reels=reels,
        reels_error=reels_error,
        grid_image=grid_image,
        bet_info=bet_info,
        bet_info_error=bet_info_error,
        checks=checks,
        errors=errors,
    )
