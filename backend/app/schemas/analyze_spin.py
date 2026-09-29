"""Payloads for Analyze Spin: one orchestrated spin, and the two validations run over
what it produced."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.meter import MeterMode, MeterValues
from app.schemas.paylines import PaylineStats, PaylineStep
from app.schemas.paytable import DenominationInfo
from app.schemas.tile_clips import TileClipSet

__all__ = [
    "SpinAnalysisState",
    "SpinExpectedAward",
    "SpinFrame",
    "SpinLineAward",
    "SpinLogEvent",
    "SpinMeterCheck",
    "SpinMeterFigures",
    "SpinMeterReading",
    "SpinMeterUnit",
    "SpinMeterValidation",
    "SpinOutcome",
    "SpinPaylineValidation",
    "SpinRecording",
    "SpinReelReading",
    "SpinRun",
    "SpinRunState",
    "SpinStep",
    "SpinStepState",
    "SpinSymbolReading",
    "SpinVerdict",
]


# --- vocabulary -----------------------------------------------------------


class SpinStepState(StrEnum):
    """How far one step of the sequence got."""

    PENDING = "pending"
    """Not reached yet."""

    RUNNING = "running"
    """In progress; at most one step is ever in this state."""

    COMPLETED = "completed"

    SKIPPED = "skipped"
    """Deliberately not run -- take-win on a spin that won nothing."""

    FAILED = "failed"


class SpinControl(StrEnum):
    """Which channel drove the spin and collected the win.

    The rest of the sequence is identical either way -- same screenshots,
    same log-following, same three closing readings. Only the two presses
    differ, so this is one field rather than a second orchestration.
    """

    IDECK = "ideck"
    """The i-deck key for the spin, and a posted click into the game's own
    window for the win -- take-win is not one of the panel's fourteen keys."""

    GAF = "gaf"
    """The game's own methods, over GAF -- no coordinates and no key layout,
    both presses are calls the game makes to itself."""


class SpinRunState(StrEnum):
    """How the run as a whole ended, or that it has not."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SpinOutcome(StrEnum):
    """What the spin did, as the game's own log reported it."""

    WIN = "win"
    """The win meter counted up, so there was something to collect."""

    NO_WIN = "no-win"
    """No count-up arrived before the wait elapsed."""

    UNKNOWN = "unknown"
    """The run never got as far as finding out."""


class SpinVerdict(StrEnum):
    """The result of comparing two independently measured numbers."""

    PASSED = "passed"
    FAILED = "failed"

    INDETERMINATE = "indeterminate"
    """One of the two numbers could not be read, so no comparison was made.
    Never folded into 'failed': an unreadable meter and a wrong balance are
    different problems with different fixes."""


# --- the run's own pieces -------------------------------------------------


class SpinStep(BaseModel):
    """One stage of the sequence, and what happened in it."""

    key: str = Field(description="Stable identifier, e.g. 'reels-stop'.")
    label: str = Field(description="How the step reads in the timeline.")
    state: SpinStepState = Field(description="How far this step got.")
    detail: str | None = Field(
        default=None,
        description="What the step actually did or found, in one line.",
    )
    started_at: datetime | None = Field(
        default=None, description="Null while the step is still pending."
    )
    finished_at: datetime | None = Field(
        default=None, description="Null until the step leaves 'running'."
    )
    duration_ms: int | None = Field(
        default=None, ge=0, description="How long the step took, once it is over."
    )
    error: str | None = Field(
        default=None,
        description="Why the step failed, from the service that refused it.",
    )
    error_code: str | None = Field(
        default=None,
        description=(
            "Error code of the underlying failure, e.g. "
            "'IDECK_PRESS_NOT_CONFIRMED' -- the same code the equivalent "
            "direct request would return."
        ),
    )


class SpinLogEvent(BaseModel):
    """One recognised line of the game's log, read while the run waited."""

    event: str = Field(description="Event name from app.utils.game_log.")
    summary: str = Field(description="The event as a sentence.")
    at: datetime | None = Field(
        default=None, description="The game's own timestamp, in host local time."
    )
    log_line: str = Field(description="The whole line, as its own evidence.")


