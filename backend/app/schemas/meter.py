"""Meter reading payloads."""

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


class MeterEngine(StrEnum):
    """Which OCR engine reads the strip.

    Both stay available on purpose. Tesseract is what Analyze Spin's meter
    validations were measured against; PaddleOCR reads a cell in one call rather
    than an escalating ladder of subprocesses, and scores every reading it
    returns. They do not read a strip identically, so this is a choice per
    caller rather than a setting for the project.
    """

    TESSERACT = "tesseract"
    PADDLE = "paddle"


class MeterField(BaseModel):
    """One number read off the strip, with how sure the engine was; carried
    per field since one average would hide a confident balance beside a bad bet."""

    value: float | None = Field(
        default=None, description="The number, or null when none was read."
    )
    text: str = Field(
        default="", description="Raw text the engine returned, junk included."
    )
    confidence: float = Field(
        default=0.0,
        description=(
            "0-100; verified-correct strips scored 79 or better. Exactly 0 means "
            "unmeasured, not wrong: Tesseract declines to score a word it read "
            "under a character whitelist, and a correct balance often comes back "
            "that way. Treat it as 'no score', and read `value` on its own merits."
        ),
    )
    box: list[int] = Field(
        default_factory=list,
        description="Columns [left, right] of the strip this value occupied.",
    )


class MeterUnmapped(BaseModel):
    """A confident number that fell outside every field's window; never
    filed under the nearest field, since that could silently mean something else."""

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
            "Currency symbol on the values, e.g. '$'; '?' means an unrecognised "
            "symbol. Null in credits mode."
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
        description="Last win; null is normal -- the WIN cell is empty between spins.",
    )
    bet: float | None = Field(default=None, description="Current total bet.")

    fields: dict[str, MeterField] = Field(
        default_factory=dict,
        description="Per-field detail, keyed by field name, for diagnosing a bad read.",
    )
    unmapped: list[MeterUnmapped] = Field(
        default_factory=list,
        description=(
            "Confident numbers belonging to no field; non-empty means this "
            "skin's layout doesn't match the expected one."
        ),
    )
    band: list[int] = Field(
        default_factory=list,
        description="Rows [top, bottom] of the strip read; cached per game.",
    )
    engine_calls: int = Field(
        default=0,
        ge=0,
        description="Tesseract invocations this reading cost, at roughly 200ms each.",
    )
    duration_ms: int = Field(default=0, ge=0, description="How long the read took.")
    error: str | None = Field(
        default=None, description="Why the meter could not be read; null when it was."
    )
