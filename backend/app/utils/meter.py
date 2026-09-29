"""Geometry shared by both readers of a game's meter strip (``roi.cash_meter``:
balance, last win, current bet) -- row/column detection, cell cropping and
field assignment are properties of how the game draws the strip, not of the
engine reading it. :mod:`app.utils.meter_paddle` is the only reader; this
module used to hold a Tesseract one too, replaced when PaddleOCR became the
project's sole OCR engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
from PIL import Image, ImageOps

__all__ = [
    "DEFAULT_WINDOWS",
    "Band",
    "MeterError",
    "MeterField",
    "MeterScan",
    "Unmapped",
    "assign_fields",
    "cell_crop",
    "reportable_span",
    "row_bands",
]


class MeterError(ValueError):
    """The strip is unusable -- no area, or nothing bright enough to read."""


# Brightness (as a fraction of the strip's own max) for a column to count as a
# glyph. Swept over saved strips: 0.62 swallows cell borders into the group; 0.82
# hugs the glyphs cleanly.
INK_LEVEL = 0.82

# Slack columns around a located group -- enough to not shave a glyph's dim edge,
# few enough to keep the cell border out.
CLUSTER_PAD = 2

# Clear background a glyph needs above/below, as a share of band height -- not
# cosmetic: a glyph flush against its crop's edge reads as one with an extra
# stroke (`105` came back `4105`). A share, not pixels, so it holds from a
# 0.35x canvas to 2x. Topped up rather than always added, since a band that
# already has room shouldn't be padded further.
GLYPH_MARGIN_SHARE = 0.18

# Never fewer than this, however small the band: two rows is the least that reads
# as background rather than as a border touching the glyph.
MIN_GLYPH_MARGIN = 2

# Brightness for a row to hold part of a glyph, as a share of the crop's range
# above background. Deliberately looser than :data:`INK_LEVEL`, for the
# opposite reason: that one wants glyph *cores* (keeping a border out of a
# column group), this one wants the glyph's dimmest extremity, since any
# stroke the engine can see needs clearance.
GLYPH_EDGE = 0.15

# Share of a row's width lit for it to count as the cell's border (not text) --
# where growth stops: a drawn rule lights nearly every column, glyphs only
# their strokes. Growth lets one fraction hold across resolutions (the same
# band is 29 rows of a 1080x75 strip and 11 of a 421x29 one); a clipped glyph
# can only be fixed by going back to the strip, not by padding.
BORDER_SHARE = 0.8

# Gap (as a share of band height) that splits two lit runs into different numbers.
# Swept against 20 saved strips scoring on wrong (not just missing) values; 0.55
# is tight enough to keep a decimal point from splitting `$1.76`, loose enough to
# keep ~19px cell separations apart.
GAP_SHARE = 0.55

# Floor under that gap, in pixels. Only binds on a small capture: a 459x29
# strip needs 9 or it splits `$1.76` into `$1.` and `76`; a 405x28 one needs 5
# or it welds CASH to WIN. The one constant a share can't rescue -- the gap
# inside a value and between two cells converge as the strip shrinks (9 and 17
# at 1080x75, no daylight by 405x28). Unreliable below about a third of canvas.
MIN_GAP = 9

# Narrower than this and it's a cell corner or artwork speck, not a value.
MIN_GROUP_WIDTH = 10

# A bright core thinner than this is a border highlight, not a line of text.
MIN_CORE_ROWS = 3

# Share of a core's height to grow it by, to reach the glyph's dimmer top/bottom.
# 0.35 reproduces the hand-tuned bands measured on both known skins.
BAND_PAD_SHARE = 0.35

# Fields a candidate band must read before its confidence can end the search. A
# strip has three cells and the middle (WIN) is often empty between spins.
ENOUGH_FIELDS = 2

# Ignore a reading below this. 40.0 was measured against Tesseract's 0-100
# confidence scale (every value verified correct on saved strips scored 79 or
# better); PaddleOCR's own confidence is rescaled onto the same 0-100 range at
# the point of reading (`meter_paddle.read_group`), so the two are at least
# dimensionally comparable, but this floor has not been independently
# re-measured against a Paddle-only split -- re-tune from real readings before
# trusting it as more than a starting point.
FLOOR = 40.0

# A field with no reading at all -- an engine that ran and found nothing, or
# one that could not run -- defaults to this rather than a genuinely low
# score, so :attr:`MeterField.measured` can tell "read as zero confidence"
# apart from "never answered". Ranked below any measured reading and exempt
# from :data:`FLOOR` rather than discarded, since a field with a reading is
# still worth carrying even when nothing scored it.
UNMEASURED = 0.0

# What an unmeasured reading counts as when a caller compares two bands' scans
# (see `MeterScan.score`). The floor deliberately: a band that produced a
# reading nothing scored still beats one that produced nothing at all, and
# still loses to one that read convincingly.
UNMEASURED_SCORE = FLOOR

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

Band = tuple[int, int]
"""Rows of the strip the values sit in, as ``(top, bottom)`` pixels."""


@dataclass(frozen=True)
class MeterField:
    """One number read off the strip."""

    value: Decimal | None = None
    text: str = ""
    """Raw text of the winning read, engine junk included."""

    confidence: float = UNMEASURED
    """0-100 as the engine reports it, or :data:`UNMEASURED` when nothing did.
    Read it through :attr:`measured` before comparing it to a threshold."""

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
        """Compares two readings of one crop: a value beats none, a scored read beats an
        unscored one, then the score decides."""
        return self.value is not None, self.measured, self.confidence


@dataclass(frozen=True)
class Unmapped:
    """A confident number belonging to no field, reported rather than filed under the
    nearest name."""

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
        """Mean confidence of the fields that read, for comparing two bands."""
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
    """Candidate row bands, from the strip's own ink profile."""
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


