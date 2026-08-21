"""OCR status, tuning and reading payloads.

Two things here are worth knowing before using them.

**Every reading carries its confidence and the options that produced it.** A
region read off a game frame is not a scanned document: the same crop can read
perfectly at one page-segmentation mode and come back as punctuation at another.
So a reading is never just text -- it says how sure the engine was and exactly
what it was asked, which is what makes a bad reading diagnosable instead of
mysterious.

**A read never fails as a whole because one region failed.** Ask for four regions
and a broken one comes back with its ``error`` populated beside the three that
worked, because the alternative is a 502 that says nothing about the three.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OcrEngineState(StrEnum):
    """Whether text can be read right now."""

    DISABLED = "disabled"
    """``OCR_ENABLED`` is false; no engine is looked for."""

    NOT_INSTALLED = "not_installed"
    """No Tesseract executable was found, configured or discovered."""

    ERROR = "error"
    """An executable is there but would not report its version."""

    READY = "ready"


class OcrSource(StrEnum):
    """Which frame a reading was taken from."""

    LIVE = "live"
    """A screenshot taken from OBS for this request."""

    RUN = "run"
    """A screenshot already on disk in an event-capture run."""


class OcrOptions(BaseModel):
    """A fully resolved set of engine and preprocessing options.

    This is what a read actually used: environment defaults, with the game
    config's per-region overrides and then the request's own applied over them.
    """

    language: str = Field(description="Traineddata name(s), e.g. 'eng'.")
    psm: int = Field(ge=0, le=13, description="Page segmentation mode.")
    oem: int = Field(ge=0, le=3, description="OCR engine mode.")
    char_whitelist: str = Field(
        description="Characters the engine was restricted to; empty means all."
    )
    upscale: float = Field(gt=0, le=10, description="Factor the crop was enlarged by.")
    grayscale: bool = Field(description="Whether colour was dropped first.")
    autocontrast: bool = Field(description="Whether the crop's range was stretched.")
    invert: bool = Field(description="Whether light-on-dark was flipped.")
    threshold: int | None = Field(
        default=None, ge=0, le=255, description="Binarization level; null for none."
    )
    dpi: int | None = Field(
        default=None, ge=70, le=2400, description="Resolution reported to the engine."
    )


class OcrOptionOverrides(BaseModel):
    """Options to change for one read, leaving the rest as configured.

    For tuning a region from the dashboard: sweep ``psm`` or turn ``invert`` on
    against a frame that is already on disk, then write whatever worked into the
    game config's ``ocr`` block so it applies from then on.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"psm": 11, "char_whitelist": "0123456789.,$"}]
        },
    )

    language: str | None = Field(default=None, min_length=1)
    psm: int | None = Field(default=None, ge=0, le=13)
    oem: int | None = Field(default=None, ge=0, le=3)
    char_whitelist: str | None = None
    upscale: float | None = Field(default=None, gt=0, le=10)
    grayscale: bool | None = None
    autocontrast: bool | None = None
    invert: bool | None = None
    threshold: int | None = Field(default=None, ge=0, le=255)
    dpi: int | None = Field(default=None, ge=70, le=2400)


class OcrStatus(BaseModel):
    """What the dashboard card polls.

    Always returned, engine or no engine, so the card never has to branch on an
    error to find out whether OCR is available.
    """

    state: OcrEngineState = Field(description="Whether text can be read right now.")
    executable: str | None = Field(
        default=None, description="Engine that was found; null when none was."
    )
    version: str | None = Field(
        default=None, description="Version line the engine reported, if it ran."
    )
    languages: list[str] = Field(
        default_factory=list,
        description="Traineddata the engine can see, e.g. ['eng', 'osd'].",
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
    """One recognised word, with where it sat and how sure the engine was.

    Coordinates are pixels inside the *crop*, not the frame, so an overlay drawn
    on the region image lines up without rescaling.
    """

    text: str = Field(description="The word as recognised.")
    confidence: float = Field(description="0-100, as the engine reports it.")
    left: int = Field(description="Left edge, in crop pixels.")
    top: int = Field(description="Top edge, in crop pixels.")
    width: int = Field(description="Width, in crop pixels.")
    height: int = Field(description="Height, in crop pixels.")


class OcrReading(BaseModel):
    """The text read out of one region."""

    region: str = Field(description="Region that was read.")
    text: str = Field(default="", description="Recognised text; empty if none was.")
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
        description="Mean word confidence, 0-100; null when nothing was read.",
    )
    words: list[OcrWordBox] = Field(
        default_factory=list, description="Per-word detail, in reading order."
    )
    crop: list[int] = Field(
        default_factory=list,
        description="Pixel box [left, top, right, bottom] the region resolved to.",
    )
    crop_image: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the preprocessed crop, when the request asked "
            "for it. This is what the engine saw, which is the thing to look at "
            "when a reading is wrong."
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
    """Which regions to read, off which frame.

    With no ``run_id`` the frame is taken from OBS now. With one, the named
    screenshot from that capture run is read instead -- which is how a reading is
    tuned or re-checked without the game having to still be on screen.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"regions": ["cash_meter"]},
                {
                    "run_id": "2026-08-19_04-01-02",
                    "file_name": "041_spin-result-received_04-01-24.png",
                    "regions": ["cash_meter"],
                    "options": {"psm": 11},
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
        description="Return the preprocessed crop as a data URI beside each reading.",
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
            "game filled, which every region's fractions were resolved against. "
            "The whole frame when the capture had no letterboxing to trim."
        ),
    )
    letterboxed: bool = Field(
        default=False,
        description=(
            "Whether any of the frame was letterbox rather than game. False "
            "means the regions resolved exactly as fractions of the canvas."
        ),
    )
    readings: list[OcrReading] = Field(
        description="One entry per requested region, in the order asked for."
    )
