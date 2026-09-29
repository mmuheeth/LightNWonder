"""Reads the numbers off a game's meter strip with PaddleOCR -- the sole meter
reader, for every feature that reads one -- reusing :mod:`app.utils.meter`'s
geometry (row bands, column groups, cell crops -- properties of how the game
draws a meter, not of the reading engine) and supplying only the per-cell
recognition step. One call per cell, no escalation ladder; scores every
reading (no whitelist, so :data:`FLOOR` applies with no unscored-reading
exemption); reads in colour since greyscale-and-threshold loses gold-on-light
digits."""

from __future__ import annotations

import re
from decimal import Decimal

from PIL import Image

from app.utils import meter, paddle_ocr
from app.utils.meter import Band, MeterError, MeterField, MeterScan, Unmapped
from app.utils.paddle_ocr import OcrError, parse_number

__all__ = [
    "CONFIDENT",
    "FLOOR",
    "MeterError",
    "extract",
    "fit_band",
    "read_group",
]

# Paddle scores a legible meter cell at ~0.99, so below this a string is artwork
# bleeding into the crop rather than a value -- the same judgement
# ``PaddleOptions.min_confidence`` makes for an orb, and for the same reason.
# Expressed 0-100 rather than 0-1 to match ``MeterField.confidence``'s own
# scale, which is what the API reports regardless of how a reading was scored.
FLOOR = 50.0

# Stop fitting once a band reads this well. Cheaper than the Tesseract reader's
# equivalent (one call a cell rather than up to seven) but not free: every
# further candidate band is another set of Paddle reads.
CONFIDENT = 90.0

# A value, with the currency mark that may be glued to its left. Paddle returns a
# cell as one string, so the token shape ``meter`` looks for applies -- widened
# by the two marks a European skin draws, since unlike Tesseract there is no
# character whitelist here constraining what can come back.
_TOKEN = re.compile(r"[$€£¥]?\d[\d.,]*")
_SYMBOLS = "$€£¥"


def _token(text: str) -> tuple[Decimal | None, str]:
    """The value and its currency symbol out of one cell's recognised text. The
    *longest* match wins rather than the first, so a cell drawn ``$1,234.00`` beside a
    speck the detector also boxed reads as the amount, not the speck."""
    best = ""
    for match in _TOKEN.finditer(text):
        if len(match.group()) > len(best):
            best = match.group()
    if not best:
        return None, ""
    return parse_number(best), best[0] if best[0] in _SYMBOLS else ""


def read_group(
    image: Image.Image,
    box: Band,
    band: Band,
    *,
    options: paddle_ocr.PaddleOptions,
) -> MeterField:
    """Read one cell of the strip with PaddleOCR. Never raises for a crop the engine
    refused -- that is one field of the strip and the others are still worth reading --
    it comes back as a ``MeterField`` with no value, the same shape an empty WIN cell
    produces."""
    crop = meter.cell_crop(image, box, band)
    try:
        result = paddle_ocr.read_image(crop, options=options)
    except OcrError:
        return MeterField(box=box, calls=1)

    text = result.text.strip().replace("\n", " ")
    value, symbol = _token(text)
    return MeterField(
        value=value,
        text=text,
        # Paddle's 0-1 onto the 0-100 the API reports, so a confidence means the
        # same thing whichever engine produced it.
        confidence=(result.confidence or 0.0) * 100.0,
        symbol=symbol,
        box=box,
        calls=1,
    )


def extract(
    image: Image.Image,
    *,
    band: Band,
    options: paddle_ocr.PaddleOptions,
    windows: dict[str, tuple[float, float]] | None = None,
    ordinal: bool = False,
) -> MeterScan:
    """Read every number in ``band`` and file each under the field whose window it sits
    in, via :func:`~app.utils.meter.assign_fields` (by window centre, or left-to-right
    with ``ordinal``). A number belonging to no field is reported as
    :class:`~app.utils.meter.Unmapped` rather than filed under the nearest name -- the
    warning that a skin's layout doesn't match the declared one. Unlike the Tesseract
    reader, :data:`FLOOR` applies to every reading here since there are no unscored
    ones."""
    spans = meter.DEFAULT_WINDOWS if windows is None else windows
    boxes = meter.ink_groups(image, band)
    # Sequentially, unlike the Tesseract reader's thread pool. Paddle runs
    # in-process behind its own engine lock, so threads would queue on that lock
    # while still competing for cores -- the same measurement that has
    # `analyze_spin` read scatter orbs one at a time rather than gathered.
    results = [read_group(image, box, band, options=options) for box in boxes]

    readings: list[tuple[float, MeterField]] = []
    calls = sum(reading.calls for reading in results)
    for box, reading in zip(boxes, results, strict=True):
        if reading.value is None or reading.confidence < FLOOR:
            continue
        readings.append(((box[0] + box[1]) / 2 / image.width, reading))

    fields, claimed = meter.assign_fields(spans, readings, ordinal=ordinal)

    reach = meter.reportable_span(spans)
    unmapped = tuple(
        Unmapped(
            value=reading.value,
            centre=centre,
            confidence=reading.confidence,
            text=reading.text,
        )
        for index, (centre, reading) in enumerate(readings)
        if index not in claimed
        and reading.value is not None
        and reach[0] <= centre <= reach[1]
    )
    return MeterScan(fields=fields, unmapped=unmapped, band=band, calls=calls)


def fit_band(
    image: Image.Image,
    *,
    options: paddle_ocr.PaddleOptions,
    windows: dict[str, tuple[float, float]] | None = None,
    ordinal: bool = False,
) -> tuple[Band, MeterScan]:
    """Choose the row band that reads best, and return it with its scan. Only reached
    for a game whose config declares no ``meter.band`` -- both shipped games declare
    one, so this is the fallback for an unmeasured skin."""
    best: tuple[Band, MeterScan] | None = None
    for candidate in meter.row_bands(image):
        scan = extract(
            image, band=candidate, options=options, windows=windows, ordinal=ordinal
        )
        # Mean confidence first, then read count as the tiebreaker.
        if best is None or (scan.score, scan.read_count) > (
            best[1].score,
            best[1].read_count,
        ):
            best = (candidate, scan)
        if scan.read_count >= meter.ENOUGH_FIELDS and scan.score >= CONFIDENT:
            break
    if best is None:
        raise MeterError("the strip has no rows bright enough to read")
    return best
