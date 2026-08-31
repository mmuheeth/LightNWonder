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

Two of those bite in ways worth knowing before touching a constant here.

One band has to serve three cells that are not the same height, so it is
measured against the shortest and the tallest arrives clipped or flush against
the edge -- which Tesseract reads as a glyph with an extra stroke, turning `105`
into `4105` at confidence 83. Hence :func:`_grown` and :func:`_padded`: every
value gets :data:`GLYPH_MARGIN_SHARE` clear rows, taken from the strip where they are
really there and synthesised where they are not.

And "most confident" needs a caveat, because the engine does not always say.
Under a ``tessedit_char_whitelist`` Tesseract 5's LSTM reports confidence
exactly 0 for a word it read perfectly -- see :data:`UNMEASURED`, which is what
:attr:`MeterField.rank` and :data:`FLOOR` are written around. A reading with no
score is still a reading.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

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

# Clear background a glyph needs above and below it before the engine sees it, as
# a share of the band's own height. Not cosmetic: Tesseract reads a glyph flush
# against the edge of its image as one with an extra stroke -- `105` came back
# `4105` at confidence 83 and `$1,250.00` as `$4.7250.00`, both from a crop that
# is perfectly legible by eye. It bites the tallest cell first, because one band
# has to serve all three: on FortuneOx's 1080x75 strips the WIN digits stand 3
# rows taller than CASH's and BET's, so a band that clears their borders leaves
# WIN's touching both edges. Made up from the strip where the rows are really
# there (:func:`_grown`) and synthesised only for what is left (:func:`_padded`),
# since the rows either side of a value are often the cell border and the label --
# what the band exists to exclude. Topped up rather than always added, because a
# *fitted* band already pads its cores (:data:`BAND_PAD_SHARE`) and padding one
# that has room costs a digit: 4 unconditional rows turn a 459x29 strip's `49531`
# into `49331`.
#
# A *share*, not a pixel count, and that is the whole point. Four fixed rows are
# ample clearance on a 29-row band and thin on a 43-row one, so at a 1.5x canvas
# the cell border welded a leading `1` onto `$1,250.00` and dragged a correct
# reading below :data:`FLOOR`. Scaling with the band holds from a 0.35x canvas to
# 2x. Every pixel-measured constant in this module has to be read this way -- a
# meter reader is a pile of measurements, and a canvas change invalidates the ones
# expressed in pixels while leaving every fraction correct.
GLYPH_MARGIN_SHARE = 0.18

# Never fewer than this, however small the band: two rows is the least that reads
# as background rather than as a border touching the glyph.
MIN_GLYPH_MARGIN = 2

# Brightness for a row to count as holding part of a glyph when that margin is
# measured, as a share of the crop's own range above its background. Deliberately
# far looser than :data:`INK_LEVEL`, and for the opposite reason: that one wants
# glyph *cores*, to keep a cell border out of a column group, while this one wants
# a glyph's dimmest extremity, since a stroke Tesseract can see is a stroke that
# needs clearance. At 0.82 the WIN cell measures 4 clear rows it does not have --
# its digits fade from 250 at the core to 150 at the edge, and every row of the
# crop but one is part of a digit.
GLYPH_EDGE = 0.15

# The share of a row's width that has to be lit for it to be the cell's own border
# rather than a line of text, which is where growth stops: a drawn rule lights
# nearly every column, a row of glyphs only its strokes. Growth itself is limited
# to :data:`GLYPH_MARGIN_SHARE` of the band, per edge.
#
# One band serves three cells of different heights, so the tallest can arrive
# clipped -- and a clipped glyph cannot be repaired by padding, only by going back
# to the strip for the rows left out. Growth is what lets one declared fraction
# hold across capture resolutions: the same band is 29 rows of a 1080x75 strip and
# 11 of a 421x29 one, and 11 rows have no slack for a meter row that stands 11
# tall.
BORDER_SHARE = 0.8

# Gap (as a share of band height) that splits two lit runs into different numbers.
# Swept against 20 saved strips scoring on wrong (not just missing) values; 0.55
# is tight enough to keep a decimal point from splitting `$1.76`, loose enough to
# keep ~19px cell separations apart.
GAP_SHARE = 0.55

