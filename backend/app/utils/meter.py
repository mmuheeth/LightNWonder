"""Reading the numbers off a game's meter strip.

A meter strip is the bar across the bottom of a slot game carrying its balance,
its last win and its current bet -- what :mod:`app.utils.image_roi` cuts out as
``roi.cash_meter``. This module turns that picture into those numbers. It takes an
image and a path to the engine; where the image came from and which game it
belongs to are the caller's business, like every other module in this package.

Four things measured on this project's own captures decide everything here, and
each one rules out a simpler design.

**The values are found, not looked up.** Fixed pixel boxes cannot work: the same
683x29 strip arrives in three different horizontal alignments, because
``roi.cash_meter`` is a fraction of the *frame* and the game does not fill the
canvas identically every launch. A hand-tuned box table reads 15 of 20 strips and
mangles the rest -- truncating ``$996.10`` to ``$996.1``, welding a cell border
onto ``,$999.12``. So the digits are located per image instead: they are the
brightest thing in the strip, so a column carrying ink is one whose brightest
pixel is close to the strip's own maximum, and neighbouring lit columns group into
one number. Relative to each image, so a change of skin, palette or background
costs nothing.

**Which number is which comes from position, because the labels are unreadable.**
``CASH``, ``WIN`` and ``BET`` are printed 8px tall and letter-spaced over
artwork. Tight crops at 10x return ``'LA'``, ``'C'``, ``'CREOS'`` -- confidence 0
to 45, against 79 to 97 for the values. Reading them is not a tuning problem, it
is not possible, so it is not attempted. Each field owns a window of the strip's
width instead. Detecting the *cells* and taking them in order would avoid the
windows, but a cell full of bright digits splits into fragments while an empty
cell stays whole, and 11 of 20 strips have an empty WIN cell -- so ordering
cannot be established from geometry either.

**The row band is per skin, so it is fitted rather than assumed.** One game prints
its labels *below* the cells and another *inside* them, so there is no band that
suits both: read the full height of the first and its values collapse into
``4000.07`` and ``30.88``, while the second is unharmed. The band cannot be
derived from the image's shape, but it can be *chosen* -- the row-ink profile
offers two or three candidate bands and the one that reads with the most
confidence wins. :func:`fit_band` does that; the caller caches the answer.

**One reading is usually enough, and the confident one wins.** psm 7 reads
``49531`` as ``49331``; psm 8 gets that right but turns ``75`` into ``75.``; and
one skin's orange WIN value is read by psm 13 alone, every other mode returning
nothing at all for it. So a field is read at psm 8 first and escalated only while
it is still unconvincing, and the reading the engine was *surest* of is taken --
not the most popular one. Where those two rules disagreed on this project's
strips, confident was right and popular was wrong every time: ``0.88`` over
``0.83``, ``75`` over ``5``, ``99371`` over ``99374``. Escalating rather than
always reading nine ways is what keeps a strip at roughly six engine calls
instead of twenty-seven, which matters because each one costs about 200ms.
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


# The digits, their separators, and the currency symbols these games draw. A hint
# rather than a rule under the LSTM engine, but it keeps a cell border from
# arriving as a letter.
WHITELIST = "0123456789.,$¥"

# How bright a column must be, as a fraction of the strip's own brightest pixel,
# to count as carrying a glyph. Swept over this project's strips: at 0.62 the
# groups swallow the cell borders, which psm 8 then collapses -- 49883 arrives as
# 43 -- and at 0.68 nine strips of twenty disagree with themselves. 0.82 hugs the
# glyphs.
INK_LEVEL = 0.82

# Columns of slack around a located group. Two: enough that a glyph's dimmest edge
# is not shaved off, few enough that the cell border stays out.
CLUSTER_PAD = 2

# How far apart two lit column runs must be to be different numbers, as a share of
# the band's height. Scaled to the glyphs rather than to the strip's width, because
# what has to be told apart is the gap *inside* a number from the gap *between*
# cells, and both scale with the text.
#
# Swept against the 20 saved strips, scoring on values that are wrong rather than
# only on values that are missing -- a wrong number looks like an answer and a
# missing one does not:
#   0.45, 0.50  ->  one wrong  (a balance read as 10.0)
#   0.55        ->  none wrong, one missing
#   0.60        ->  none wrong, three missing
# So 0.55: tight enough to keep a decimal point from splitting `$1.76` into `$1`
# and `76`, loose enough that the ~19px cell separations stay separate.
GAP_SHARE = 0.55

# Narrower than this and it is a cell corner or a speck of artwork, not a value.
MIN_GROUP_WIDTH = 10

# A bright core thinner than this is a border highlight or a stray lit row, not
# a line of text.
MIN_CORE_ROWS = 3

# How far to grow a core to reach the glyph's dimmer top and bottom, as a share of
# the core's own height. 0.35 turns the cores measured on both known skins into the
# bands that were hand-tuned for them -- (6, 16) becomes (2, 19) against a measured
# (3, 20), and (0, 13) becomes (0, 18) against a measured (1, 18).
BAND_PAD_SHARE = 0.35

# A meter strip holds a handful of numbers, so this caps the pool rather than
# sizing it -- there is never a reason to run more engines than there are cells.
MAX_WORKERS = 4

# How many fields a candidate band must read before its confidence is allowed to
# end the search. A meter strip has three cells and the middle one is empty
# between spins, so two is "as many as there usually are".
ENOUGH_FIELDS = 2

# Stop escalating once a field reads this well, and ignore a reading below the
# floor entirely. Every value verified correct on this project's strips scored 79
# or better.
CONFIDENT = 90.0
FLOOR = 40.0

# Read at the first rung, then add rungs only while the field is unconvincing.
# psm 8 is "one word", 7 is "one text line", 13 is "one raw line" with layout
# analysis switched off -- the only mode that reads one skin's WIN value.
LADDER: tuple[tuple[tuple[int, float], ...], ...] = (
    ((8, 8.0),),
    ((7, 8.0), (13, 8.0)),
    ((8, 6.0), (7, 6.0), (8, 10.0), (7, 10.0)),
)

# Where each field sits, as a fraction of the strip's width. Measured across both
# known skins, which agree despite looking nothing alike -- cash centres land at
# 0.373-0.412 and 0.380-0.402, win at 0.502-0.526 and 0.502-0.504, bet at
# 0.622-0.643 and 0.605-0.616 -- because meter bars put these three cells at
# similar proportions. The windows clear the junk between them: a game logo at
# 0.27, inline label brackets at 0.35 and 0.45, a denomination badge at 0.70-0.82.
DEFAULT_WINDOWS: dict[str, tuple[float, float]] = {
    "cash": (0.33, 0.44),
    "win": (0.47, 0.55),
    "bet": (0.58, 0.66),
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
    """A confident number that belongs to no field.

    Reported rather than pushed into the nearest window. A meter strip from a skin
    that orders its cells differently reads perfectly well and means something
    else entirely, and a value silently filed under the wrong name is worse than
    one that arrives asking to be looked at.
    """

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

        The mean and not the sum. A band that takes in the label rows reads *more*
        things -- junk among them, at around 84 -- and a sum rewards it for the
        extra noise: it beat the right band, which read two fields at 96 and
        correctly left an empty WIN cell empty. Averaging asks the question that
        matters instead, which is how well what was read was read.
        """
        read = [f.confidence for f in self.fields.values() if f.value is not None]
        return sum(read) / len(read) if read else 0.0

    @property
    def read_count(self) -> int:
        """How many fields produced a number."""
        return sum(1 for f in self.fields.values() if f.value is not None)


