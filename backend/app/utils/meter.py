"""Reads the numbers off a game's meter strip (``roi.cash_meter``: balance,
last win, current bet). Four things measured on real captures shape the
design: digits are located per image (brightest columns) rather than by fixed
box, since the strip isn't aligned identically every launch; which number is
which comes from a per-field width window, since the ``CASH``/``WIN``/``BET``
labels are too small to OCR and cell detection can't order an empty cell;
the row band is fitted per skin via :func:`fit_band` rather than assumed,
since labels sit below cells on one game and inside them on another; and a
field is read at psm 8 first, escalating only while unconvincing, taking the
engine's most *confident* reading rather than the most popular one.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image

from app.utils import ocr

__all__ = [
    "DEFAULT_WINDOWS",
    "Band",
    "MeterError",
    "MeterField",
    "MeterScan",
    "Unmapped",
    "extract",
    "fit_band",
    "row_bands",
]


class MeterError(ValueError):
    """The strip is unusable -- no area, or nothing bright enough to read."""


# Digits, separators and currency symbols these games draw -- a hint to the LSTM
# engine, keeping a cell border from arriving as a letter.
WHITELIST = "0123456789.,$¥"

# Brightness (as a fraction of the strip's own max) for a column to count as a
# glyph. Swept over saved strips: 0.62 swallows cell borders into the group; 0.82
# hugs the glyphs cleanly.
INK_LEVEL = 0.82

# Slack columns around a located group -- enough to not shave a glyph's dim edge,
# few enough to keep the cell border out.
CLUSTER_PAD = 2

# Gap (as a share of band height) that splits two lit runs into different numbers.
# Swept against 20 saved strips scoring on wrong (not just missing) values; 0.55
# is tight enough to keep a decimal point from splitting `$1.76`, loose enough to
# keep ~19px cell separations apart.
GAP_SHARE = 0.55

# Narrower than this and it's a cell corner or artwork speck, not a value.
MIN_GROUP_WIDTH = 10

# A bright core thinner than this is a border highlight, not a line of text.
MIN_CORE_ROWS = 3

# Share of a core's height to grow it by, to reach the glyph's dimmer top/bottom.
# 0.35 reproduces the hand-tuned bands measured on both known skins.
BAND_PAD_SHARE = 0.35

# Caps the thread pool -- never a reason to run more engines than there are cells.
MAX_WORKERS = 4

# Fields a candidate band must read before its confidence can end the search. A
# strip has three cells and the middle (WIN) is often empty between spins.
ENOUGH_FIELDS = 2

# Stop escalating once a field reads this well; ignore a reading below the floor.
# Every value verified correct on saved strips scored 79 or better.
CONFIDENT = 90.0
FLOOR = 40.0

# Read at the first rung, adding rungs only while unconvincing. psm 8 is "one
# word", 7 is "one text line", 13 is "one raw line" with layout analysis off --
# the only mode that reads one skin's WIN value.
LADDER: tuple[tuple[tuple[int, float], ...], ...] = (
    ((8, 8.0),),
    ((7, 8.0), (13, 8.0)),
    ((8, 6.0), (7, 6.0), (8, 10.0), (7, 10.0)),
)

# Where each field sits, as a fraction of the strip's width -- and the strip is a
# crop of the content box, not the canvas, so these track the game regardless of
# resize. Union of both known skins, so wide: a fallback for an unmeasured game,
# not a substitute for measuring one -- both shipped games declare their own
# ``meter.windows`` since the union is too loose for either.
DEFAULT_WINDOWS: dict[str, tuple[float, float]] = {
    "cash": (0.26, 0.37),
    "win": (0.46, 0.57),
    "bet": (0.63, 0.79),
}

# A value, with the symbol that may be glued to its left.
_TOKEN = re.compile(r"[$¥]?\d[\d.,]*")
_SYMBOLS = "$¥"

Band = tuple[int, int]
"""Rows of the strip the values sit in, as ``(top, bottom)`` pixels."""


@dataclass(frozen=True)
class MeterField:
    """One number read off the strip."""

    value: Decimal | None = None
    text: str = ""
    """Raw text of the winning read, engine junk included."""

    confidence: float = 0.0
    symbol: str = ""
    """Currency symbol the engine recognised on this value, if any."""

    box: Band | None = None
    """Columns the value occupied, for drawing it back onto the strip."""

    calls: int = 0
    """Engine invocations this field cost. The escalation's own receipt."""


