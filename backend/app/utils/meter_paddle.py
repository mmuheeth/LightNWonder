"""Reads the numbers off a game's meter strip with PaddleOCR.

**The geometry is not re-invented here.** :mod:`app.utils.meter` already knows
how to find the row band the values sit in (:func:`~app.utils.meter.row_bands`),
the column groups the cells occupy (:func:`~app.utils.meter.ink_groups`) and how
to prepare one cell's picture (:func:`~app.utils.meter.cell_crop`) -- and every
one of those is a property of how the *game* draws a meter, not of the engine
that reads it. This module imports that work and replaces only the step that
turns one column group into a number.

Why a second reader rather than switching :mod:`app.utils.meter` over: both
engines stay available on purpose. Tesseract is what Analyze Spin's meter
validation was measured against, and the two do not read a strip identically --
so which one answers is a per-feature choice, and Evaluate Screen makes the
other one.

What Paddle changes about reading a cell:

* **One call, not a ladder.** Tesseract needs a page-segmentation mode and a
  scale guessed per crop, so :func:`~app.utils.meter.read_group` tries up to
  seven subprocesses and keeps the best. Paddle's detector finds the text line
  itself and its recogniser scores what it read, so there is one call and its
  own confidence decides.
* **No whitelist, and therefore no unscored reading.** Tesseract will not score
  a word produced under ``tessedit_char_whitelist`` (see
  :data:`~app.utils.meter.UNMEASURED`), which is why that module carries a whole
  ranking rule for transcriptions with no score. Paddle scores everything it
  returns, so a confidence here is always a real one and
  :data:`FLOOR` can simply be applied.
* **No flattening.** :func:`app.utils.paddle_ocr.preprocess` hands the crop over
  in colour; the greyscale-and-threshold pass Tesseract needs is what loses
  gold-on-light meter digits.
"""

from __future__ import annotations

import re
from decimal import Decimal

from PIL import Image

from app.utils import meter, paddle_ocr
from app.utils.meter import Band, MeterError, MeterField, MeterScan, Unmapped
from app.utils.ocr import OcrError, parse_number

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
# Expressed 0-100 rather than 0-1 to match ``MeterField.confidence``, which is
# Tesseract's scale and is what the API reports whichever engine read the strip.
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
    """The value and its currency symbol out of one cell's recognised text.

    The *longest* match wins rather than the first: a cell drawn ``$1,234.00``
    beside a speck the detector also boxed should read as the amount, not the
    speck.
    """
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
    """Read one cell of the strip with PaddleOCR.

    Never raises for a crop the engine refused: that is one field of the strip,
    and the others are still worth reading. It comes back as a ``MeterField``
    with no value, which is the same shape an empty WIN cell produces.
    """
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
) -> MeterScan:
    """Read every number in ``band`` and file each under the field whose window it
    sits in.

    The filing rule is ``meter``'s, not a second one: a cell is claimed by the
    window its centre falls in, the best reading wins a window two groups share,
    and a number belonging to no window is reported as
    :class:`~app.utils.meter.Unmapped` rather than filed under the nearest name --
    that list is the warning that a skin's layout does not match the declared
    one.

    One difference from the Tesseract reader, and it is a simplification: the
    :data:`FLOOR` here applies to every reading. There is no unscored-reading
    exemption because there are no unscored readings.
    """
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

    fields: dict[str, MeterField] = {}
    claimed: set[int] = set()
    for name, (low, high) in spans.items():
        best_index: int | None = None
        for index, (centre, reading) in enumerate(readings):
            if not low <= centre <= high:
                continue
            if best_index is None or reading.rank > readings[best_index][1].rank:
                best_index = index
        if best_index is None:
            fields[name] = MeterField()
            continue
        fields[name] = readings[best_index][1]
        claimed.add(best_index)

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
) -> tuple[Band, MeterScan]:
    """Choose the row band that reads best, and return it with its scan.

    Only reached for a game whose config declares no ``meter.band``; both shipped
    games declare one, so this is the fallback for an unmeasured skin.
    """
    best: tuple[Band, MeterScan] | None = None
    for candidate in meter.row_bands(image):
        scan = extract(image, band=candidate, options=options, windows=windows)
        # Mean confidence first, then read count as the tiebreaker -- the same
        # comparison the Tesseract reader makes.
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
