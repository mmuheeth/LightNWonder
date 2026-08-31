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
* :class:`SpinLineAward` names the symbol its run is made of and prices it
  exactly. That is new: the run used to be measured by cosine similarity, which
  says which tiles match each other and never which symbol they are, so an award
  could only be narrowed to every paytable row paying at that length. The symbol
  now comes from :class:`SpinReelReading` -- the image classifier reading the
  tiles -- so a run is one row and one number.

Image fields are populated only when the caller asks for them
(``include_images``). The progress stream never carries them: a snapshot goes
out on every step transition, and forty line pictures per push would make the
stream the slowest part of a spin.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.meter import MeterMode, MeterValues
from app.schemas.paylines import PaylineStats, PaylineStep
from app.schemas.paytable import DenominationInfo

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


class SpinMeterUnit(StrEnum):
    """Which of the two quantities a figure or a check is expressed in."""

    CREDITS = "credits"
    CASH = "cash"


class SpinMeterFigures(BaseModel):
    """The strip's three numbers in one unit.

    A meter draws one of the two and the other follows from the denomination, so
    both are always reported: the paytable talks in credits while the glass may be
    drawing money, and a reader should not have to do the conversion to compare
    them. Which one was *read* is the validation's ``mode``; the other is derived.
    All three are null when the denomination is unknown, since then there is
    nothing to convert with.
    """

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
            "The balance exactly as drawn, in whichever of cash and credits the "
            "meter was showing -- see the validation's `mode`. Null when it could "
            "not be read. This is the reading; `credits` and `cash` are the "
            "interpretation."
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
            "The same three numbers in credits -- what the paytable is denominated "
            "in, so what an award is compared against."
        ),
    )
    cash: SpinMeterFigures = Field(
        default_factory=SpinMeterFigures,
        description="The same three numbers in money.",
    )
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
    """One arithmetic relation between two readings, checked in one unit."""

    key: str = Field(
        description=(
            "Stable identifier, e.g. 'bet-deducted-credits'. Unit-suffixed for the "
            "relations that are checked in both."
        )
    )
    unit: SpinMeterUnit | None = Field(
        default=None,
        description=(
            "Which quantity the figures below are in. Null for a relation that is "
            "not about an amount at all -- whether the WIN cell showed anything is "
            "the same question in either unit."
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
    """Every frame's meter, and what the differences between them prove.

    ``mode``, ``currency`` and ``denomination`` come first because they are the
    units every number below is in: the same ``1250`` is 1250 credits or 1250 of
    some currency, and which one it is changes what the balance, the win and the
    bet *mean*. One answer for the run rather than one per frame, since a machine
    does not change denomination mid-spin -- the per-frame reading is still on
    ``readings[].values`` for a run where the frames disagreed.

    The three are not three readings of one thing. ``mode`` and ``currency`` are
    read off the strip; ``denomination`` is what converts between the two units
    ``mode`` chooses between, and it comes from the game's log.

    Because it does convert between them, **every reading carries both units and
    every arithmetic relation is checked in both** -- once over ``credits`` and
    once over ``cash``, each with its own tolerance, and ``checks[].unit`` says
    which. They are the same equation scaled, so neither can fail alone for a real
    reason; what differs is the precision they are read at, and a relation that
    holds in money to two decimals while being a whole credit out is a statement
    about the OCR rather than about the game. A unit whose figures are unknown
    contributes no checks rather than indeterminate ones.
    """

    mode: MeterMode = Field(
        default=MeterMode.UNKNOWN,
        description=(
            "Whether the meter was counting money or credits, across every frame "
            "read. 'cash' wins a disagreement: money is read *positively* (a "
            "currency symbol, or an amount with a fractional part) while credits "
            "is inferred from the absence of both, so one frame whose digits "
            "happened to be whole and whose symbol did not OCR is not evidence "
            "of a credit meter. 'unknown' means nothing was readable."
        ),
    )
    currency: str | None = Field(
        default=None,
        description=(
            "The symbol drawn on the values in cash mode, e.g. '$'. '?' means "
            "money was read but no symbol Tesseract will name -- the yen glyph "
            "these games draw reads as nothing at every mode and scale. Null in "
            "credits mode and when nothing was read."
        ),
    )
    denomination: DenominationInfo | None = Field(
        default=None,
        description=(
            "What one credit is worth, which is the third unit these numbers are "
            "in: it converts between the two the mode chooses between. Comes from "
            "the game's log by way of the paytable, not off the strip -- the "
            "denomination badge the games draw is unlabelled and its unit glyph "
            "does not OCR. Null when the paytable could not be read."
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
            "How far two money amounts may differ and still be called equal -- "
            "absorbing the OCR of the last decimal, not a real discrepancy."
        ),
    )
    credit_tolerance: float = Field(
        default=0.0,
        ge=0,
        description=(
            "The same, for the checks made in credits. Separate because a credit "
            "is a whole number: this absorbs a converted figure's rounding and "
            "nothing else, so it is far tighter than the money one in proportion."
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


# --- what landed ----------------------------------------------------------


class SpinSymbolReading(BaseModel):
    """One tile of the spin's reels, as the classifier read it.

    ``leading`` and ``confidence`` are present whether or not the tile was named,
    because a rejection with no figure behind it is not checkable: the grid means
    "the model was sure", and this row means "here is what it thought".
    """

    name: str = Field(description="Grid position, e.g. 'r1c1'.")
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    symbol: str | None = Field(
        default=None,
        description=(
            "The symbol code, or null when nothing cleared the confidence floor. "
            "Null is an answer: the game declares more symbol codes than there is "
            "artwork to train on, so the model is regularly shown a picture it has "
            "no class for, and a softmax can only spread rather than abstain."
        ),
    )
    label: str = Field(description="Display name from the game config, or 'unknown'.")
    leading: str | None = Field(
        default=None,
        description=(
            "Code of the leading candidate whether or not it cleared the floor -- "
            "so a rejected tile still says what the model leaned towards."
        ),
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="That candidate's probability."
    )
    known: bool = Field(description="Whether it cleared the floor.")


class SpinReelReading(BaseModel):
    """What landed, read off the picture by the image classifier.

    This is the one measurement the payline validation now rests on, and it
    replaced two older ones at once:

    * **cosine similarity between tiles**, which said which tiles were alike and
      never which symbol they were -- so an award could only be narrowed to every
      paytable row paying at that run length, not priced;
    * **the reel stops in the game's own log**, which named the symbols by
      agreeing with the game. A reading taken out of the log cannot catch a reel
      drawing the wrong symbol, because it never looked at the reel.

    Both are still in the tree and neither is used here. What is left is one
    source: the tiles the reel grid wrote, named by a network that only ever saw
    the picture.

    Carries no ``error``, unlike the two validations: it exists only when the
    reading succeeded. A failure is on its own step (``classify``) and again on
    the payline validation's ``error``, which is what has to explain why the lines
    could not be checked.
    """

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
            "Floor a tile's leading probability had to clear to be named. Set "
            "above where the classes separate on purpose, so a tile the model is "
            "only fairly sure of comes back unnamed -- and a line through it stops "
            "there rather than being credited with a run nothing measured."
        ),
    )
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)
    symbol_grid: list[list[str | None]] = Field(
        default_factory=list,
        description=(
            "The codes on screen, row-major, null where a tile was not named. "
            "Deliberately the same field name and shape as the image classifier's "
            "own `symbol_grid`, which is where it comes from."
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
    output_dir: str | None = Field(
        default=None, description="Directory the ringed reels were written to."
    )
    overlay_file: str | None = Field(
        default=None,
        description=(
            "What that picture was written as. The picture itself is deliberately "
            "*not* carried: the codes and their confidences say everything the "
            "rings do, and a data URI of the reels on every report is weight for "
            "something the dashboard does not draw. It is still on disk beside "
            "the tiles it was computed from."
        ),
    )


# --- payline validation ---------------------------------------------------


class SpinLineAward(BaseModel):
    """One payline of the live geometry, evaluated and priced.

    Two judgements, kept apart. **What landed** is ``pays`` -- the leading run of
    positions the classifier named with the same code, with the codes themselves
    on ``steps``. **Whether it pays** is ``awarded``, which is the paytable's
    answer and nobody else's: a run of two of a symbol that pays from three is a
    real run and no win.
    """

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
            "Length of the leading run of like symbols: 0 when the first two "
            "reels differ, otherwise 2 or more. A tile the classifier could not "
            "name breaks the run there -- 'I could not tell' twice is not a match."
        ),
    )
    paying: bool = Field(
        description=(
            "Whether the reels landed a run of two or more. Evidence, not a "
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
            "reels landed and the maths does not pay -- cancelled, with 'note' "
            "saying why."
        ),
    )
    steps: list[PaylineStep] = Field(
        default_factory=list,
        description=(
            "Every adjacent pair on the line, left to right, with the code read "
            "off each tile. 'matched' is whether the two codes are equal and both "
            "were read; 'counted' whether the left-to-right run got that far. "
            "'similarity' is null -- these tiles were compared by name, not by "
            "how alike their pixels are."
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
            "The code the leading run is made of. Present whenever there is a "
            "run, since a run only exists when two tiles were named the same."
        ),
    )
    symbol_name: str | None = Field(
        default=None, description="That code's display name, when the config names it."
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
            "The award this line earns, per line at one credit staked. Null when "
            "the maths pays nothing for this symbol at this length, which is also "
            "when 'awarded' is false and 'note' says so."
        ),
    )
    min_pay_length: int | None = Field(
        default=None,
        description=(
            "Shortest run this symbol pays at -- what a cancelled run is measured "
            "against."
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
            "awarded lines only, and only when images were asked for."
        ),
    )