def _grey(image: Image.Image) -> np.ndarray:
    """The strip as a float greyscale array.

    Raises:
        MeterError: if the image has no area to read.
    """
    if image.width <= 0 or image.height <= 0:
        raise MeterError("the strip must have a non-zero width and height")
    return np.asarray(image.convert("L"), dtype=float)


def row_bands(image: Image.Image) -> tuple[Band, ...]:
    """Candidate row bands, from the strip's own ink profile.

    Two thresholds, because no single one does both jobs. The strict level that
    finds the *columns* also finds each line of text's bright core -- ``(6, 16)``
    for the values and ``(19, 27)`` for the labels on this project's fire skin, a
    clean separation. But a core is only the middle of a glyph: reading rows 6 to
    16 of a fourteen-row digit turns ``$1.76`` into ``76`` and ``$1,000.07`` into
    ``41000.07``. Lowering the threshold to catch the glyph's dimmer top and bottom
    merges the values into the labels and loses the separation entirely -- measured
    at every level from 0.3 to 0.7.

    So the core locates the line and is then padded outwards by a share of its own
    height, stopping short of the neighbouring core. That reproduces the bands
    measured by hand on both known skins, at any capture size.

    Which core holds the values still cannot be told from the profile -- on one
    strip the label rows carry *more* ink than the digits -- so every candidate is
    offered and :func:`fit_band` picks by reading.
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
        # Never grow into the next line of text: that is the separation the strict
        # threshold was used to find, and giving it away undoes the whole point.
        floor = cores[index - 1][1] if index > 0 else 0
        ceil_ = cores[index + 1][0] if index + 1 < len(cores) else height
        candidates.append((max(floor, top - pad), min(ceil_, bottom + pad)))

    # The whole strip, last and only as a fallback. It is the widest band and so
    # the tempting one to try first, but on a skin with labels below the cells it
    # reads the balance confidently and everything else as junk -- which looks like
    # success and ends the search. The cores the profile found go first.
    if (0, height) not in candidates:
        candidates.append((0, height))
    return tuple(dict.fromkeys(candidates))


def ink_groups(image: Image.Image, band: Band) -> tuple[Band, ...]:
    """Column ranges holding numbers, one per cell that has something in it.

    Raises:
        MeterError: if the strip has no area.
    """
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
    """The value and its symbol out of raw engine text.

    Takes the longest number-shaped token rather than the first, because a cell
    border sometimes arrives as a leading ``1`` or ``,`` welded to the value.
    """
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

    Never raises for a failed read: a field the engine could not manage comes back
    empty beside the ones that worked, which is the same treatment
    :mod:`app.services.ocr` gives a region it cannot read.
    """
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
    """Read every number in ``band`` and file each one under its field.

    Args:
        image: The meter strip, at whatever size it was cropped.
        executable: Path to ``tesseract``.
        band: Rows the values sit in -- see :func:`fit_band`.
        windows: Field name to the span of the strip's width it owns. Defaults to
            :data:`DEFAULT_WINDOWS`.

    Raises:
        MeterError: if the strip has no area to read.
    """
    spans = DEFAULT_WINDOWS if windows is None else windows
    boxes = ink_groups(image, band)
    # Concurrently, because each read is a Tesseract subprocess at roughly 200ms
    # and a strip has three or four of them -- `subprocess.run` releases the GIL
    # while it waits, so threads are the right shape here and the engine is a
    # separate process per call with no state to share.
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
        # Several groups can share a window -- an inline label bracket sits inside
        # the cash cell's on one skin. The one the engine was surest of is the
        # value; the bracket reads as no number at all and never gets here.
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

    The candidates come from the strip's ink profile rather than from a sweep of
    every plausible pair of edges, so this costs two or three scans rather than
    twenty. Callers are expected to remember the answer: the band is a property of
    the skin, not of the frame.

    Raises:
        MeterError: if the strip has no area, or no candidate produced a reading.
    """
    best: tuple[Band, MeterScan] | None = None
    for candidate in row_bands(image):
        scan = extract(image, executable=executable, band=candidate, windows=windows)
        # Mean confidence first, then how much was read: between two bands the
        # engine is equally sure of, the one that found more fields is better.
        if best is None or (scan.score, scan.read_count) > (
            best[1].score,
            best[1].read_count,
        ):
            best = (candidate, scan)
        if scan.read_count >= ENOUGH_FIELDS and scan.score >= CONFIDENT:
            # Several fields, all read confidently: nothing left to beat, and each
            # further candidate is a fistful of 200ms engine calls. The count
            # matters as much as the confidence -- a band that reads one field
            # perfectly and misses two is not a good band, and stopping on it is
            # how the whole-strip fallback used to win.
            break
    if best is None:
        raise MeterError("the strip has no rows bright enough to read")
    return best
