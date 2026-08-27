"""Payloads for Analyze Spin: one orchestrated spin, and the two validations
run over what it produced.

Three shapes are worth reading before the rest:

* :class:`SpinStep` is the *plan* as much as the progress. Every step of a run
  exists from the moment it starts, ``pending`` until it happens, so the UI
  renders the whole sequence up front and a run that dies on step four says
  which later steps never ran rather than simply stopping.
* :class:`SpinMeterCheck` carries ``expected`` beside ``actual``. A cash-meter
  check that only said "failed" would be unactionable: the digits it read and
  the arithmetic it did are the answer, and the verdict is a consequence.
* :class:`SpinLineAward` lists *candidate* awards, not one. A screenshot says
  which tiles match each other, never which symbol they are, so a three-long
  run maps to every paytable row that pays at three -- and narrowing that to
  one would be inventing a reading nothing measured.

Image fields are populated only when the caller asks for them
(``include_images``). The progress stream never carries them: a snapshot goes
out on every step transition, and forty line pictures per push would make the
stream the slowest part of a spin.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.meter import MeterValues
from app.schemas.paylines import PaylineStats, PaylineStep

__all__ = [
    "SpinAnalysisState",
    "SpinExpectedAward",
    "SpinFrame",
    "SpinLineAward",
    "SpinLineAwardCandidate",
    "SpinLogEvent",
    "SpinMeterCheck",
    "SpinMeterReading",
    "SpinMeterValidation",
    "SpinOutcome",
    "SpinPaylineValidation",
    "SpinRecording",
    "SpinRun",
    "SpinRunState",
    "SpinStartRequest",
    "SpinStep",
    "SpinStepState",
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
    """One of the two numbers could not be read, so there was no comparison to
    make. Never folded into ``failed``: an unreadable meter and a wrong balance
    are different problems with different fixes."""


class SpinStartRequest(BaseModel):
    """Body of ``POST /start``. Optional, so the dashboard's default press and a
    bare curl both work unchanged."""

    record: bool | None = Field(
        default=None,
        description=(
            "Whether this run also records a video of itself. Omit to use "
            "ANALYZE_SPIN_RECORD; a value here overrides it for this run only."
        ),
    )


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
            "'IDECK_PRESS_NOT_CONFIRMED' -- the same code the equivalent direct "
            "request would have returned."
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
            "Whether the frame came back with nothing in it. A black capture is "
            "not an error anywhere -- OBS renders nothing for a moment after its "
            "window source is re-pointed -- so it is retried and then reported, "
            "because every reading taken off it is meaningless rather than dark."
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


class SpinMeterReading(BaseModel):
    """The meter as one screenshot drew it."""

    frame: str = Field(description="Which frame this was read off, by key.")
    label: str = Field(description="That frame's moment, for a table heading.")
    file_name: str = Field(description="The screenshot it was read from.")
    balance: float | None = Field(
        default=None,
        description=(
            "The balance, whichever of cash and credits the meter was showing. "
            "Null when it could not be read."
        ),
    )
    win: float | None = Field(
        default=None,
        description="The WIN cell. Null is normal between spins -- it is empty.",
    )
    bet: float | None = Field(default=None, description="The BET cell.")
    values: MeterValues | None = Field(
        default=None,
        description=(
            "The whole reading, with per-field confidence and the raw text. "
            "Carried because a failed check is usually a misread digit, and this "
            "is where that shows."
        ),
    )
    error: str | None = Field(
        default=None, description="Why this frame's meter could not be read."
    )
    crop_image: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the meter strip that was read. Null unless the "
            "request asked for images."
        ),
    )


class SpinMeterCheck(BaseModel):
    """One arithmetic relation between two readings, checked."""

    key: str = Field(description="Stable identifier, e.g. 'bet-deducted'.")
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
            "How far two amounts may differ and still be called equal -- "
            "absorbing the OCR of the last decimal, not a real discrepancy."
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
            "Why no meter could be read at all -- no 'cash_meter' region, or no "
            "OCR engine. Null when readings were taken."
        ),
    )


# --- payline validation ---------------------------------------------------


class SpinLineAwardCandidate(BaseModel):
    """One paytable row that pays at this line's run length."""

    codes: list[str] = Field(description="Symbol codes sharing the row.")
    names: list[str | None] = Field(description="Their display names, in step.")
    value: float = Field(
        description=(
            "Credits the row pays for this run length, per line at one credit "
            "staked on it."
        )
    )


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
            "The line as winGeometry.xml writes it: [reel, position] pairs, both "
            "0-indexed. Carried beside 'positions' so the conversion between the "
            "two forms can be checked."
        ),
    )
    pays: int = Field(
        ge=0,
        description=(
            "Length of the leading run of matching tiles: 0 when the first two "
            "reels differ, otherwise 2 or more."
        ),
    )
    paying: bool = Field(
        description=(
            "Whether the picture found a run of two or more. Evidence, not a "
            "win -- 'awarded' is whether the paytable pays for it, and the two "
            "differ whenever a symbol's shortest paying run is longer than what "
            "landed."
        )
    )
    awarded: bool = Field(
        default=False,
        description=(
            "Whether this line actually earns anything: the paytable pays this "
            "symbol at this run length. False with 'paying' true is a run the "
            "picture found and the maths does not pay -- cancelled, with 'note' "
            "saying why."
        ),
    )
    steps: list[PaylineStep] = Field(
        default_factory=list,
        description=(
            "Every adjacent pair on the line with its cosine similarity, left to "
            "right. This is the measurement the run was read from: 'matched' is "
            "whether the score cleared the threshold, 'counted' whether the "
            "left-to-right read got that far."
        ),
    )
    color: str = Field(description="Hex colour the line is drawn in.")
    break_position: str | None = Field(
        default=None,
        description="Tile where the run stopped; null when the whole line matched.",
    )
    candidates: list[SpinLineAwardCandidate] = Field(
        default_factory=list,
        description=(
            "Every paytable row paying at this run length -- what the picture "
            "alone narrows the award to. More than one because similarity says "
            "these tiles are alike, never which symbol they are. Kept even once "
            "the stops have named the symbol, so the narrowing is visible."
        ),
    )
    symbols: list[str | None] = Field(
        default_factory=list,
        description=(
            "Symbol code at each position of this line, left to right, read from "
            "the reel stops the game logged. Empty when no stops were seen."
        ),
    )
    symbol: str | None = Field(
        default=None,
        description=(
            "The code the leading run is made of, from those stops -- the one "
            "thing the picture cannot say, and what makes the award exact."
        ),
    )
    symbol_name: str | None = Field(
        default=None, description="That code's display name, when the config names it."
    )
    run_from_stops: int = Field(
        default=0,
        ge=0,
        description=(
            "Leading run of identical codes in 'symbols' -- the maths' own answer "
            "to what 'pays' measured off the picture. Wilds are not substituted, "
            "so the two numbers measure the same thing."
        ),
    )
    agrees: bool | None = Field(
        default=None,
        description=(
            "Whether 'run_from_stops' equals 'pays'. Null when no stops were "
            "seen. False is the interesting case: the reels drew something other "
            "than what the maths says landed -- or a wild is standing in, which "
            "similarity cannot see."
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
    credits: float | None = Field(
        default=None,
        description=(
            "The award this line earns, per line at one credit staked, once the "
            "symbol is known. Null while it is still a range."
        ),
    )
    value_min: float | None = Field(
        default=None, description="Cheapest possible award; null when there is none."
    )
    value_max: float | None = Field(
        default=None, description="Dearest possible award; null when there is none."
    )
    exact: bool = Field(
        default=False,
        description=(
            "Whether the award is one number rather than a range -- because the "
            "stops named the symbol, or because every candidate pays the same."
        ),
    )
    min_pay_length: int | None = Field(
        default=None,
        description=(
            "Shortest run this symbol pays at, when the symbol is known -- what "
            "a cancelled run is measured against."
        ),
    )
    note: str | None = Field(
        default=None,
        description=(
            "Why this line has no award despite a run -- most often that the "
            "symbol's shortest paying run is longer than what landed."
        ),
    )
    image_data: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of this line drawn over the reels. Populated for "
            "paying lines only, and only when images were asked for."
        ),
    )