class SpinFrame(BaseModel):
    """One screenshot the run took, written where ROI and the reel grid read."""

    key: str = Field(
        description="'initial', 'outcome' or 'collected' -- which moment it is."
    )
    label: str = Field(description="What that moment is called.")
    file_name: str = Field(
        description="File name inside the dashboard screenshots directory."
    )
    at: datetime = Field(description="When the run took it.")
    blank: bool = Field(
        default=False,
        description=(
            "Whether the frame came back empty. Not an error -- OBS renders "
            "nothing for a moment after its window source is re-pointed -- so "
            "it is retried and reported, since every reading off it would be "
            "meaningless."
        ),
    )
    attempts: int = Field(
        default=1,
        ge=1,
        description="Screenshots taken for this moment, counting retries after a blank one.",
    )


class SpinRecording(BaseModel):
    """The video of the spin, if one was made."""

    output_path: str | None = Field(
        default=None,
        description="Where OBS wrote it. Null when OBS did not report a path.",
    )
    duration_ms: int = Field(default=0, ge=0, description="As OBS timed it.")


# --- cash meter validation ------------------------------------------------


class SpinMeterUnit(StrEnum):
    """Which of the two quantities a figure or a check is expressed in."""

    CREDITS = "credits"
    CASH = "cash"


class SpinMeterFigures(BaseModel):
    """The strip's three numbers in one unit."""

    balance: float | None = Field(default=None, description="The balance cell.")
    win: float | None = Field(
        default=None,
        description="The WIN cell. Null is normal between spins -- it is empty.",
    )
    bet: float | None = Field(default=None, description="The BET cell.")


class SpinMeterReading(BaseModel):
    """The meter as one screenshot drew it, in both units."""

    frame: str = Field(description="Which frame this was read off, by key.")
    label: str = Field(description="That frame's moment, for a table heading.")
    file_name: str = Field(description="The screenshot it was read from.")
    balance: float | None = Field(
        default=None,
        description=(
            "Balance exactly as drawn, in whichever unit the meter showed -- "
            "see the validation's `mode`. Null when unreadable; this is the "
            "raw reading, `credits`/`cash` are the interpretation."
        ),
    )
    win: float | None = Field(
        default=None,
        description="The WIN cell as drawn. Null is normal between spins.",
    )
    bet: float | None = Field(default=None, description="The BET cell as drawn.")
    credits: SpinMeterFigures = Field(
        default_factory=SpinMeterFigures,
        description=(
            "Same three numbers in credits -- what the paytable is "
            "denominated in, so what an award is compared against."
        ),
    )
    cash: SpinMeterFigures = Field(
        default_factory=SpinMeterFigures,
        description="The same three numbers in money.",
    )
    values: MeterValues | None = Field(
        default=None,
        description=(
            "The whole reading, with per-field confidence and raw text -- "
            "where a failed check's misread digit shows up."
        ),
    )
    error: str | None = Field(
        default=None, description="Why this frame's meter could not be read."
    )
    crop_image: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the meter strip that was read. Null unless "
            "the request asked for images."
        ),
    )


class SpinMeterCheck(BaseModel):
    """One arithmetic relation between two readings, checked in one unit."""

    key: str = Field(
        description=(
            "Stable identifier, e.g. 'bet-deducted-credits' -- unit-suffixed "
            "for relations checked in both."
        )
    )
    unit: SpinMeterUnit | None = Field(
        default=None,
        description=(
            "Which quantity the figures below are in. Null for a relation "
            "that is not about an amount, e.g. whether WIN showed anything."
        ),
    )
    label: str = Field(description="The relation in words.")
    verdict: SpinVerdict = Field(description="Whether the relation held.")
    expected: float | None = Field(
        default=None, description="What the other readings imply it should be."
    )
    actual: float | None = Field(default=None, description="What was read.")
    difference: float | None = Field(
        default=None, description="actual - expected, when both are known."
    )
    detail: str = Field(description="The arithmetic in one line, whatever the verdict.")


