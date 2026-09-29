"""PaddleOCR runtime settings. ``OCR_ENABLED`` gates every reader at once; the
rest tune the three built on it -- orb/prize figures, cash meters, and named
regions -- each in its own block since they read different kinds of crop and
were measured separately."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings

__all__ = ["OcrSettings"]


class OcrSettings(BaseSettings):
    """Runtime options for reading text off a captured frame."""

    # false makes every OCR request answer "the engine is disabled" without
    # importing PaddleOCR, for a host that has no engine and is not meant to
    # grow one.
    OCR_ENABLED: bool = True

    # Width of the live-read screenshot. Unset means native resolution: unlike
    # an event-capture screenshot, this frame exists only to be read.
    OCR_SCREENSHOT_WIDTH: int | None = Field(default=None, ge=8, le=4096)

    # --- orb numbers --------------------------------------------------------
    # The figure printed on a symbol orb.
    #
    # PaddleOCR's language pack. "en" is the Latin-digit recogniser; the
    # figures on an orb are digits, so this is not a choice of spoken language.
    OCR_ORB_PADDLE_LANGUAGE: str = Field(default="en", min_length=1)

    # No upscaling, and this is what decides how long the orb step takes.
    # Paddle's detector resizes internally, unlike Tesseract which needed a
    # fixed multiplier for ~30px glyphs. Measured over all 131 written scatter
    # tiles, 1x reads 126 identically to 4x at 1.56s a tile against 10.07s, and
    # the 5 that differ are jackpot banner text bleeding into the crop rather
    # than prizes. Raising this buys nothing and costs seconds per orb.
    OCR_ORB_PADDLE_UPSCALE: float = Field(default=1.0, gt=0, le=10)

    # Below this, a recognised string is noise off the artwork rather than a
    # prize. Paddle scores a real figure at ~0.999, so this rejects junk
    # without touching a genuine reading.
    OCR_ORB_PADDLE_MIN_CONFIDENCE: float = Field(default=0.5, ge=0.0, le=1.0)

    # Paddle runs in-process, so this bounds the worker thread rather than
    # killing a subprocess. Higher than OCR_TIMEOUT_SECONDS because the first
    # read of a process also builds the model.
    OCR_ORB_PADDLE_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)

    # --- cash meter ----------------------------------------------------------
    # The five-value strip app.utils.meter_paddle reads. Its own block since a
    # meter cell is drawn differently from an orb: gold digits on a light
    # face rather than over reel artwork.
    OCR_METER_PADDLE_LANGUAGE: str = Field(default="en", min_length=1)
    OCR_METER_PADDLE_UPSCALE: float = Field(default=1.0, gt=0, le=10)

    # --- named regions (GET/POST /api/ocr/*) ---------------------------------
    # An arbitrary region a game config declares in its "roi" block -- a
    # cash-meter crop or an orb are read by their own tuned readers above;
    # this is for anything else a config names (a jackpot amount, a bonus
    # counter). Not independently measured against a written split the way the
    # orb/meter defaults were -- these are starting points, tune them per game
    # in the config's own "ocr" block rather than trusting the numbers here.
    OCR_REGION_PADDLE_LANGUAGE: str = Field(default="en", min_length=1)
    OCR_REGION_PADDLE_UPSCALE: float = Field(default=1.0, gt=0, le=10)
    OCR_REGION_PADDLE_MIN_CONFIDENCE: float = Field(default=0.5, ge=0.0, le=1.0)