# Floor under that gap, in pixels. A band tight enough to clear the labels at a
# small capture size leaves the share below the width of a value's own decimal
# point: at 459x29 the band is 11 rows, 0.55 of it is 6, and `$1.76` arrives as
# `$1.` and `76` -- the second of which reads 76 and wins its window. Measured
# over the saved strips: gaps inside one value reach 8 and the tightest gap
# between two cells is 11, so 9 sits between them.
#
# It only ever binds on a small capture, and there it is a genuine trade rather
# than a tuning: a 1080x75 strip computes 16 from the share alone and never
# reaches this, a 459x29 one needs 9 or it splits a value, and a 405x28 one needs
# 5 or it welds CASH to WIN. No single number serves all three, and this is the
# one constant here that a share could not rescue -- both a width share and a
# band share were measured, and the width share was far worse (it fragments
# values at every size). The reason is that the two gaps being told apart, one
# inside a value and one between two cells, converge as the strip shrinks: at
# 1080x75 they measure 9 and 17, and by 405x28 there is no longer daylight
# between them. Sized for the canvas the cabinet runs, where the share governs
# and this does not, and the cost is that the meter is unreliable below roughly
# a third of that canvas. See the sweep in `test_meter.py`.
MIN_GAP = 9

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

# Tesseract 5's LSTM engine reports confidence 0 -- not a low score, exactly 0 --
# for a word it produced under `tessedit_char_whitelist`, which it honours as a
# filter but does not score. So 0 from a whitelisted read means "unmeasured", and
# it is the *correct* readings that hit it most: `$2,959.44` comes back verbatim
# from six of the seven rungs at confidence 0 every time. Ranked below any
# measured reading and exempt from :data:`FLOOR`, rather than discarded -- a
# perfect transcription with no score attached is still the value on the meter.
# The whitelist stays, because dropping it costs accuracy where it matters most:
# without it a cell border welds onto the value, `$2` arrives as `s2`, and the
# leading digit is lost to a value that still looks plausible.
UNMEASURED = 0.0