class SpinMeterValidation(BaseModel):
    """Every frame's meter, and what the differences between them prove."""

    mode: MeterMode = Field(
        default=MeterMode.UNKNOWN,
        description=(
            "Whether the meter counted money or credits, across every frame. "
            "'cash' wins a disagreement -- money is read positively (a symbol "
            "or fractional amount), credits is inferred from the absence of "
            "both -- and 'unknown' means nothing was readable."
        ),
    )
    currency: str | None = Field(
        default=None,
        description=(
            "Symbol drawn on the values in cash mode, e.g. '$'. '?' means "
            "money was read but no symbol OCR'd -- the yen glyph never reads "
            "at any scale. Null in credits mode or when nothing was read."
        ),
    )
    denomination: DenominationInfo | None = Field(
        default=None,
        description=(
            "What one credit is worth -- converts between the two units "
            "`mode` chooses between. Comes from the game's log via the "
            "paytable, not the strip, since the denomination badge is "
            "unlabelled and does not OCR. Null when the paytable could not "
            "be read."
        ),
    )
    readings: list[SpinMeterReading] = Field(
        default_factory=list,
        description="One per screenshot, in the order they were taken.",
    )
    checks: list[SpinMeterCheck] = Field(
        default_factory=list,
        description="The relations between them; empty when nothing was readable.",
    )
    tolerance: float = Field(
        default=0.0,
        ge=0,
        description=(
            "How far two money amounts may differ and still count equal -- "
            "absorbs the OCR of the last decimal, not a real discrepancy."
        ),
    )
    credit_tolerance: float = Field(
        default=0.0,
        ge=0,
        description=(
            "The same, for checks made in credits -- tighter in proportion "
            "since a credit is a whole number and this only absorbs a "
            "converted figure's rounding."
        ),
    )
    verdict: SpinVerdict = Field(
        description=(
            "'failed' if any check failed, 'indeterminate' if none failed but "
            "one could not be judged, else 'passed'."
        )
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why no meter could be read at all -- no 'cash_meter' region, or "
            "no OCR engine. Null when readings were taken."
        ),
    )


# --- what landed ----------------------------------------------------------


class SpinSymbolReading(BaseModel):
    """One tile of the spin's reels, as the classifier read it."""

    name: str = Field(description="Grid position, e.g. 'r1c1'.")
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    symbol: str | None = Field(
        default=None,
        description=(
            "The symbol code, or null when nothing cleared the confidence "
            "floor. Null is an answer: the game declares more codes than "
            "there is artwork to train on, so a softmax is sometimes shown a "
            "class it has none for."
        ),
    )
    label: str = Field(description="Display name from the game config, or 'unknown'.")
    leading: str | None = Field(
        default=None,
        description=(
            "Code of the leading candidate whether or not it cleared the "
            "floor -- so a rejected tile still says what the model leaned "
            "towards."
        ),
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="That candidate's probability."
    )
    known: bool = Field(description="Whether it cleared the floor.")


class SpinScatterReading(BaseModel):
    """One scatter that landed, and what is printed on it.

    An orb carries either a prize figure or a jackpot tier name, never both;
    a feature scatter carries neither. Its own model rather than fields on
    :class:`SpinSymbolReading` because a scatter is answered by a second
    reader: the classifier names the artwork, OCR reads the prize.
    """

    name: str = Field(description="Grid position, e.g. 'r2c3'.")
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    symbol: str = Field(description="The scatter's code, e.g. 'SC' or 'FG'.")
    label: str = Field(description="Its display name from the game config.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="The classifier's probability for that code on this tile.",
    )
    value: float | None = Field(
        default=None,
        description=(
            "The number printed on the tile, or null when it carries none. "
            "An orb printed with a jackpot tier instead reports it in "
            "`prize_label`; a feature scatter (e.g. free-games) has neither."
        ),
    )
    prize_label: str | None = Field(
        default=None,
        description=(
            "Jackpot tier printed on the orb where it carries a word instead "
            "of a figure -- 'MAJOR', 'MINI', whatever the game draws. Null "
            "when the orb carries a number or nothing. Not matched against a "
            "known list -- reported as read."
        ),
    )
    text: str | None = Field(
        default=None,
        description=(
            "Everything OCR read off the crop, kept beside `value` so a "
            "misread is diagnosable rather than only wrong. Null when OCR "
            "did not run."
        ),
    )
    ocr_confidence: float | None = Field(
        default=None,
        description="OCR's own mean word confidence, 0-100 as the engine reports it.",
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why this tile could not be read at all -- no engine installed, "
            "or the crop was missing. Different from a tile that read as no "
            "digits."
        ),
    )