class SpinExpectedAward(BaseModel):
    """What the paytable says the spin should have paid, and whether the meter
    agrees.

    Every intermediate is carried rather than only the answer, because the
    conversion from paytable credits to money on the glass runs through the
    denomination and the line count -- and a wrong verdict is nearly always one
    of those rather than the pay itself.
    """

    paying_lines: int = Field(ge=0, description="Lines with a run that pays.")
    credits_min: float = Field(
        default=0.0, description="Total award in paytable credits, cheapest reading."
    )
    credits_max: float = Field(
        default=0.0, description="Total award in paytable credits, dearest reading."
    )
    exact: bool = Field(
        default=False,
        description=(
            "Whether every paying line's candidates agreed, making the total one "
            "number rather than a range."
        ),
    )
    line_count: int | None = Field(
        default=None, description="Lines the loaded paytable plays."
    )
    denomination: float | None = Field(
        default=None, description="Denomination the game's log reported."
    )
    total_bet: float | None = Field(
        default=None, description="Total bet as the meter drew it, in money."
    )
    bet_credits: float | None = Field(
        default=None, description="total_bet divided by denomination."
    )
    credits_per_line: float | None = Field(
        default=None, description="bet_credits divided by line_count."
    )
    cash_min: float | None = Field(
        default=None,
        description=(
            "credits_min x credits_per_line x denomination -- the award in money, "
            "on the assumption that a paytable value is credits per line at one "
            "credit staked."
        ),
    )
    cash_max: float | None = Field(
        default=None, description="The same conversion applied to credits_max."
    )
    observed_win: float | None = Field(
        default=None, description="The WIN cell of the outcome screenshot."
    )
    verdict: SpinVerdict = Field(
        description=(
            "Whether observed_win falls between cash_min and cash_max. "
            "'indeterminate' whenever any input above is missing."
        )
    )
    detail: str = Field(description="The comparison in one line.")


