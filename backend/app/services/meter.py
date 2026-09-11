"""Reads the five values off a cash-meter crop that :mod:`app.services.roi` already
cropped."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from PIL import Image

from app.config.ocr import candidate_executables
from app.config.runtime import settings
from app.core.logging import get_logger
from app.schemas.meter import (
    MeterField,
    MeterMode,
    MeterUnmapped,
    MeterValues,
)
from app.utils import meter, meter_paddle, ocr, paddle_ocr

logger = get_logger("meter")

# Reported as the currency when money was read but its symbol was not. The yen
# glyph these games draw reads as nothing at every mode and scale, so "there is a
# symbol here and it is not legible" is a different fact from "there is none".
UNNAMED_SYMBOL = "?"

# Fitted row bands, keyed by (game, strip width, strip height). Cleared by reset().
_bands: dict[tuple[str, int, int], meter.Band] = {}

# The same, for the PaddleOCR reader. Separate on purpose: which rows read best is
# a judgement the *engine* makes, so a band fitted by Tesseract is not a fact
# Paddle may reuse.
_paddle_bands: dict[tuple[str, int, int], meter.Band] = {}


def reset() -> None:
    """Drop the fitted bands. Tests, and after a game config change."""
    _bands.clear()
    _paddle_bands.clear()


def _paddle_options() -> paddle_ocr.PaddleOptions:
    """How a meter cell is read by Paddle, from the environment.

    Shares the orb reader's settings block: both are "read the digits drawn on a
    small crop of game artwork", and a host that tuned upscale or the confidence
    floor for one meant it for the other. ``min_digits`` is the one departure --
    a meter cell is legitimately one digit (a bet of ``5``, a win of ``0``) where
    a prize on an orb never is.
    """
    return paddle_ocr.PaddleOptions(
        language=settings.OCR_ORB_PADDLE_LANGUAGE,
        upscale=settings.OCR_ORB_PADDLE_UPSCALE,
        min_confidence=settings.OCR_ORB_PADDLE_MIN_CONFIDENCE,
        min_digits=1,
        timeout_seconds=settings.OCR_ORB_PADDLE_TIMEOUT_SECONDS,
    )


def _executable() -> Path:
    """The engine to read with; raises ``meter.MeterError`` if OCR is off or Tesseract wasn't found."""
    if not settings.OCR_ENABLED:
        raise meter.MeterError("OCR is disabled; set OCR_ENABLED=true to turn it on")
    executable = settings.ocr_tesseract_cmd
    if executable is None or not executable.is_file():
        looked = ", ".join(str(path) for path in candidate_executables())
        raise meter.MeterError(
            "No Tesseract executable was found. Install Tesseract OCR or set "
            f"OCR_TESSERACT_CMD to where it is. Looked in: {looked or 'PATH'}"
        )
    return executable


def _classify(fields: dict[str, meter.MeterField]) -> tuple[MeterMode, str | None]:
    """Decide cash-vs-credits and currency from the values read — the CASH/CREDITS
    label itself doesn't OCR reliably, so a symbol or fractional amount means money."""
    read = [f for f in fields.values() if f.value is not None]
    if not read:
        return MeterMode.UNKNOWN, None

    symbols = {f.symbol for f in read if f.symbol}
    amounts = [f.value for f in read if f.value is not None]
    fractional = any(value != value.to_integral_value() for value in amounts)
    if not symbols and not fractional:
        return MeterMode.CREDITS, None
    if symbols:
        return MeterMode.CASH, sorted(symbols)[0]
    # Money without a symbol the engine would name -- see :data:`UNNAMED_SYMBOL`.
    return MeterMode.CASH, UNNAMED_SYMBOL


def combine(readings: Iterable[MeterValues | None]) -> tuple[MeterMode, str | None]:
    """One mode and currency for several readings of the *same* meter -- the frames of
    one spin, say."""
    read = [values for values in readings if values is not None]
    modes = [values.mode for values in read]
    named = sorted(
        {
            values.currency
            for values in read
            if values.currency and values.currency != UNNAMED_SYMBOL
        }
    )
    if MeterMode.CASH in modes:
        return MeterMode.CASH, named[0] if named else UNNAMED_SYMBOL
    if MeterMode.CREDITS in modes:
        return MeterMode.CREDITS, None
    return MeterMode.UNKNOWN, None


def _as_field(reading: meter.MeterField) -> MeterField:
    """One internal reading as the API reports it."""
    return MeterField(
        value=float(reading.value) if reading.value is not None else None,
        text=reading.text,
        confidence=round(reading.confidence, 1),
        box=list(reading.box) if reading.box is not None else [],
    )


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _declared_band(profile: Mapping[str, Any], height: int) -> meter.Band | None:
    """The band the game config declares, in pixels for a strip this tall (stored as
    fractions, like ``roi`` and ``button_targets``, so it holds at any resolution)."""
    band = profile.get("band")
    if band is None:
        return None
    top, bottom = band
    low = max(0, min(height - 1, round(top * height)))
    high = max(low + 1, min(height, round(bottom * height)))
    return low, high