class SpinReelReading(BaseModel):
    """What landed, read off the picture by the image classifier."""

    split: str = Field(description="Split directory the tiles were read from.")
    architecture: str = Field(description="Which network answered, e.g. 'resnet34'.")
    label: str = Field(description="That network's display name.")
    trained_at: str | None = Field(
        default=None, description="When its checkpoint was written."
    )
    min_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Floor a tile's leading probability had to clear to be named -- "
            "set above where the classes separate, so a line through an "
            "unnamed tile stops there rather than being credited with a run "
            "nothing measured."
        ),
    )
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)
    symbol_grid: list[list[str | None]] = Field(
        default_factory=list,
        description=(
            "The codes on screen, row-major, null where unnamed. Deliberately "
            "the same field name and shape as the image classifier's own "
            "`symbol_grid`, which is where it comes from."
        ),
    )
    label_grid: list[list[str | None]] = Field(
        default_factory=list,
        description="The same matrix in display names, null in the same places.",
    )
    tiles: list[SpinSymbolReading] = Field(
        default_factory=list, description="Every tile, in reading order."
    )
    named: int = Field(default=0, ge=0, description="Tiles that cleared the floor.")
    unknown: int = Field(default=0, ge=0, description="Tiles that did not.")
    summary: str = Field(default="", description="The reading in one line.")
    scatters: list[SpinScatterReading] = Field(
        default_factory=list,
        description=(
            "Every scatter on the grid, in reading order -- the codes the "
            "game config's `scatter_symbols` names. Empty both when none "
            "landed and when the game declares none, which `scatter_summary` "
            "tells apart."
        ),
    )
    scatter_summary: str = Field(
        default="",
        description=(
            "The scatters in one line, e.g. 'SC x2 (12, 50), FG x1'. Empty "
            "when the game declares no scatter codes."
        ),
    )
    output_dir: str | None = Field(
        default=None, description="Directory the ringed reels were written to."
    )
    overlay_file: str | None = Field(
        default=None,
        description=(
            "What the ringed-reels picture was written as. The picture "
            "itself is deliberately not carried -- the codes and confidences "
            "already say what the rings show -- but it is still on disk "
            "beside the tiles it was computed from."
        ),
    )


# --- payline validation ---------------------------------------------------