# What an unmeasured reading counts as when :func:`fit_band` compares two bands.
# The floor deliberately: a band that reads values the engine would not score
# still beats one that reads nothing, and still loses to one that reads
# convincingly.
UNMEASURED_SCORE = FLOOR

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

    confidence: float = UNMEASURED
    """0-100 as the engine reports it, or :data:`UNMEASURED` for a whitelisted
    read it declined to score. Read it through :attr:`measured` before comparing
    it to a threshold."""

    symbol: str = ""
    """Currency symbol the engine recognised on this value, if any."""

    box: Band | None = None
    """Columns the value occupied, for drawing it back onto the strip."""

    calls: int = 0
    """Engine invocations this field cost. The escalation's own receipt."""

    @property
    def measured(self) -> bool:
        """Whether :attr:`confidence` is a score at all -- see :data:`UNMEASURED`."""
        return self.confidence > UNMEASURED

    @property
    def rank(self) -> tuple[bool, bool, float]:
        """How two readings of the same crop are compared: one that produced a
        value beats one that did not, one the engine scored beats one it did
        not, and only then does the score itself decide."""
        return self.value is not None, self.measured, self.confidence


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
        label rows over one that correctly leaves an empty cell empty. A field
        the engine declined to score counts as :data:`UNMEASURED_SCORE`, so a
        band whose values all read unmeasured is still preferred to one that
        reads nothing at all."""
        read = [
            f.confidence if f.measured else UNMEASURED_SCORE
            for f in self.fields.values()
            if f.value is not None
        ]
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

    gap = max(MIN_GAP, round((bottom - top) * GAP_SHARE))
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


def _margin(rows: int) -> int:
    """Clear rows a value occupying ``rows`` of them needs around it, from
    :data:`GLYPH_MARGIN_SHARE`. A share rather than a constant so the clearance
    tracks the capture size -- see that constant."""
    return max(MIN_GLYPH_MARGIN, round(rows * GLYPH_MARGIN_SHARE))


def _grown(image: Image.Image, columns: Band, band: Band) -> Band:
    """``band``, extended over ``columns`` on whichever edge its glyphs run into.

    Only the edges they touch, and only while the row gained is not the cell's
    own border -- see :data:`BORDER_SHARE`. A cell whose glyphs already sit clear
    of the band is left on the band exactly, so this can only widen the crop of
    a value that was being cut."""
    top, bottom = band
    left, right = columns
    window = np.asarray(image.convert("L"), dtype=float)[:, left:right]
    if window.size == 0:
        return band
    inside = window[top:bottom]
    if inside.size == 0:
        return band
    floor, ceiling = inside.min(), inside.max()
    if ceiling <= floor:
        return band
    level = floor + (ceiling - floor) * GLYPH_EDGE

    def is_border(row: int) -> bool:
        return bool((window[row] > level).mean() >= BORDER_SHARE)

    lit = window[top:bottom].max(axis=1) > level
    rows = np.flatnonzero(lit)
    if rows.size == 0:
        return band

    reach = _margin(band[1] - band[0])
    if rows[0] == 0:
        while top > 0 and bottom - top < (band[1] - band[0]) + reach:
            if is_border(top - 1):
                break
            top -= 1
    if rows[-1] == inside.shape[0] - 1:
        limit = (band[1] - band[0]) + reach + (band[0] - top)
        while bottom < image.height and bottom - top < limit:
            if is_border(bottom):
                break
            bottom += 1
    return top, bottom


def _padded(crop: Image.Image, margin: int | None = None) -> Image.Image:
    """``crop`` grown until its glyphs have ``margin`` clear rows above and below,
    or unchanged if they already do -- or if it has no background to grow with.

    ``margin`` defaults to what :func:`_margin` makes of the crop's own height;
    :func:`read_group` passes the band's instead, so growing the crop first cannot
    then demand more clearance than the band was measured to need.

    Measured off the crop rather than assumed, on both counts. A band fitted with
    room to spare is left alone and only a tight one is topped up. And a crop with
    no clear row at all is left alone entirely, because the fill has to come from
    somewhere: the band there is not bounding the cell but cutting through it, so
    the edge pixels are the cell's own border -- one FortuneOx crop at 459x29 has
    dark red top corners and bright gold bottom ones, and a border in either tone
    is a new edge rather than the removal of one."""
    if crop.width == 0 or crop.height == 0:
        return crop
    wanted = _margin(crop.height) if margin is None else margin
    if wanted <= 0:
        return crop
    grey = np.asarray(crop.convert("L"), dtype=float)
    floor, ceiling = grey.min(), grey.max()
    if ceiling <= floor:
        return crop
    profile = grey.max(axis=1)
    level = floor + (ceiling - floor) * GLYPH_EDGE
    inked = np.flatnonzero(profile > level)
    if inked.size == 0 or inked.size == crop.height:
        return crop
    pad = wanted - min(int(inked[0]), crop.height - 1 - int(inked[-1]))
    if pad <= 0:
        return crop

    # The quietest row is the background, so it is what the border is made of.
    background = int(np.argmin(profile))
    pixel = crop.getpixel((int(np.argmin(grey[background])), background))
    fill: int | tuple[int, ...] = (
        tuple(int(channel) for channel in pixel)
        if isinstance(pixel, tuple)
        else int(pixel or 0)
    )
    return ImageOps.expand(crop, border=pad, fill=fill)


def read_group(
    image: Image.Image, box: Band, band: Band, *, executable: Path
) -> MeterField:
    """Read one number, escalating only while the reading is unconvincing.
    Never raises for a failed read -- an unreadable field comes back empty
    beside the ones that worked."""
    columns = (max(0, box[0] - CLUSTER_PAD), min(image.width, box[1] + CLUSTER_PAD))
    top, bottom = _grown(image, columns, band)
    crop = _padded(
        image.crop((columns[0], top, columns[1], bottom)),
        _margin(band[1] - band[0]),
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
            candidate = MeterField(
                value=value,
                text=text,
                confidence=result.confidence or UNMEASURED,
                symbol=symbol,
                box=box,
            )
            # Ranked, not compared on confidence alone: an unmeasured reading
            # scores 0, and `0 > 0` would throw away the only transcription
            # there is. See :data:`UNMEASURED`.
            if candidate.rank > best.rank:
                best = candidate
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


def _reportable(spans: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """The span of the strip a stray value is worth reporting from: the windows'
    own reach, widened by the widest of them at each end.

    Widened rather than taken exactly, because a stray *just* outside a window is
    the case this is for -- a cell that moved, or a window measured slightly
    wrong. A whole window's width is the largest miss that still means that;
    further out and it is the strip's chrome, not its cells."""
    if not spans:
        return 0.0, 1.0
    lows = [low for low, _ in spans.values()]
    highs = [high for _, high in spans.values()]
    slack = max(high - low for low, high in spans.values())
    return max(0.0, min(lows) - slack), min(1.0, max(highs) + slack)


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
        # The floor judges a score, so it cannot judge a reading that has none --
        # an unmeasured transcription is carried, and earns its place below by
        # being the best thing in a field's window. See :data:`UNMEASURED`.
        if reading.value is None or (reading.measured and reading.confidence < FLOOR):
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
            if best_index is None or reading.rank > readings[best_index][1].rank:
                best_index = index
        if best_index is None:
            fields[name] = MeterField()
            continue
        fields[name] = readings[best_index][1]
        claimed.add(best_index)

    # Only a *scored* stray inside the cells' own reach is worth reporting, on
    # both counts because of what this list is for: it warns that the windows may
    # not match the skin. The exemption granted above is for a reading with a
    # field behind it -- knowing which cell it came out of is what makes an
    # unscored transcription trustworthy, and a stray has no such backing. And a
    # number out at the strip's extremes is not evidence about the windows but
    # fixed chrome beside them: the meter row is full-width, so it also carries
    # "Line 1 Pays 25" at 0.03, "50 CREDIT GAME ACTIVE" at 0.84 and the
    # change-denom button at 0.96, none of which is a cell that moved.
    reach = _reportable(spans)
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
        and reading.measured
        and reach[0] <= centre <= reach[1]
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