class SpinExpectedAward(BaseModel):
    """What the paytable says the spin should have paid, and whether the meter
    agrees.

    The award is one multiplication -- ``credits x money_per_credit`` -- because a
    paytable combo's value *is* the award in credits and not a per-line rate to be
    scaled by the stake. The bet in credits and the stake per line are reported
    beside it as context, since a wrong verdict is usually a misread bet or a
    misresolved denomination and those are where it shows, but neither is an input.

    The denomination arrives as two fields on purpose. The game's log reports it
    as a count of cents (``denom[2.000]`` on a ``-2c-`` paytable), so the number
    to divide a money bet by is ``money_per_credit`` (0.02) and never the value
    itself -- the two differ by a factor of a hundred, and the wrong one produces
    a plausible-looking figure rather than an error. ``denomination_label`` is the
    operator-facing form and takes no part in the arithmetic.

    One number rather than a range, unlike the version this replaced: the
    classifier names the symbol on every tile, so each awarded line resolves to
    one paytable row and one value. There is nothing left to be uncertain
    *between* -- an unreadable input makes the verdict ``indeterminate`` instead.
    """

    paying_lines: int = Field(ge=0, description="Lines with a run that pays.")
    credits: float = Field(default=0.0, description="Total award in paytable credits.")
    line_count: int | None = Field(
        default=None, description="Lines the loaded paytable plays."
    )
    denomination_label: str | None = Field(
        default=None,
        description=(
            "The denomination as an operator says it, e.g. '2c'. For reading, not "
            "for arithmetic -- the number the conversion runs through is "
            "`money_per_credit`, and they differ by a factor of a hundred."
        ),
    )
    money_per_credit: float | None = Field(
        default=None,
        description=(
            "One credit in money -- 0.02 on a 2c game. Null when the denomination "
            "was reported but its unit could not be resolved, which leaves the "
            "verdict indeterminate rather than priced by a guess."
        ),
    )
    total_bet: float | None = Field(
        default=None,
        description=(
            "Total bet as the meter drew it, in whichever of money and credits it "
            "was showing -- see the meter validation's own `mode`."
        ),
    )
    bet_credits: float | None = Field(
        default=None,
        description=(
            "The bet in credits: total_bet divided by money_per_credit on a cash "
            "meter, and total_bet itself on a credit meter, which is already "
            "counting them. Context, not a step -- it prices nothing below, and "
            "is carried because it is the one figure that checks the denomination "
            "against the cabinet's own declared minimum bet."
        ),
    )
    credits_per_line: float | None = Field(
        default=None,
        description=(
            "bet_credits divided by line_count -- the stake on each line. Takes no "
            "part in the award: a paytable value is the award, not a per-line rate "
            "to be scaled by the stake."
        ),
    )
    cash: float | None = Field(
        default=None,
        description=(
            "credits x money_per_credit -- the award in money, and the whole "
            "conversion. Null only when money_per_credit could not be resolved; it "
            "needs neither the bet nor the line count."
        ),
    )
    unit: SpinMeterUnit = Field(
        default=SpinMeterUnit.CASH,
        description=(
            "Which of the pairs above the verdict was reached on: the unit the "
            "meter was actually drawing, since the other side of every pair is "
            "derived from that one and only this one's tolerance means anything."
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
            "Whether the WIN cell matches the award in `unit`. 'indeterminate' "
            "whenever an input to that is missing."
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

    What the lines are read *by* is :class:`SpinReelReading`, on the run beside
    this: the symbol codes the image classifier named each tile with. Neither the
    cosine similarity between tiles nor the reel stops in the game's log takes
    part -- the first could not name a symbol and the second agreed with the game
    by construction.
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
    min_confidence: float | None = Field(
        default=None,
        description=(
            "The confidence floor the tiles were named at -- the one tunable this "
            "reading has, in place of the similarity threshold it replaced. Null "
            "when the reels were never read."
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
        description="Lines where the reels landed a run of two or more.",
    )
    awarded_lines: int = Field(
        default=0,
        ge=0,
        description=(
            "How many of those the paytable pays for. Fewer than 'runs_found' "
            "means some runs were cancelled; see each line's 'note'."
        ),
    )
    unnamed_positions: list[str] = Field(
        default_factory=list,
        description=(
            "Tiles no code was read off, so every line through one stops there. "
            "The first thing to check when a spin that plainly paid reports no "
            "run: the floor may simply be above what the model managed."
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
            "The comparison run as a whole. Its four score figures are null: "
            "codes were compared, and there is no distribution to separate."
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
    reels: SpinReelReading | None = Field(
        default=None,
        description=(
            "The symbols the classifier read off the result screenshot. Null "
            "until that step has run. Its own block rather than part of the "
            "payline validation because it is one reading of one picture, and it "
            "is worth having even when the lines could not be checked."
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
