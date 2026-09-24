"""Scatter Value Validation: read one screen, then judge every landed scatter's
prize figure against the value range ``math.xml`` declares for it.

**Nothing here reads a screen or a maths file.** The grid half is read the same
way ``app.services.evaluate_screen`` reads it (screenshot, grid split, CNN,
PaddleOCR per scatter) -- but *not* its cash meter, which this feature has no
use for. The bet and denomination half is
:class:`~app.schemas.paytable.PaytableView`, produced by ``app.services.paytable``
exactly as the Game Config tab does. This module adds one new judgement over
both: does the number PaddleOCR read off a landed orb appear in that symbol's
declared value table, at the bet the log last reported.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.analyze_spin import SpinReelReading
from app.schemas.evaluate_screen import EvaluateScreenSource

__all__ = [
    "ScatterCheckStatus",
    "ScatterValidationBetInfo",
    "ScatterValidationRequest",
    "ScatterValidationResult",
    "ScatterValueCheck",
]

ScatterCheckStatus = Literal["matched", "not_matched", "unreadable", "no_table"]


class ScatterValidationRequest(BaseModel):
    """Which screen to read, and how. Same shape as ``EvaluateScreenRequest`` --
    this feature reads a screen exactly the way that one does and adds a
    judgement on top, so the request that drives it does not diverge either."""

    file_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Screenshot in the screenshots directory to read. Must be a bare "
            "filename. Omit to capture the game's current screen with OBS."
        ),
    )
    architecture: str | None = Field(
        default=None,
        description=(
            "Which trained network names the tiles: 'resnet34' or "
            "'efficientnet_b0'. Omit for the configured default."
        ),
    )
    include_images: bool = Field(
        default=True,
        description="Return the meter crop and the ringed grid as data URIs.",
    )


class ScatterValidationBetInfo(BaseModel):
    """The bet and denomination in play right now, read the way the Game Config
    tab reads them -- the log's last ``BetChangeMsg`` and ``UpdatePayTable``
    lines, joined against the paytable folder they named. A value table is
    priced per bet rung, so this is what a landed orb's figure is judged
    against."""

    paytable_id: str = Field(description="Paytable folder the maths was read from.")
    source_origin: str = Field(
        description="'log', 'requested' or 'only' -- how that id was arrived at."
    )
    log_path: str | None = Field(
        default=None,
        description="Game log the denomination and paytable id were read from.",
    )
    log_line: str | None = Field(
        default=None,
        description="The whole 'UpdatePayTable' log line the denomination was read out of.",
    )
    current_bet: int | None = Field(
        default=None,
        description=(
            "The bet-per-unit rung actually in play: the log's last "
            "'BetChangeMsg' when there is one, else the paytable's declared "
            "minimum. Null when neither is available -- then a scatter is "
            "checked against every bet rung's table instead of one."
        ),
    )
    current_bet_source: str | None = Field(
        default=None, description="'log' or 'unit_cost' -- which of the above it was."
    )
    ladder: list[int] = Field(
        default_factory=list,
        description="Every bet-per-unit rung the game offers, in the file's own order.",
    )
    unit_cost: int | None = Field(
        default=None, description="Credits one spin costs at one bet per unit."
    )
    denomination_value: float | None = Field(
        default=None,
        description="The number the log reported, e.g. 2.0 for a 2c game.",
    )
    denomination_label: str | None = Field(
        default=None, description="The denomination as an operator says it, e.g. '2c'."
    )
    money_per_credit: float | None = Field(
        default=None,
        description="One credit in money, e.g. 0.02 for a 2c game. Null when unresolved.",
    )


class ScatterValueCheck(BaseModel):
    """One landed scatter, judged against its declared value range."""

    name: str = Field(description="Grid position, e.g. 'r2c3'.")
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    symbol: str = Field(description="The scatter's code, e.g. 'SC' or 'FG'.")
    label: str = Field(description="Its display name from the game config.")
    ocr_value: float | None = Field(
        default=None, description="The number PaddleOCR read off the tile."
    )
    ocr_prize_label: str | None = Field(
        default=None,
        description="A jackpot tier word read off the tile instead of a number.",
    )
    ocr_text: str | None = Field(
        default=None, description="Everything OCR read off the crop, raw."
    )
    ocr_confidence: float | None = Field(
        default=None, description="OCR's own mean word confidence, 0-100."
    )
    money_per_credit: float | None = Field(
        default=None,
        description=(
            "The live denomination's own rate, e.g. 2.0 for $2 -- the factor "
            "expected_values was scaled by so it reads in the same units as "
            "ocr_value. Null when the denomination is unresolved; 1.0 when it "
            "resolved but scaling is a no-op (every denomination but $2 today)."
        ),
    )
    raw_expected_values: list[float] = Field(
        default_factory=list,
        description=(
            "math.xml's own declared multipliers, unscaled -- the figures "
            "'expected_values' were computed from. Equal to expected_values "
            "whenever money_per_credit is 1.0 or null."
        ),
    )
    symbol_kind: str | None = Field(
        default=None,
        description=(
            "'SC' or 'NonSC' -- which orb value table this code is judged "
            "against. Null when the game declares no orb value tables at all."
        ),
    )
    bet: int | None = Field(
        default=None,
        description="Bet rung the expected values were looked up at, when one was live.",
    )
    expected_values: list[float] = Field(
        default_factory=list,
        description=(
            "Every plain credit amount math.xml declares for this symbol kind "
            "(and bet, when live), scaled by money_per_credit so it reads in "
            "the same units the glass and ocr_value do -- jackpot codes "
            "excluded, since those print a tier word rather than a number. "
            "Identical to raw_expected_values except at $2."
        ),
    )
    expected_jackpot_labels: list[str] = Field(
        default_factory=list,
        description="Jackpot tier labels this symbol kind can land on, at the same table.",
    )
    status: ScatterCheckStatus = Field(
        description=(
            "'matched': the OCR'd figure (or prize label) is one math.xml "
            "declares. 'not_matched': it read something, but not one math.xml "
            "declares for this symbol at this bet. 'unreadable': the tile should "
            "carry a figure but OCR read none. 'no_table': math.xml declares no "
            "value table for this symbol kind, so nothing could be checked."
        )
    )
    reason: str | None = Field(
        default=None,
        description="Why the status is what it is, for anything but a plain match.",
    )


class ScatterValidationResult(BaseModel):
    """Everything one screen was read to say, plus the scatter value verdicts."""

    game: str = Field(description="Filename stem of the game config that was used.")
    label: str = Field(description="Display name that config declares.")
    source: EvaluateScreenSource = Field(description="The screen that was read.")
    duration_ms: int = Field(ge=0, description="How long the whole reading took.")
    reels: SpinReelReading | None = Field(
        default=None,
        description="What landed, tile by tile, and every scatter's figure.",
    )
    reels_error: str | None = Field(
        default=None,
        description="Why the grid could not be read, when it could not be.",
    )
    grid_image: str | None = Field(
        default=None, description="The reels crop with a ring over every named cell."
    )
    bet_info: ScatterValidationBetInfo | None = Field(
        default=None,
        description="The live bet and denomination, when the paytable could be read.",
    )
    bet_info_error: str | None = Field(
        default=None, description="Why there is no bet info, when there is none."
    )
    checks: list[ScatterValueCheck] = Field(
        default_factory=list,
        description="Every landed scatter, judged against its declared value range.",
    )
    errors: list[str] = Field(
        default_factory=list, description="Everything that went wrong, in order."
    )
