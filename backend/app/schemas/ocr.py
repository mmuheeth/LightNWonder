"""OCR status, tuning and reading payloads."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OcrEngineState(StrEnum):
    """Whether text can be read right now."""

    DISABLED = "disabled"
    """``OCR_ENABLED`` is false; PaddleOCR is never imported."""

    NOT_INSTALLED = "not_installed"
    """PaddleOCR is not importable in this environment."""

    ERROR = "error"
    """PaddleOCR is installed but would not build its model."""

    READY = "ready"


class OcrSource(StrEnum):
    """Which frame a reading was taken from."""

    LIVE = "live"
    """A screenshot taken from OBS for this request."""

    RUN = "run"
    """A screenshot already on disk in an event-capture run."""


class OcrOptions(BaseModel):
    """A fully resolved set of engine options: environment defaults, with the
    config's and then the request's overrides applied."""

    language: str = Field(description="PaddleOCR language pack, e.g. 'en'.")
    upscale: float = Field(gt=0, le=10, description="Factor the crop was enlarged by.")
    min_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Below this, a recognised word is treated as noise: it still "
            "appears in 'words', but does not contribute to 'text'/'value'."
        ),
    )


class OcrOptionOverrides(BaseModel):
    """Options to change for one read, leaving the rest as configured; for
    tuning a region before writing the result into the game config."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"min_confidence": 0.8}]},
    )

    language: str | None = Field(default=None, min_length=1)
    upscale: float | None = Field(default=None, gt=0, le=10)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class OcrStatus(BaseModel):
    """What the dashboard card polls; always returned, engine or no engine."""

    state: OcrEngineState = Field(description="Whether text can be read right now.")
    version: str | None = Field(
        default=None, description="PaddleOCR's version string, if it built."
    )
    languages: list[str] = Field(
        default_factory=list,
        description="Language pack(s) configured to read with, e.g. ['en'].",
    )
    detail: str | None = Field(
        default=None,
        description="Why the engine is unusable, when it is; null when ready.",
    )
    options: OcrOptions = Field(
        description="Defaults every read starts from, before per-region overrides."
    )


class OcrRegion(BaseModel):
    """One readable region of the active game, and how it will be read."""

    region: str = Field(description="Region name, as the game config declares it.")
    roi: list[float] = Field(
        min_length=4,
        max_length=4,
        description=(
            "[left, top, right, bottom] as fractions of the part of the frame "
            "the game fills, not of the whole canvas."
        ),
    )
    options: OcrOptions = Field(
        description="Effective options: the environment defaults with this "
        "region's overrides applied."
    )
    overrides: dict[str, object] = Field(
        default_factory=dict,
        description="What the game config's 'ocr' block changes for this region.",
    )


class OcrRegionCatalog(BaseModel):
    """Every region of the active game that can be read."""

    game: str = Field(description="Game the regions belong to.")
    regions: list[OcrRegion] = Field(
        description="Readable regions, sorted by name; empty if none are declared."
    )


class OcrWordBox(BaseModel):
    """One recognised word (Paddle detects a *line*, so this is one line);
    coordinates are pixels inside the crop, not the frame, so an overlay drawn
    on the region image lines up unscaled."""

    text: str = Field(description="The word as recognised.")
    confidence: float = Field(description="0-100, Paddle's 0-1 rescaled.")
    left: int = Field(description="Left edge, in crop pixels.")
    top: int = Field(description="Top edge, in crop pixels.")
    width: int = Field(description="Width, in crop pixels.")
    height: int = Field(description="Height, in crop pixels.")


class OcrReading(BaseModel):
    """The text read out of one region."""

    region: str = Field(description="Region that was read.")
    text: str = Field(
        default="",
        description="Text from words that cleared min_confidence; empty if none did.",
    )
    value: float | None = Field(
        default=None,
        description="First number in the text, if it has one -- what a meter is for.",
    )
    values: list[float] = Field(
        default_factory=list,
        description=(
            "Every number in the text, in reading order. A meter panel often "
            "holds credit, bet and win in one region."
        ),
    )
    confidence: float | None = Field(
        default=None,
        description="Mean confidence of the words behind 'text', 0-100; null when none cleared the floor.",
    )
    words: list[OcrWordBox] = Field(
        default_factory=list,
        description="Every word the detector found, in reading order -- rejected ones too.",
    )
    crop: list[int] = Field(
        default_factory=list,
        description="Pixel box [left, top, right, bottom] the region resolved to.",
    )
    crop_image: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the crop, when the request asked for it. This "
            "is what the engine saw, which is the thing to look at when a "
            "reading is wrong."
        ),
    )
    options: OcrOptions | None = Field(
        default=None, description="Options this region was read with."
    )
    duration_ms: int = Field(default=0, ge=0, description="How long the read took.")
    error: str | None = Field(
        default=None,
        description="Why this region could not be read; null when it was.",
    )


class OcrReadRequest(BaseModel):
    """Which regions to read, off which frame -- live from OBS with no
    ``run_id``, or a capture run's screenshot with one."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"regions": ["cash_meter"]},
                {
                    "run_id": "2026-08-19_04-01-02",
                    "file_name": "041_spin-result-received_04-01-24.png",
                    "regions": ["cash_meter"],
                    "options": {"min_confidence": 0.8},
                    "include_crop": True,
                },
            ]
        },
    )

    regions: list[str] | None = Field(
        default=None,
        description=(
            "Region names from the active game's config. Omit to read every "
            "region it declares."
        ),
    )
    run_id: str | None = Field(
        default=None, description="Capture run holding the frame to read."
    )
    file_name: str | None = Field(
        default=None,
        description="Screenshot inside that run. Required when run_id is given.",
    )
    options: OcrOptionOverrides | None = Field(
        default=None,
        description="Options to override for this read only, for tuning a region.",
    )
    include_crop: bool = Field(
        default=False,
        description="Return the crop as a data URI beside each reading.",
    )


class OcrReadResult(BaseModel):
    """The readings taken off one frame."""

    source: OcrSource = Field(description="Where the frame came from.")
    game: str = Field(description="Game whose regions were used.")
    run_id: str | None = Field(default=None, description="Run the frame came from.")
    file_name: str | None = Field(default=None, description="Frame that was read.")
    frame_width: int = Field(ge=1, description="Width of the frame in pixels.")
    frame_height: int = Field(ge=1, description="Height of the frame in pixels.")
    content_box: list[int] = Field(
        default_factory=list,
        description=(
            "Pixel box [left, top, right, bottom] of the part of the frame the "
            "game filled; the whole frame when there was no letterboxing."
        ),
    )
    letterboxed: bool = Field(
        default=False,
        description="Whether any of the frame was letterbox rather than game.",
    )
    readings: list[OcrReading] = Field(
        description="One entry per requested region, in the order asked for."
    )