class SpinLineAward(BaseModel):
    """One payline of the live geometry, evaluated and priced."""

    line: str = Field(description="Line number as the geometry counts it, e.g. '3'.")
    label: str = Field(description="How it is spoken about, e.g. 'Line 3'.")
    positions: list[str] = Field(
        description="Tile names the line runs through, left to right."
    )
    elements: list[list[int]] = Field(
        default_factory=list,
        description=(
            "The line as winGeometry.xml writes it: [reel, position] pairs, "
            "both 0-indexed -- kept beside `positions` so the conversion "
            "between the two can be checked."
        ),
    )
    pays: int = Field(
        ge=0,
        description=(
            "Length of the leading run of like symbols: 0 when reels 1 and 2 "
            "differ, otherwise 2 or more. A tile the classifier could not "
            "name breaks the run there -- 'I could not tell' twice is not a "
            "match."
        ),
    )
    paying: bool = Field(
        description=(
            "Whether the reels landed a run of two or more. Evidence, not a "
            "win -- 'awarded' is whether the paytable pays for it, and the "
            "two differ whenever a symbol's shortest paying run is longer "
            "than what landed."
        )
    )
    awarded: bool = Field(
        default=False,
        description=(
            "Whether this line actually earns anything: the paytable pays "
            "this symbol at this run length. False with 'paying' true is a "
            "run the reels landed and the maths does not pay -- cancelled, "
            "with 'note' saying why."
        ),
    )
    steps: list[PaylineStep] = Field(
        default_factory=list,
        description=(
            "Every adjacent pair on the line, left to right. 'matched' is "
            "judged against 'line_symbol' -- a wild counts as whatever the "
            "run pays as; 'counted' is whether the run got that far. "
            "'similarity' is null -- these tiles were compared by name, not "
            "by pixel likeness."
        ),
    )
    color: str = Field(description="Hex colour the line is drawn in.")
    break_position: str | None = Field(
        default=None,
        description="Tile where the run stopped; null when the whole line matched.",
    )
    symbols: list[str | None] = Field(
        default_factory=list,
        description=(
            "Symbol code at each position of this line, left to right, as the "
            "classifier read it. Null in a cell it was not sure enough of."
        ),
    )
    symbol: str | None = Field(
        default=None,
        description=(
            "The code this line is priced as -- what its wilds stood in for, "
            "not necessarily the tile on reel 1, or the wild's own code when "
            "that combo paid more. Present whenever there is a run."
        ),
    )
    symbol_name: str | None = Field(
        default=None, description="That code's display name, when the config names it."
    )
    leading_wilds: int = Field(
        default=0,
        ge=0,
        description=(
            "How many of the run's leading positions were the wild itself. "
            "Above one it is a combo in its own right, which is why this "
            "line was priced twice -- see 'combo_pays'."
        ),
    )
    combo_id: int | None = Field(
        default=None,
        description="Id of the math.xml combo this run matched, when one did.",
    )
    combo_symbols: list[str] = Field(
        default_factory=list,
        description="That combo's pattern as the file writes it, 'ANY' tail included.",
    )
    combo_pays: int | None = Field(
        default=None,
        ge=0,
        description=(
            "How many positions the combo that paid covers. Equal to 'pays', "
            "except on a wild-led line whose own combo paid more than the "
            "substituted reading, where it is 'leading_wilds' instead. Null "
            "when nothing was awarded."
        ),
    )
    combo_value: float | None = Field(
        default=None,
        description=(
            "The paytable row's own number, before the bet unit multiplies "
            "it -- what the Game Config page shows for this symbol at this "
            "length. Null when the maths pays nothing, which is also when "
            "'awarded' is false."
        ),
    )
    credits: float | None = Field(
        default=None,
        description=(
            "What this line actually awards: combo_value x bet_per_unit, "
            "since a paytable value is a rate per bet unit. Equal to "
            "combo_value only at one credit a unit. Null when the maths pays "
            "nothing, and also when no bet per unit was given."
        ),
    )
    min_pay_length: int | None = Field(
        default=None,
        description=(
            "Shortest run this symbol pays at -- what a cancelled run is "
            "measured against."
        ),
    )
    note: str | None = Field(
        default=None,
        description=(
            "Why this line was priced the way it was: most often that the "
            "symbol's shortest paying run is longer than what landed, or "
            "that a run of wilds was worth more than the symbol they stood "
            "in for."
        ),
    )
    image_data: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of this line drawn over the reels. Populated "
            "for awarded lines only, and only when images were asked for."
        ),
    )