def _margin(rows: int) -> int:
    """Clear rows a value occupying ``rows`` of them needs around it, from
    :data:`GLYPH_MARGIN_SHARE`."""
    return max(MIN_GLYPH_MARGIN, round(rows * GLYPH_MARGIN_SHARE))


def _grown(image: Image.Image, columns: Band, band: Band) -> Band:
    """``band``, extended over ``columns`` on whichever edge its glyphs run into."""
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
    """``crop`` grown until its glyphs have ``margin`` clear rows above and below, or
    unchanged if they already do -- or if it has no background to grow with."""
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


def cell_crop(image: Image.Image, box: Band, band: Band) -> Image.Image:
    """The picture of one cell, ready to read: ``box`` widened by
    :data:`CLUSTER_PAD`, the band grown onto the glyph edge (:func:`_grown`),
    then any missing clearance synthesised (:func:`_padded`). Public so
    :mod:`app.utils.meter_paddle` shares these corrections instead of
    repeating them and drifting."""
    columns = (max(0, box[0] - CLUSTER_PAD), min(image.width, box[1] + CLUSTER_PAD))
    top, bottom = _grown(image, columns, band)
    return _padded(
        image.crop((columns[0], top, columns[1], bottom)),
        _margin(band[1] - band[0]),
    )


def assign_fields(
    spans: dict[str, tuple[float, float]],
    readings: list[tuple[float, MeterField]],
    *,
    ordinal: bool = False,
) -> tuple[dict[str, MeterField], set[int]]:
    """Files each reading under a field name: by default, the window its centre
    falls in (best-ranked wins when several share one). With ``ordinal``, by
    left-to-right position instead -- first=CASH, last=BET, middle=WIN -- for a
    skin whose label-derived windows can't be trusted but value order can."""
    names = list(spans)
    fields: dict[str, MeterField] = {name: MeterField() for name in names}
    claimed: set[int] = set()
    if not readings:
        return fields, claimed

    if ordinal:
        order = sorted(range(len(readings)), key=lambda i: readings[i][0])
        if names:
            fields[names[0]] = readings[order[0]][1]
            claimed.add(order[0])
        if len(names) > 1 and len(order) > 1:
            fields[names[-1]] = readings[order[-1]][1]
            claimed.add(order[-1])
        middle_names = names[1:-1]
        middle_indices = [index for index in order[1:-1] if index not in claimed]
        if middle_names and middle_indices:
            best = max(middle_indices, key=lambda index: readings[index][1].rank)
            fields[middle_names[0]] = readings[best][1]
            claimed.add(best)
        return fields, claimed

    for name, (low, high) in spans.items():
        best_index: int | None = None
        for index, (centre, reading) in enumerate(readings):
            if not low <= centre <= high:
                continue
            if best_index is None or reading.rank > readings[best_index][1].rank:
                best_index = index
        if best_index is None:
            continue
        fields[name] = readings[best_index][1]
        claimed.add(best_index)
    return fields, claimed


def reportable_span(spans: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """The span of the strip a stray value is worth reporting from. Public for the
    same reason as :func:`cell_crop`: it is a property of the declared windows,
    which the reader files its readings into."""
    return _reportable(spans)


def _reportable(spans: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """The span of the strip a stray value is worth reporting from: the windows' own
    reach, widened by the widest of them at each end."""
    if not spans:
        return 0.0, 1.0
    lows = [low for low, _ in spans.values()]
    highs = [high for _, high in spans.values()]
    slack = max(high - low for low, high in spans.values())
    return max(0.0, min(lows) - slack), min(1.0, max(highs) + slack)