class SpinPaylineValidation(BaseModel):
    """The lines the *running* game plays, checked against the spin's reels.

    The patterns come from the game's own ``winGeometry.xml`` by way of the
    paytable its log named -- not from the ``paylines`` block of the config in
    this repo, which is a hand-copy kept for machines without the game
    installed. Which set of that file is in play comes from the paytable's own
    ``NumberOfLines``, and ``resolved_from`` says so.
    """

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
    threshold: float = Field(
        default=0.0,
        description=(
            "Cosine similarity two tiles had to reach to count as one symbol."
        ),
    )
    summary: str = Field(
        default="",
        description=(
            "What was awarded, in one sentence -- written from the awards rather "
            "than taken from the payline check, because the check knows what runs "
            "it found and only the paytable knows which of them pay. 'Pays' is "
            "credits here, never the run length: they are different numbers, and "
            "one word for both is how a run of five gets read as five credits."
        ),
    )
    runs_found: int = Field(
        default=0,
        ge=0,
        description="Lines where the picture found a run of two or more.",
    )
    awarded_lines: int = Field(
        default=0,
        ge=0,
        description=(
            "How many of those the paytable pays for. Fewer than 'runs_found' "
            "means some runs were cancelled; see each line's 'note'."
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
            "The comparison run as a whole, including how well its scores separated."
        ),
    )
    stops: list[int] = Field(
        default_factory=list,
        description=(
            "Where each reel landed, as the game logged it. Used to *name* the "
            "symbols a run is made of, never to decide what paid -- reading the "
            "answer out of the log would agree with the log by construction."
        ),
    )
    stops_log_line: str | None = Field(
        default=None, description="The whole line those stops were read out of."
    )
    stop_anchor: str | None = Field(
        default=None,
        description=(
            "Which visible row the stop index was taken to be: 'top', 'middle' or "
            "'bottom'. The maths files state the index and never the convention."
        ),
    )
    stop_anchor_decided: bool = Field(
        default=False,
        description=(
            "Whether that anchor was chosen by evidence -- the one whose "
            "predicted matching pairs agreed best with the measured ones -- "
            "rather than settled by a tie or pinned by configuration."
        ),
    )
    stop_agreed: int | None = Field(
        default=None,
        description="Compared pairs where the derived grid and the picture agreed.",
    )
    stop_compared: int | None = Field(
        default=None, description="Pairs there were to agree about."
    )
    symbol_grid: list[list[str]] = Field(
        default_factory=list,
        description=(
            "The symbols those stops put on screen, row-major -- the same shape "
            "as the reel grid's own 'positions'. Empty when no stops were seen."
        ),
    )
    stops_error: str | None = Field(
        default=None,
        description=(
            "Why the symbols could not be named. Null when they were, and never "
            "fatal: the lines are still checked off the picture without them, and "
            "the awards simply stay ranges."
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
            "Base64 data URI of the reels with every paying line drawn over "
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
            "Recognised game-log lines seen while the run waited, newest last. "
            "Capped by ANALYZE_SPIN_MAX_EVENTS."
        ),
    )
    recording: SpinRecording | None = Field(
        default=None, description="The video, once the recording has stopped."
    )
    meter: SpinMeterValidation | None = Field(
        default=None, description="Null until the cash meter step has run."
    )
    paylines: SpinPaylineValidation | None = Field(
        default=None, description="Null until the payline step has run."
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Everything that went wrong, in order, deduplicated.",
    )


class SpinAnalysisState(BaseModel):
    """What the service has: the run in progress, or the last one it finished.

    ``run`` outliving its own completion is deliberate -- the report is the
    point of the feature, so reloading the page after a spin has to still show
    it. ``active`` is what a caller branches on, not ``run`` being present.
    """

    active: bool = Field(description="Whether a run is in progress right now.")
    run: SpinRun | None = Field(
        default=None,
        description=(
            "The run in progress, or the most recent one; null before the first."
        ),
    )