def _declared_windows(
    profile: Mapping[str, Any],
) -> dict[str, tuple[float, float]] | None:
    """The field windows the game config declares, if it declares any."""
    windows = profile.get("windows")
    return dict(windows) if windows else None


def read(
    strip: Image.Image,
    *,
    game: str,
    profile: Mapping[str, Any] | None = None,
) -> MeterValues:
    """Read the five values off a cash-meter crop. Never raises — a declared band
    in ``profile`` wins; otherwise one is fitted and cached per (game, size)."""
    started = time.perf_counter()
    block: Mapping[str, Any] = profile or {}
    windows = _declared_windows(block)
    try:
        executable = _executable()
        key = (game, strip.width, strip.height)
        band = _declared_band(block, strip.height) or _bands.get(key)
        if band is None:
            band, scan = meter.fit_band(strip, executable=executable, windows=windows)
            _bands[key] = band
            logger.info(
                "Fitted meter band %s for %s at %dx%d",
                band,
                game,
                strip.width,
                strip.height,
            )
        else:
            scan = meter.extract(
                strip, executable=executable, band=band, windows=windows
            )
    except (meter.MeterError, ocr.OcrError) as exc:
        return MeterValues(
            mode=MeterMode.UNKNOWN,
            error=str(exc),
            duration_ms=_elapsed_ms(started),
        )

    return _assemble(scan, game=game, started=started, engine="tesseract")


def read_paddle(
    strip: Image.Image,
    *,
    game: str,
    profile: Mapping[str, Any] | None = None,
) -> MeterValues:
    """Read the five values off a cash-meter crop with PaddleOCR instead of
    Tesseract. Same contract as :func:`read` -- never raises, a declared band in
    ``profile`` wins, and the reading comes back in the same ``MeterValues``.

    Used by Evaluate Screen. Analyze Spin stays on :func:`read`: the two engines
    do not read a strip identically and that feature's validations were measured
    against Tesseract, so this is a choice per feature rather than a switch for
    the project.

    The fitted band is cached **separately** from :func:`read`'s, under its own
    key: which rows read best is a judgement the engine makes, so a band fitted
    by one reader is not a fact the other one may reuse.
    """
    started = time.perf_counter()
    block: Mapping[str, Any] = profile or {}
    windows = _declared_windows(block)
    try:
        if not paddle_ocr.available():
            raise meter.MeterError(
                "PaddleOCR is not installed, so the cash meter cannot be read "
                "with it. Install paddleocr (it needs Python 3.13 or lower)"
            )
        options = _paddle_options()
        key = (game, strip.width, strip.height)
        band = _declared_band(block, strip.height) or _paddle_bands.get(key)
        if band is None:
            band, scan = meter_paddle.fit_band(strip, options=options, windows=windows)
            _paddle_bands[key] = band
            logger.info(
                "Fitted meter band %s for %s at %dx%d with PaddleOCR",
                band,
                game,
                strip.width,
                strip.height,
            )
        else:
            scan = meter_paddle.extract(
                strip, band=band, options=options, windows=windows
            )
    except (meter.MeterError, ocr.OcrError) as exc:
        return MeterValues(
            mode=MeterMode.UNKNOWN,
            error=str(exc),
            duration_ms=_elapsed_ms(started),
        )

    return _assemble(scan, game=game, started=started, engine="PaddleOCR")


def _assemble(
    scan: meter.MeterScan, *, game: str, started: float, engine: str
) -> MeterValues:
    """One scan as the API reports it, whichever engine produced it. Shared so the
    two readers cannot disagree about what a scan *means* -- only about how the
    digits were recognised."""
    mode, currency = _classify(scan.fields)
    balance = scan.fields.get("cash", meter.MeterField()).value
    values = MeterValues(
        mode=mode,
        currency=currency,
        cash=_number(balance) if mode is MeterMode.CASH else None,
        credits=_number(balance) if mode is MeterMode.CREDITS else None,
        win=_number(scan.fields.get("win", meter.MeterField()).value),
        bet=_number(scan.fields.get("bet", meter.MeterField()).value),
        fields={name: _as_field(f) for name, f in scan.fields.items()},
        unmapped=[
            MeterUnmapped(
                value=float(item.value),
                centre=round(item.centre, 4),
                confidence=round(item.confidence, 1),
                text=item.text,
            )
            for item in scan.unmapped
        ],
        band=list(scan.band),
        engine_calls=scan.calls,
        duration_ms=_elapsed_ms(started),
    )
    if values.unmapped:
        # Loud, because this is the one failure that otherwise looks like success.
        logger.warning(
            "Meter read of %s left %d value(s) unmapped at %s -- the layout may "
            "not match the expected one",
            game,
            len(values.unmapped),
            [round(item.centre, 3) for item in values.unmapped],
        )
    logger.info(
        "Read meter of %s with %s: mode=%s cash=%s credits=%s win=%s bet=%s "
        "(%d engine calls)",
        game,
        engine,
        values.mode.value,
        values.cash,
        values.credits,
        values.win,
        values.bet,
        values.engine_calls,
    )
    return values


def _elapsed_ms(started: float) -> int:
    """Milliseconds since ``started``, floored at zero."""
    return max(0, round((time.perf_counter() - started) * 1000))