@dataclass(frozen=True)
class Unmapped:
    """A confident number that belongs to no field -- reported rather than
    pushed into the nearest window, since a value silently filed under the
    wrong name is worse than one that arrives asking to be looked at."""

    value: Decimal
    centre: float
    """Where it sat, as a fraction of the strip's width."""

    confidence: float
    text: str


@dataclass(frozen=True)
class MeterScan:
    """Everything one pass over a strip produced."""

    fields: dict[str, MeterField] = field(default_factory=dict)
    unmapped: tuple[Unmapped, ...] = ()
    band: Band = (0, 0)
    calls: int = 0

    @property
    def score(self) -> float:
        """Mean confidence of the fields that read, for comparing two bands.
        Mean, not sum -- a sum rewards a band that reads extra junk from the
        label rows over one that correctly leaves an empty cell empty."""
        read = [f.confidence for f in self.fields.values() if f.value is not None]
        return sum(read) / len(read) if read else 0.0

    @property
    def read_count(self) -> int:
        """How many fields produced a number."""
        return sum(1 for f in self.fields.values() if f.value is not None)


def _grey(image: Image.Image) -> np.ndarray:
    """The strip as a float greyscale array."""
    if image.width <= 0 or image.height <= 0:
        raise MeterError("the strip must have a non-zero width and height")
    return np.asarray(image.convert("L"), dtype=float)


def row_bands(image: Image.Image) -> tuple[Band, ...]:
    """Candidate row bands, from the strip's own ink profile. The strict
    ink threshold finds each text line's bright core, but a core alone crops
    off a glyph's dimmer top/bottom -- so each core is padded outward by a
    share of its own height instead of lowering the threshold, which would
    merge values into labels. Which core holds the values can't be told from
    the profile alone, so every candidate is offered and :func:`fit_band`
    picks by reading.
    """
    grey = _grey(image)
    height = grey.shape[0]
    ceiling = grey.max()
    if ceiling <= 0:
        return ((0, height),)

    lit = (grey > ceiling * INK_LEVEL).sum(axis=1)
    cores: list[Band] = []
    start: int | None = None
    for row, count in enumerate(lit):
        if count > 0 and start is None:
            start = row
        elif count == 0 and start is not None:
            cores.append((start, row))
            start = None
    if start is not None:
        cores.append((start, height))
    cores = [core for core in cores if core[1] - core[0] >= MIN_CORE_ROWS]

    candidates: list[Band] = []
    for index, (top, bottom) in enumerate(cores):
        pad = max(1, round((bottom - top) * BAND_PAD_SHARE))
        # Never grow into the next line of text -- that's the separation the
        # strict threshold exists to find.
        floor = cores[index - 1][1] if index > 0 else 0
        ceil_ = cores[index + 1][0] if index + 1 < len(cores) else height
        candidates.append((max(floor, top - pad), min(ceil_, bottom + pad)))

    # The whole strip, last: it's the widest band and would end the search early
    # by reading confidently on a skin with labels below the cells. Cores go first.
    if (0, height) not in candidates:
        candidates.append((0, height))
    return tuple(dict.fromkeys(candidates))


def ink_groups(image: Image.Image, band: Band) -> tuple[Band, ...]:
    """Column ranges holding numbers, one per cell that has something in it."""
    grey = _grey(image)
    top, bottom = band
    window = grey[top:bottom, :]
    if window.size == 0:
        return ()
    ceiling = window.max()
    if ceiling <= 0:
        return ()

    lit = np.flatnonzero(window.max(axis=0) > ceiling * INK_LEVEL)
    if lit.size == 0:
        return ()

    gap = max(6, round((bottom - top) * GAP_SHARE))
    groups: list[Band] = []
    start = int(lit[0])
    previous = start
    for current in lit[1:]:
        if int(current) - previous > gap:
            groups.append((start, previous + 1))
            start = int(current)
        previous = int(current)
    groups.append((start, previous + 1))
    return tuple(
        (left, right) for left, right in groups if right - left >= MIN_GROUP_WIDTH
    )


def _token(text: str) -> tuple[Decimal | None, str]:
    """The value and its symbol out of raw engine text. Takes the longest
    number-shaped token rather than the first, since a cell border sometimes
    arrives as a leading digit or comma welded to the value."""
    best = ""
    for match in _TOKEN.finditer(text):
        if len(match.group()) > len(best):
            best = match.group()
    if not best:
        return None, ""
    return ocr.parse_number(best), best[0] if best[0] in _SYMBOLS else ""