class SpinExpectedAward(BaseModel):
    """What the paytable says the spin should have paid, and whether the meter agrees.
    One number, not a range; ``indeterminate`` when an input was unread."""

    paying_lines: int = Field(ge=0, description="Lines with a run that pays.")
    credits: float = Field(
        default=0.0,
        description=(
            "Total award in credits: awarded lines' own priced figures added "
            "up, each already multiplied by bet_per_unit. 0 when no bet per "
            "unit was given, in which case the verdict is indeterminate."
        ),
    )
    bet_per_unit: int | None = Field(
        default=None,
        description=(
            "Credits staked on each bet unit, as the run was asked to grade "
            "at -- the multiplier behind every line's award. Null leaves the "
            "verdict indeterminate."
        ),
    )
    line_count: int | None = Field(
        default=None, description="Lines the loaded paytable plays."
    )
    denomination_label: str | None = Field(
        default=None,
        description=(
            "The denomination as an operator says it, e.g. '2c' -- for "
            "reading only; the arithmetic runs through `money_per_credit`, "
            "which differs by a factor of a hundred."
        ),
    )
    money_per_credit: float | None = Field(
        default=None,
        description=(
            "One credit in money -- 0.02 on a 2c game. Null when the "
            "denomination was reported but its unit could not be resolved, "
            "leaving the verdict indeterminate rather than priced by a guess."
        ),
    )
    total_bet: float | None = Field(
        default=None,
        description=(
            "Total bet as the meter drew it, in whichever of money and "
            "credits it was showing -- see the meter validation's own `mode`."
        ),
    )
    bet_credits: float | None = Field(
        default=None,
        description=(
            "The bet in credits: total_bet / money_per_credit on a cash "
            "meter, or total_bet itself on a credit meter. Prices nothing "
            "directly -- it is compared against `unit_cost x bet_per_unit` "
            "to confirm the graded rung matches the cabinet's."
        ),
    )
    credits_per_line: float | None = Field(
        default=None,
        description=(
            "bet_credits divided by line_count -- the stake on each line. "
            "Not the bet unit: FortuneOx spreads 88 credits over 40 lines, "
            "2.2 a line and not a rung of any ladder. Reported because it is "
            "what a player reads off the glass."
        ),
    )
    cash: float | None = Field(
        default=None,
        description=(
            "credits x money_per_credit -- the award in money. Null when "
            "money_per_credit or bet_per_unit could not be resolved."
        ),
    )
    unit: SpinMeterUnit = Field(
        default=SpinMeterUnit.CASH,
        description=(
            "Which of the pairs above the verdict was reached on -- the unit "
            "the meter actually drew; the other side is derived from it and "
            "only this one's tolerance means anything."
        ),
    )
    observed_win: float | None = Field(
        default=None,
        description="The WIN cell of the outcome screenshot, in `unit`.",
    )
    observed_credits: float | None = Field(
        default=None,
        description=(
            "The same WIN cell in credits -- the side to compare `credits` "
            "against. Null when the denomination was not known."
        ),
    )
    observed_cash: float | None = Field(
        default=None,
        description="The same WIN cell in money -- the side to compare `cash` against.",
    )
    verdict: SpinVerdict = Field(
        description=(
            "Whether the WIN cell matches the award in `unit`. "
            "'indeterminate' whenever an input to that is missing."
        )
    )
    detail: str = Field(description="The comparison in one line.")


class SpinPaylineValidation(BaseModel):
    """The lines the *running* game plays, checked against the spin's reels."""

    frame: str = Field(description="Screenshot the reels were split out of.")
    split: str | None = Field(
        default=None, description="Split directory the tiles were read from."
    )
    paytable_id: str = Field(description="Paytable folder the geometry came with.")
    paytable_origin: str = Field(
        description="How that paytable was arrived at: 'log', 'requested' or 'only'."
    )
    payline_set_id: str | None = Field(
        default=None, description="Set of winGeometry.xml that is live, e.g. '40'."
    )
    resolved_from: str = Field(
        default="unresolved",
        description=(
            "Which file picked that set: 'game_config' (the paytable's own "
            "NumberOfLines), 'math_default', or 'unresolved'."
        ),
    )
    line_count: int | None = Field(
        default=None, description="Lines the applicable set declares."
    )
    min_confidence: float | None = Field(
        default=None,
        description=(
            "The confidence floor the tiles were named at -- the one tunable "
            "this reading has, in place of the similarity threshold it "
            "replaced. Null when the reels were never read."
        ),
    )
    wild_symbol: str | None = Field(
        default=None,
        description=(
            "The code that was read as whatever the run it landed on was "
            "paying as, so a substituted tile can be told from a matched "
            "one. Null when the game's config declares no "
            "'wild_card_replacement'."
        ),
    )
    summary: str = Field(
        default="",
        description=(
            "What was awarded, in one sentence -- written from the awards "
            "rather than the payline check, since only the paytable knows "
            "which runs pay. 'Pays' is credits here, never the run length."
        ),
    )
    runs_found: int = Field(
        default=0,
        ge=0,
        description="Lines where the reels landed a run of two or more.",
    )
    awarded_lines: int = Field(
        default=0,
        ge=0,
        description=(
            "How many of those the paytable pays for. Fewer than "
            "'runs_found' means some runs were cancelled; see each line's "
            "'note'."
        ),
    )
    unnamed_positions: list[str] = Field(
        default_factory=list,
        description=(
            "Tiles no code was read off, so every line through one stops "
            "there. The first thing to check when a spin that plainly paid "
            "reports no run."
        ),
    )
    pay_lengths: list[int] = Field(
        default_factory=list,
        description="Run lengths the paytable pays for, longest first.",
    )
    lines: list[SpinLineAward] = Field(
        default_factory=list,
        description="Every line of the live set, in order, paying or not.",
    )
    stats: PaylineStats | None = Field(
        default=None,
        description=(
            "The comparison run as a whole. Its four score figures are null "
            "-- codes were compared by name, not by a distribution."
        ),
    )
    expected: SpinExpectedAward | None = Field(
        default=None, description="What those lines should have paid."
    )
    output_dir: str | None = Field(
        default=None, description="Directory the annotated reels were written to."
    )
    output_file: str | None = Field(
        default=None, description="What that picture was written as."
    )
    overlay_image: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the reels with every awarded line drawn over "
            "them. Null unless images were asked for."
        ),
    )
    error: str | None = Field(
        default=None,
        description="Why the lines could not be checked. Null when they were.",
    )


