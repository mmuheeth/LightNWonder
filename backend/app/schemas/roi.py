"""ROI extraction payloads.

Two things here differ from the OCR schemas next door, and both are the point of
this feature existing separately.

**A crop is returned, not a reading.** Extraction answers "is this region aimed
at the right part of the screen", which is a question about a rectangle and not
about text. So the response carries the picture and the pixel box it came from,
and nothing about an engine -- the region can be checked before Tesseract is
even installed.

**The frame defaults to the newest screenshot on disk.** The dashboard's
Screenshot button writes into the configured screenshots directory, so "extract
the cash meter" means "off the shot I just took" without anyone naming a file.
Naming one is still allowed, for going back to an older frame.

**The cash meter comes back read as well as cropped.** Extracting that one region
also carries the numbers on it -- see :attr:`RoiExtractResult.meter` -- because
cropping the meter and reading it are one action from the panel's point of view,
and cropping twice would allow two extractions of the same strip to disagree.
Every other region leaves it null, and a reading that failed populates its own
``error`` rather than failing the crop.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.meter import MeterValues


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
        description="[left, top, right, bottom] as fractions of the frame.",
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why this region is unusable, when it is. A malformed region is "
            "listed with its reason rather than hidden, so a typo in the config "
            "is visible in the dropdown instead of only on extraction."
        ),
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
    width: int = Field(ge=1, description="Crop width in pixels.")
    height: int = Field(ge=1, description="Crop height in pixels.")
    image_data: str = Field(
        description="Base64 data URI of the crop, ready for an <img> src."
    )
    meter: MeterValues | None = Field(
        default=None,
        description=(
            "The values read off the crop, for the cash meter region only; null "
            "for every other region. Carries its own 'error' when the numbers "
            "could not be read, because a failed reading still has a crop worth "
            "looking at."
        ),
    )
