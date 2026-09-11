"""ROI extraction payloads."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.meter import MeterEngine, MeterValues


class RoiFrame(BaseModel):
    """One screenshot on disk that a region can be taken out of."""

    file_name: str = Field(description="File name inside the screenshots directory.")
    captured_at: datetime = Field(
        description="Modification time of the file, as a UTC timestamp."
    )
    width: int = Field(ge=1, description="Frame width in pixels.")
    height: int = Field(ge=1, description="Frame height in pixels.")


class RoiRegionSummary(BaseModel):
    """One region the active game declares, as the dropdown lists it."""

    region: str = Field(description="Region name, as the game config declares it.")
    label: str = Field(description="Qualified name for display, e.g. 'roi.cash_meter'.")
    roi: list[float] = Field(
        min_length=4,
        max_length=4,
        description=(
            "[left, top, right, bottom] as fractions of the part of the frame "
            "the game fills -- not of the whole canvas, which is letterboxed "
            "when the game window is not the canvas's shape."
        ),
    )
    error: str | None = Field(
        default=None,
        description="Why this region is unusable, when it is; null when it is not.",
    )


class RoiCatalog(BaseModel):
    """What the panel needs to render before anything is extracted."""

    game: str = Field(description="Game the regions belong to.")
    regions: list[RoiRegionSummary] = Field(
        description="Declared regions, sorted by name; empty if none are declared."
    )
    latest_frame: RoiFrame | None = Field(
        default=None,
        description=(
            "Newest screenshot in the screenshots directory, which is what an "
            "extraction with no 'file_name' will use. Null when none has been "
            "taken yet."
        ),
    )


class RoiExtractRequest(BaseModel):
    """Which region to cut out, and off which frame."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"region": "cash_meter"},
                {"region": "cash_meter", "file_name": "screenshot-1787192135120.png"},
            ]
        },
    )

    region: str = Field(
        min_length=1,
        max_length=200,
        description="Region name from the active game's 'roi' block.",
    )
    file_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Screenshot in the screenshots directory to crop. Must be a bare "
            "filename. Omit to use the newest one."
        ),
    )
    engine: MeterEngine = Field(
        default=MeterEngine.TESSERACT,
        description=(
            "Which OCR engine reads the cash meter. Only consulted for the "
            "'cash_meter' region, since it is the only one this endpoint reads "
            "numbers off. The two do not read a strip identically, so it is a "
            "choice per caller: Analyze Spin's validations were measured against "
            "Tesseract, and Evaluate Screen asks for PaddleOCR."
        ),
    )


class RoiExtractResult(BaseModel):
    """The crop, and enough of its provenance to judge whether it is right."""

    game: str = Field(description="Game whose region was used.")
    region: str = Field(description="Region that was extracted.")
    roi: list[float] = Field(
        min_length=4,
        max_length=4,
        description="Fractions the region is declared as.",
    )
    source: RoiFrame = Field(description="Frame the crop was taken from.")
    box: list[int] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Pixel box [left, top, right, bottom] the fractions resolved to on "
            "this frame -- what to check when a crop looks off by a few pixels."
        ),
    )
    content_box: list[int] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Pixel box [left, top, right, bottom] of the part of the frame the "
            "game filled; the whole frame when there was no letterboxing."
        ),
    )
    letterboxed: bool = Field(
        description="Whether any of the frame was letterbox rather than game.",
    )
    width: int = Field(ge=1, description="Crop width in pixels.")
    height: int = Field(ge=1, description="Crop height in pixels.")
    image_data: str = Field(
        description="Base64 data URI of the crop, ready for an <img> src."
    )
    meter: MeterValues | None = Field(
        default=None,
        description=(
            "Values read off the crop, for the cash meter region only; null "
            "for every other region."
        ),
    )
