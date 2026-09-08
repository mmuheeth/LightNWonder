"""Request and response models for the payline check."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PaylineCheckRequest",
    "PaylineCheckResult",
    "PaylineLayout",
    "PaylineLine",
    "PaylineMethod",
    "PaylineOverlay",
    "PaylineSetOption",
    "PaylineSource",
    "PaylineStats",
    "PaylineStep",
]


class PaylineMethod(StrEnum):
    """How two tiles were decided to be the same symbol."""

    SIMILARITY = "similarity"
    """Cosine similarity between the two pictures, cut at a threshold. Says the
    tiles are alike and never which symbol they are, so an award read this way
    is only ever narrowed to every paytable row paying at that run length."""

    SYMBOL = "symbol"
    """The symbol codes a classifier read off each tile, compared for equality or
    by substituting the wild. Names the symbol as well as the run, so an award is
    one row and one number. Two *unnamed* tiles are never a match: "I could not
    tell" twice is not "the same symbol", so a line through a tile below the
    confidence floor stops there rather than being credited with a run nothing
    measured. This is also the only method a wild can be read by -- see
    ``wild_symbol`` on the result."""


class PaylineSetOption(BaseModel):
    """One bet configuration the active game declares."""

    name: str = Field(description="The key it is declared under, e.g. '5'.")
    label: str = Field(description="How it reads in a dropdown, e.g. '5 lines'.")


class PaylineSource(BaseModel):
    """The split the check was run against."""

    split: str = Field(
        description=(
            "Directory name of the split under 'obs-captured-files/grid/', "
            "which is the stem of the screenshot it came from."
        )
    )
    written_at: datetime = Field(description="When the split was written.")
    rows: int = Field(ge=1, description="Symbol positions per reel in the split.")
    columns: int = Field(ge=1, description="Reels across in the split.")
    tile_width: int = Field(ge=1, description="Width of every tile, in pixels.")
    tile_height: int = Field(ge=1, description="Height of every tile, in pixels.")
    width: int = Field(ge=1, description="Reels crop width, in pixels.")
    height: int = Field(ge=1, description="Reels crop height, in pixels.")


class PaylineStep(BaseModel):
    """One adjacent pair on a line. ``matched`` is whether the run continued across it;
    ``counted`` is whether the leading run got this far."""

    left: str = Field(description="Position name of the left tile, e.g. 'r2c1'.")
    right: str = Field(description="Position name of the right tile, e.g. 'r2c2'.")
    similarity: float | None = Field(
        default=None,
        description=(
            "Cosine similarity of the two tiles, in [-1, 1]. Read it against "
            "the run's matched_min/rejected_max, not as a plain fraction. Null "
            "when the pair was compared by symbol code, where there is no score "
            "to read -- two codes are equal or they are not."
        ),
    )
    left_symbol: str | None = Field(
        default=None,
        description=(
            "Symbol code read off the left tile. Null on a similarity check, "
            "which cannot name a tile, and on a tile no classifier was sure of."
        ),
    )
    right_symbol: str | None = Field(
        default=None, description="The same for the right tile."
    )
    line_symbol: str | None = Field(
        default=None,
        description=(
            "The code the leading run was paying as when it reached this pair, "
            "which is what the right tile was judged against. The wild appears "
            "here while a run has been wild all the way, until the symbol it "
            "stood in for lands. Null on a similarity check, which names no "
            "tile, and on a step past the break, where there is no run."
        ),
    )
    matched: bool = Field(
        description=(
            "Whether the leading run continued across this pair. On a counted "
            "step that is the right tile against 'line_symbol'; on one past the "
            "break it falls back to the pairwise question -- the score reached "
            "the threshold, or the two codes are one symbol."
        )
    )
    counted: bool = Field(description="Whether the leading run reached this step.")


class PaylineLine(BaseModel):
    """One winning pattern, evaluated."""

    name: str = Field(description="The line's own number as declared, e.g. '3'.")
    label: str = Field(description="How it is spoken about, e.g. 'Line 3'.")
    positions: list[str] = Field(
        description="Position names the line runs through, left to right."
    )
    pays: int = Field(
        ge=0,
        description=(
            "How many positions from the left are the same symbol, the wild "
            "read as whatever the run is paying: 0 when the first two reels "
            "differ, otherwise 2 or more."
        ),
    )
    paying: bool = Field(description="Whether the line paid at all -- pays >= 2.")
    matched_positions: list[str] = Field(
        description="The leading run of positions that pays, empty when none does."
    )
    symbols: list[str | None] = Field(
        default_factory=list,
        description=(
            "Symbol code at each position, left to right, when the tiles were "
            "compared by code. Empty for a similarity check, which says the tiles "
            "are alike and never which symbol they are; null in a cell no "
            "classifier was sure enough of to name."
        ),
    )
    symbol: str | None = Field(
        default=None,
        description=(
            "The code the leading run pays as -- the symbol its wilds stood in "
            "for, or the wild's own code when every position of it was wild. "
            "Not 'symbols[0]': a line that lands a wild on reel 1 is not a line "
            "of wilds. Null when the line pays nothing, and on a similarity "
            "check, which names no symbol."
        ),
    )
    leading_wilds: int = Field(
        default=0,
        ge=0,
        description=(
            "How many of the run's leading positions were the wild itself. A "
            "caller holding the paytable may price that as its own combo and "
            "take the better of the two -- four wilds then an Ox is five Ox or "
            "four wilds, and those are different money."
        ),
    )
    color: str = Field(description="Hex colour this line is drawn in on the overlay.")
    steps: list[PaylineStep] = Field(
        description="Every adjacent pair on the line, left to right."
    )
    break_position: str | None = Field(
        default=None,
        description=(
            "Position where the leading run stopped; null when the whole line paid."
        ),
    )
    image_data: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of this line drawn over the reels, marked at "
            "'break_position'. Null when the request asked for files only."
        ),
    )


class PaylineStats(BaseModel):
    """The run as a whole: what paid, and how well the scores separated."""

    lines: int = Field(ge=0, description="Lines in the set that was checked.")
    paying: int = Field(ge=0, description="How many of them paid.")
    comparisons: int = Field(
        ge=0,
        description=(
            "Distinct adjacent pairs compared -- a pair two lines run through is "
            "one comparison, so the middle of the grid is not weighted twice."
        ),
    )
    matches: int = Field(ge=0, description="Pairs decided to be the same symbol.")
    best_line: str | None = Field(
        default=None, description="Name of the longest-paying line, or null."
    )
    best_pays: int = Field(ge=0, description="Positions the best line paid on.")
    score_min: float | None = Field(
        default=None,
        description=(
            "Lowest score seen. Null when the tiles were compared by symbol "
            "code, along with the three figures below it -- there is no "
            "distribution to separate."
        ),
    )
    score_max: float | None = Field(default=None, description="Highest score seen.")
    matched_min: float | None = Field(
        default=None,
        description=(
            "Lowest score that was counted as a match. Null when nothing matched."
        ),
    )
    rejected_max: float | None = Field(
        default=None,
        description=(
            "Highest score not counted as a match; with matched_min, the gap "
            "a working threshold sits in."
        ),
    )


class PaylineLayout(BaseModel):
    """What the panel needs before anything is checked; a game with no
    paylines or nothing split reports as a state (``error`` set) on a 200."""

    game: str = Field(description="Game the paylines belong to.")
    sets: list[PaylineSetOption] = Field(
        default_factory=list,
        description="Bet configurations the game declares, in numeric order.",
    )
    default_set: str | None = Field(
        default=None, description="The set a check with no 'set' would use."
    )
    threshold: float = Field(
        description="The similarity cut a check with no 'threshold' would use."
    )
    rows: int = Field(default=0, ge=0, description="Symbol positions per reel.")
    columns: int = Field(default=0, ge=0, description="Reels across.")
    latest_split: PaylineSource | None = Field(
        default=None, description="The split a check would read, or null."
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why no check can be run yet: no paylines, no reel grid, or nothing "
            "split. Null when one can."
        ),
    )


class PaylineCheckRequest(BaseModel):
    """Which split to check, against which set of lines, and how strictly."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {},
                {"set": "5"},
                {"threshold": 0.93},
                {"split": "screenshot-1787384252086", "set": "20"},
            ]
        },
    )

    split: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Directory name of a split under 'obs-captured-files/grid/'. Must be "
            "a bare name. Omit to use the newest one."
        ),
    )
    set: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        description=(
            "Bet configuration key, e.g. '5', '20'. Omit for "
            "PAYLINE_DEFAULT_SET, or the smallest declared."
        ),
    )
    threshold: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description=(
            "Cosine similarity two tiles must reach to count as the same symbol. "
            "Omit for PAYLINE_MATCH_THRESHOLD."
        ),
    )
    include_images: bool = Field(
        default=True,
        description=(
            "Return the annotated reels as a data URI. The file is written "
            "either way; set false when only the numbers are wanted."
        ),
    )


