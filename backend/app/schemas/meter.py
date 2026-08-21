"""Meter reading payloads.

These ride along with an ROI extraction rather than having endpoints of their own:
cropping ``roi.cash_meter`` and reading the numbers off it are one action from the
dashboard's point of view, so :class:`app.schemas.roi.RoiExtractResult` carries a
:class:`MeterValues` and there is no ``/api/meter``.

Two things here are deliberate.

**Reading the meter must never fail the crop.** The panel's job is to show the
region; the numbers are what the region is *for*, but a missing Tesseract or a
strip the engine could not manage is not a reason to withhold the picture. So
failure is a populated ``error`` on this object, never an exception reaching the
ROI service.

**``cash`` and ``credits`` are separate fields with one of them null.** They are
the same cell on screen -- a slot meter shows a cash balance or a credit count,
never both -- but they are different quantities and collapsing them into one
number would lose which was on screen. ``mode`` says which, and says how it was
decided, because it is inferred from the value's shape rather than read: the
``CASH``/``CREDITS`` label is 8px tall over artwork and does not OCR at all.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class MeterMode(StrEnum):
    """Whether the meter is counting money or credits."""

    CASH = "cash"
    """A currency symbol or a fractional amount was read."""

    CREDITS = "credits"
    """A bare whole number, which is how a credit count is drawn."""

    UNKNOWN = "unknown"
    """Nothing was read, so there is nothing to judge."""


class MeterField(BaseModel):
    """One number read off the strip, with how sure the engine was.

    Carried per field rather than as one number for the whole reading, because a
    strip routinely has a confident balance beside an unreadable bet, and one
    average would hide both.
    """

    value: float | None = Field(
        default=None, description="The number, or null when none was read."
    )
    text: str = Field(
        default="", description="Raw text the engine returned, junk included."
    )
    confidence: float = Field(
        default=0.0,
        description=(
            "0-100. Values verified correct on this project's strips scored 79 "
            "or better, so a low score here is the signal to look at the crop."
        ),
    )
    box: list[int] = Field(
        default_factory=list,
        description="Columns [left, right] of the strip this value occupied.",
    )


class MeterUnmapped(BaseModel):
    """A confident number that fell outside every field's window.

    Never filed under the nearest field. A skin that orders its cells differently
    reads perfectly and means something else, and a bet quietly reported as a
    balance is worse than one that arrives here asking to be looked at.
    """

    value: float = Field(description="The number that was read.")
    centre: float = Field(
        description="Where it sat, as a fraction of the strip's width."
    )
    confidence: float = Field(description="0-100, as the engine reports it.")
    text: str = Field(description="Raw text the engine returned.")


class MeterValues(BaseModel):
    """The five values a meter strip carries, and the evidence behind them."""

    mode: MeterMode = Field(description="Whether the meter is in cash or credits.")
    currency: str | None = Field(
        default=None,
        description=(
            "Currency symbol on the values, e.g. '$'. '?' means the pixels show "
            "a symbol the engine would not name -- the yen glyph these games "
            "draw reads as nothing at every mode and scale. Null in credits mode."
        ),
    )
    cash: float | None = Field(
        default=None, description="Balance in money; null when in credits mode."
    )
    credits: float | None = Field(
        default=None, description="Balance in credits; null when in cash mode."
    )
    win: float | None = Field(
        default=None,
        description=(
            "Last win. Null is normal rather than a failure -- the WIN cell is "
            "empty between spins."
        ),
    )
    bet: float | None = Field(default=None, description="Current total bet.")

    fields: dict[str, MeterField] = Field(
        default_factory=dict,
        description="Per-field detail, keyed by field name, for diagnosing a bad read.",
    )
    unmapped: list[MeterUnmapped] = Field(
        default_factory=list,
        description=(
            "Confident numbers belonging to no field. Non-empty means this skin's "
            "layout does not match the expected one -- look before trusting the "
            "values above."
        ),
    )
    band: list[int] = Field(
        default_factory=list,
        description=(
            "Rows [top, bottom] of the strip the values were read from, chosen by "
            "whichever candidate read best. Cached per game after the first read."
        ),
    )
    engine_calls: int = Field(
        default=0,
        ge=0,
        description="Tesseract invocations this reading cost, at roughly 200ms each.",
    )
    duration_ms: int = Field(default=0, ge=0, description="How long the read took.")
    error: str | None = Field(
        default=None,
        description=(
            "Why the meter could not be read; null when it was. Populated rather "
            "than raised, so a failed reading never withholds the crop."
        ),
    )