# --- the run --------------------------------------------------------------


class SpinRun(BaseModel):
    """One spin, driven end to end, and everything it proved."""

    run_id: str = Field(description="Timestamp id, which is also its directory name.")
    game: str = Field(description="Filename stem of the game config that was used.")
    label: str = Field(description="Display name that config declares.")
    control: SpinControl = Field(
        default=SpinControl.IDECK,
        description=(
            "Which channel drove this spin and collected its win. Reported "
            "because one service holds both -- a page showing the last run "
            "may be showing one another page started."
        ),
    )
    state: SpinRunState = Field(
        description="Whether the run is going, and how it went."
    )
    outcome: SpinOutcome = Field(description="What the spin did.")
    message: str = Field(description="The run's current position, in one line.")
    started_at: datetime = Field(description="When the run began.")
    finished_at: datetime | None = Field(
        default=None, description="Null while it is still going."
    )
    duration_ms: int = Field(ge=0, description="Elapsed so far, or in total.")
    steps: list[SpinStep] = Field(
        description="The whole sequence, in order, including steps not reached yet."
    )
    frames: list[SpinFrame] = Field(
        default_factory=list,
        description=(
            "Screenshots taken: two on a losing spin, three on a winning one."
        ),
    )
    events: list[SpinLogEvent] = Field(
        default_factory=list,
        description=(
            "Recognised game-log lines seen while the run waited, newest "
            "last. Capped by ANALYZE_SPIN_MAX_EVENTS."
        ),
    )
    recording: SpinRecording | None = Field(
        default=None, description="The video, once the recording has stopped."
    )
    tile_clips: TileClipSet | None = Field(
        default=None,
        description=(
            "One short video per reel position, filmed during a win "
            "presentation. Only present on a recording, winning spin -- null "
            "is the ordinary case. Not a step of the sequence: a filming "
            "failure shows on `errors`, not as a failed stage."
        ),
    )
    meter: SpinMeterValidation | None = Field(
        default=None, description="Null until the cash meter step has run."
    )
    reels: SpinReelReading | None = Field(
        default=None,
        description=(
            "The symbols the classifier read off the result screenshot. "
            "Null until that step has run. Its own block rather than part "
            "of the payline validation, since it is worth having even when "
            "the lines could not be checked."
        ),
    )
    paylines: SpinPaylineValidation | None = Field(
        default=None, description="Null until the payline step has run."
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Everything that went wrong, in order, deduplicated.",
    )


class SpinAnalysisState(BaseModel):
    """What the service has: the run in progress, or the last one it finished."""

    active: bool = Field(description="Whether a run is in progress right now.")
    run: SpinRun | None = Field(
        default=None,
        description=(
            "The run in progress, or the most recent one; null before the first."
        ),
    )