class PaylineCheckResult(BaseModel):
    """Every line's verdict, the evidence for it, and the picture of it."""

    game: str = Field(description="Game whose paylines were checked.")
    set: str = Field(description="Bet configuration that was checked.")
    method: PaylineMethod = Field(
        default=PaylineMethod.SIMILARITY,
        description="How two tiles were decided to be the same symbol.",
    )
    threshold: float | None = Field(
        default=None,
        description=(
            "Similarity cut that was applied. Null when the tiles were compared "
            "by symbol code, which has no threshold to tune."
        ),
    )
    wild_symbol: str | None = Field(
        default=None,
        description=(
            "The symbol code that was substituted along a line, or null when "
            "none was -- a similarity check, which cannot know a wild when it "
            "sees one, or a game whose config declares no "
            "'wild_card_replacement'."
        ),
    )
    wild_replaces: list[str] = Field(
        default_factory=list,
        description=(
            "The codes the wild was allowed to stand in for, sorted. A closed "
            "list, not 'anything': the scatters and feature symbols left out of "
            "it are paid by counting them across the grid, so a wild beside two "
            "of them is not three of them."
        ),
    )
    source: PaylineSource = Field(description="The split it was checked against.")
    summary: str = Field(
        description=("The result as one sentence, e.g. 'Line 1 pays 2, Line 3 pays 4'.")
    )
    lines: list[PaylineLine] = Field(
        description="Every line in the set, in numeric order, paying or not."
    )
    stats: PaylineStats = Field(description="The run as a whole.")
    output_dir: str = Field(
        description="Absolute directory the annotated picture was written to."
    )
    output_file: str = Field(
        description="What it was written as, e.g. '5.png' for the five-line set."
    )
    overlay_image: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the reels with every paying line drawn over "
            "them. Null when the request asked for files only."
        ),
    )


class PaylineOverlay(BaseModel):
    """One combined picture of some lines over the reels, and where it landed."""

    output_dir: str = Field(description="Absolute directory it was written to.")
    output_file: str = Field(description="What it was written as.")
    image_data: str = Field(
        description="Base64 data URI of the reels with those lines drawn over them."
    )
