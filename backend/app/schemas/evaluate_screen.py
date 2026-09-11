"""Payloads for Evaluate Screen: one screenshot, read.

Where Analyze Spin *drives* a spin and grades it against the maths the game
loaded, this reads a screen that is already on it -- no key is pressed, no log is
followed, nothing is validated. So the reading types are deliberately **reused**
rather than reinvented: what the classifier makes of a grid of tiles is the same
answer here as there, and the frontend renders both with one card. What is new is
only the envelope around them, because this feature has one result rather than a
run of steps.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.analyze_spin import SpinReelReading
from app.schemas.meter import MeterEngine, MeterMode, MeterValues

__all__ = [
    "EvaluateScreenMeter",
    "EvaluateScreenRequest",
    "EvaluateScreenResult",
    "EvaluateScreenSource",
]


class EvaluateScreenRequest(BaseModel):
    """Which screen to read, and how."""

    file_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Screenshot in the screenshots directory to read. Must be a bare "
            "filename. Omit to capture the game's current screen with OBS, "
            "which is the ordinary case -- 'evaluate the screen' means the one "
            "on the cabinet now."
        ),
    )
    architecture: str | None = Field(
        default=None,
        description=(
            "Which trained network names the tiles: 'resnet34' or "
            "'efficientnet_b0'. Omit for the configured default. Both stay "
            "trained at once and they do not read the same split equally well, "
            "so it is a per-request choice."
        ),
    )
    include_images: bool = Field(
        default=True,
        description=(
            "Return the meter crop and the ringed grid as data URIs. On by "
            "default, unlike Analyze Spin's stream: this endpoint answers once "
            "and the pictures *are* the report."
        ),
    )


class EvaluateScreenSource(BaseModel):
    """The screen that was read, and where it came from."""

    file_name: str = Field(description="Screenshot the reading was taken off.")
    captured: bool = Field(
        description=(
            "Whether this request captured the frame, or read one already on disk."
        )
    )
    at: datetime = Field(description="When the frame was read.")
    width: int = Field(ge=1, description="Frame width in pixels.")
    height: int = Field(ge=1, description="Frame height in pixels.")
    blank: bool = Field(
        default=False,
        description=(
            "Whether the frame came back empty. OBS reports a successful write "
            "of a frame it rendered nothing into, so this is read back off the "
            "picture rather than trusted -- and every reading below it is "
            "worthless when true."
        ),
    )
    content_box: list[int] = Field(
        default_factory=list,
        description=(
            "Pixel box [left, top, right, bottom] the game itself fills. Every "
            "region is a fraction of this, not of the canvas -- so a crop of the "
            "wrong thing is either a badly measured region or a misdetected box, "
            "and only the pair says which."
        ),
    )
    letterboxed: bool = Field(
        default=False,
        description="Whether the frame arrived with bars around the game.",
    )


class EvaluateScreenMeter(BaseModel):
    """The cash meter strip, as PaddleOCR read it.

    Not ``SpinMeterValidation``: there is nothing to validate from a single
    screen. Two readings of a meter are what make an arithmetic check possible,
    and this feature has one -- so this reports what the strip *says* and stops
    there.
    """

    engine: MeterEngine = Field(
        default=MeterEngine.PADDLE,
        description="Which engine read the strip.",
    )
    mode: MeterMode = Field(
        description=(
            "Whether the strip was drawing money or credits. Decided from the "
            "values themselves -- a currency symbol or a fractional amount "
            "means money -- because the CASH/CREDITS label does not OCR "
            "reliably."
        )
    )
    currency: str | None = Field(
        default=None,
        description=(
            "Currency symbol on the values, e.g. '$'; '?' means a symbol is "
            "drawn that the engine would not name. Null in credits mode."
        ),
    )
    balance: float | None = Field(
        default=None,
        description=(
            "The balance cell, in whichever unit `mode` says was drawn -- the "
            "CASH cell in cash mode, the CREDITS cell in credits mode."
        ),
    )
    win: float | None = Field(
        default=None,
        description=(
            "The WIN cell. Null is ordinary rather than a failure: the cell is "
            "empty between spins."
        ),
    )
    bet: float | None = Field(default=None, description="The BET cell.")
    values: MeterValues | None = Field(
        default=None,
        description=(
            "The whole reading, per field, with the confidence and the raw text "
            "behind each number -- what to look at when a figure looks wrong."
        ),
    )
    crop_image: str | None = Field(
        default=None,
        description=(
            "The meter strip as a PNG data URI, when images were asked for. The "
            "picture beside the numbers is what makes a bad reading obvious."
        ),
    )
    error: str | None = Field(
        default=None,
        description="Why there is no reading, when there is none.",
    )


class EvaluateScreenResult(BaseModel):
    """Everything one screen was read to say."""

    game: str = Field(description="Filename stem of the game config that was used.")
    label: str = Field(description="Display name that config declares.")
    source: EvaluateScreenSource = Field(description="The screen that was read.")
    duration_ms: int = Field(ge=0, description="How long the whole reading took.")
    reels: SpinReelReading | None = Field(
        default=None,
        description=(
            "What landed, tile by tile, with the scatters and the figure "
            "PaddleOCR read off each. The same type Analyze Spin reports, so "
            "one card renders both."
        ),
    )
    reels_error: str | None = Field(
        default=None,
        description="Why the grid could not be read, when it could not be.",
    )
    meter: EvaluateScreenMeter | None = Field(
        default=None, description="What the cash meter strip says."
    )
    grid_image: str | None = Field(
        default=None,
        description=(
            "The reels crop with a ring over every cell that was named, as a "
            "PNG data URI, when images were asked for. No codes drawn on it: "
            "they are a table beside it, and drawing them over the artwork "
            "duplicates them at their least readable size."
        ),
    )
    errors: list[str] = Field(
        default_factory=list,
        description=(
            "Everything that went wrong, in order. Non-empty with a reading "
            "present is the ordinary partial case -- a grid that read and a "
            "meter that did not is still worth returning."
        ),
    )
