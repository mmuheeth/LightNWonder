"""Request and response models for the payline check.

Three things travel back and none of them is optional to understanding the
others: what each line paid, the similarity score behind every comparison that
produced it, and one picture of the lines drawn over the reels they were read
from. A pay count on its own cannot be checked -- "line 3 pays 4" is the same
sentence whether four pots really line up or the threshold is too low -- so the
result carries the evidence beside the verdict, and the panel shows both.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PaylineCheckRequest",
    "PaylineCheckResult",
    "PaylineLayout",
    "PaylineLine",
    "PaylineSetOption",
    "PaylineSource",
    "PaylineStats",
    "PaylineStep",
]


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
    """One adjacent pair on a line, and how alike the two tiles are.

    Both ``matched`` and ``counted`` are here because they answer different
    questions. ``matched`` is whether these two tiles are the same symbol.
    ``counted`` is whether the left-to-right read got this far: once a pair does
    not match the line stops paying, and every step after it is reported for
    information rather than as part of the verdict.
    """

    left: str = Field(description="Position name of the left tile, e.g. 'r2c1'.")
    right: str = Field(description="Position name of the right tile, e.g. 'r2c2'.")
    similarity: float = Field(
        description=(
            "Cosine similarity of the two tiles, in [-1, 1]. Non-negative pixel "
            "channels put unrelated symbols around 0.6-0.9 and two crops of one "
            "symbol above 0.96, so read it against the run's own distribution "
            "rather than as a fraction."
        )
    )
    matched: bool = Field(description="Whether the score reached the threshold.")
    counted: bool = Field(
        description=(
            "Whether the left-to-right read reached this step. False for every "
            "step after the first that did not match."
        )
    )


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
            "How many positions from the left are the same symbol: 0 when the "
            "first two reels differ, otherwise 2 or more."
        ),
    )
    paying: bool = Field(description="Whether the line paid at all -- pays >= 2.")
    matched_positions: list[str] = Field(
        description="The leading run of positions that pays, empty when none does."
    )
    color: str = Field(
        description=(
            "Hex colour this line is drawn in on the overlay. Reported so a "
            "swatch beside the line matches the picture."
        )
    )
    steps: list[PaylineStep] = Field(
        description=(
            "Every adjacent pair on the line, left to right -- including the "
            "ones after the run broke, so the whole line's scores can be read."
        )
    )
    break_position: str | None = Field(
        default=None,
        description=(
            "Position name where the leading run stopped -- the first tile that "
            "did not continue the match. Null when the whole line paid, since "
            "there is nothing to mark as a break."
        ),
    )
    image_data: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of this one line drawn over the reels, with a "
            "marker at 'break_position' when the run stopped early. Null when "
            "the request asked for files only."
        ),
    )


class PaylineStats(BaseModel):
    """The run as a whole: what paid, and how well the scores separated."""

    lines: int = Field(ge=0, description="Lines in the set that was checked.")
    paying: int = Field(ge=0, description="How many of them paid.")
    comparisons: int = Field(ge=0, description="Adjacent pairs compared.")
    matches: int = Field(ge=0, description="Pairs that reached the threshold.")
    best_line: str | None = Field(
        default=None, description="Name of the longest-paying line, or null."
    )
    best_pays: int = Field(ge=0, description="Positions the best line paid on.")
    score_min: float | None = Field(default=None, description="Lowest score seen.")
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
            "Highest score that was not counted as a match. Together with "
            "matched_min this is the gap the threshold sits in -- a threshold "
            "between the two is separating the symbols, one outside is not."
        ),
    )


class PaylineLayout(BaseModel):
    """What the panel needs before anything is checked.

    A game that declares no paylines, and a game that has never been split, are
    both reported as states on a 200 rather than as failures: only some games
    have the block and a fresh checkout has split nothing, and neither is
    something to recover from by retrying.
    """

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
            "Bet configuration to check, as the game config keys it -- '5', '20' "
            "or '40'. Omit for PAYLINE_DEFAULT_SET, or the smallest declared."
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
    threshold: float = Field(description="Similarity cut that was applied.")
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