def read_group(
    image: Image.Image, box: Band, band: Band, *, executable: Path
) -> MeterField:
    """Read one number, escalating only while the reading is unconvincing.
    Never raises for a failed read -- an unreadable field comes back empty
    beside the ones that worked."""
    crop = image.crop(
        (
            max(0, box[0] - CLUSTER_PAD),
            band[0],
            min(image.width, box[1] + CLUSTER_PAD),
            band[1],
        )
    )
    best = MeterField(box=box)
    calls = 0
    for rung in LADDER:
        for psm, upscale in rung:
            options = ocr.OcrOptions(psm=psm, upscale=upscale, char_whitelist=WHITELIST)
            try:
                result = ocr.read_image(crop, executable=executable, options=options)
            except ocr.OcrError:
                # A crop the engine choked on is one field, not the whole strip.
                continue
            calls += 1
            text = result.text.strip().replace("\n", " ")
            value, symbol = _token(text)
            confidence = result.confidence or 0.0
            if value is not None and confidence > best.confidence:
                best = MeterField(
                    value=value,
                    text=text,
                    confidence=confidence,
                    symbol=symbol,
                    box=box,
                )
        if best.confidence >= CONFIDENT:
            break
    return MeterField(
        value=best.value,
        text=best.text,
        confidence=best.confidence,
        symbol=best.symbol,
        box=box,
        calls=calls,
    )


def extract(
    image: Image.Image,
    *,
    executable: Path,
    band: Band,
    windows: dict[str, tuple[float, float]] | None = None,
) -> MeterScan:
    """Read every number in ``band`` and file each one under its field
    (``windows`` maps field name to the span of the strip's width it owns,
    defaulting to :data:`DEFAULT_WINDOWS`)."""
    spans = DEFAULT_WINDOWS if windows is None else windows
    boxes = ink_groups(image, band)
    # Concurrent: each read is a ~200ms Tesseract subprocess, and `subprocess.run`
    # releases the GIL while it waits, so threads fit here.
    if boxes:
        with ThreadPoolExecutor(max_workers=min(len(boxes), MAX_WORKERS)) as pool:
            results = list(
                pool.map(
                    lambda box: read_group(image, box, band, executable=executable),
                    boxes,
                )
            )
    else:
        results = []

    readings: list[tuple[float, MeterField]] = []
    calls = sum(reading.calls for reading in results)
    for box, reading in zip(boxes, results, strict=True):
        if reading.value is None or reading.confidence < FLOOR:
            continue
        readings.append(((box[0] + box[1]) / 2 / image.width, reading))

    fields: dict[str, MeterField] = {}
    claimed: set[int] = set()
    for name, (low, high) in spans.items():
        # Several groups can share a window (e.g. an inline label bracket) --
        # the one the engine was surest of is the value.
        best_index: int | None = None
        for index, (centre, reading) in enumerate(readings):
            if not low <= centre <= high:
                continue
            if (
                best_index is None
                or reading.confidence > readings[best_index][1].confidence
            ):
                best_index = index
        if best_index is None:
            fields[name] = MeterField()
            continue
        fields[name] = readings[best_index][1]
        claimed.add(best_index)

    unmapped = tuple(
        Unmapped(
            value=reading.value,
            centre=centre,
            confidence=reading.confidence,
            text=reading.text,
        )
        for index, (centre, reading) in enumerate(readings)
        if index not in claimed and reading.value is not None
    )
    return MeterScan(fields=fields, unmapped=unmapped, band=band, calls=calls)


def fit_band(
    image: Image.Image,
    *,
    executable: Path,
    windows: dict[str, tuple[float, float]] | None = None,
) -> tuple[Band, MeterScan]:
    """Choose the row band that reads best, and return it with its scan.
    Candidates come from the strip's ink profile, not a sweep of every
    plausible edge pair, so this costs two or three scans. Callers should
    cache the answer -- the band is a property of the skin, not the frame."""
    best: tuple[Band, MeterScan] | None = None
    for candidate in row_bands(image):
        scan = extract(image, executable=executable, band=candidate, windows=windows)
        # Mean confidence first, then read count as the tiebreaker.
        if best is None or (scan.score, scan.read_count) > (
            best[1].score,
            best[1].read_count,
        ):
            best = (candidate, scan)
        if scan.read_count >= ENOUGH_FIELDS and scan.score >= CONFIDENT:
            # Several fields read confidently: nothing left to beat, and each
            # further candidate costs more 200ms engine calls.
            break
    if best is None:
        raise MeterError("the strip has no rows bright enough to read")
    return best
