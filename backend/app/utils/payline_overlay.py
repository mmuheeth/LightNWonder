"""Draw evaluated paylines over the reels they were read from, and own their palette so
a swatch and a stroke cannot drift."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

__all__ = [
    "PALETTE",
    "DrawnLine",
    "colour",
    "draw",
    "scale_for",
]

# Twelve bright colours, none the purple/gold these games are drawn in, ordered
# so neighbouring lines land far apart in hue.
PALETTE: tuple[str, ...] = (
    "#00E5FF",  # cyan
    "#FF3D71",  # rose
    "#7CFF00",  # lime
    "#FFB300",  # amber
    "#00FFA3",  # spring green
    "#FF6EC7",  # hot pink
    "#4DA3FF",  # sky
    "#FFF056",  # yellow
    "#FF7A45",  # coral
    "#5CFFEA",  # aqua
    "#C6FF00",  # chartreuse
    "#FF4D4D",  # red
)

# Mixed into the unmatched tail's stroke.
_OUTLINE = (12, 8, 20)

# Tile-border colours, deliberately not in PALETTE so neither is mistaken for
# a line's own colour: green confirms a tile, red marks the one that broke it.
_PASS_OUTLINE = (46, 204, 113)
_BREAK_OUTLINE = (231, 76, 60)

# How much of a line's own colour survives into its unmatched tail's stroke --
# enough to tell tails apart, little enough the tail never reads as confirmed.
_TRAIL_WEIGHT = 0.45

# Largest enlargement factor: past this a data URI of the picture gets big.
_MAX_SCALE = 6

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class DrawnLine:
    """One line to draw: its path, its tiles, and how much detail to show."""

    colour: str
    """Hex colour, as :func:`colour` hands it out."""

    points: Sequence[tuple[int, int]]
    """Tile centres of the confirmed run, left to right, in the *unenlarged*
    crop's pixels. Empty when the first pair already failed. The only field
    the combined overlay (``detailed=False``) draws."""

    trail: Sequence[tuple[int, int]] = ()
    """Centres the line runs through but did not confirm. Ignored unless
    :attr:`detailed`."""

    matched_boxes: Sequence[Box] = ()
    """Pixel box of every tile in :attr:`points`, bordered green. Ignored
    unless :attr:`detailed`."""

    break_box: Box | None = None
    """Pixel box of the tile that ended the run, bordered red, or None when
    the whole line paid. Ignored unless :attr:`detailed`."""

    detailed: bool = True
    """False draws only the plain stroke through :attr:`points` -- used for
    the combined overlay; a line's own picture leaves this True."""


def colour(index: int) -> str:
    """The colour of the line at this position in its set, 0-indexed."""
    return PALETTE[index % len(PALETTE)]


def _dim(hex_colour: str, weight: float) -> tuple[int, int, int]:
    """One palette colour mixed towards the outline, for the unmatched tail -- so it
    reads as *this line, not paying* rather than a neutral grey shared by every line."""
    value = hex_colour.lstrip("#")
    channels = (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    return tuple(  # type: ignore[return-value]
        round(channel * weight + base * (1 - weight))
        for channel, base in zip(channels, _OUTLINE, strict=True)
    )


def scale_for(width: int, minimum: int) -> int:
    """How many times to enlarge a crop of this width to annotate it legibly.
    A whole number so a tile boundary stays a tile boundary; at least 1."""
    if width <= 0:
        return 1
    return max(1, min(_MAX_SCALE, math.ceil(minimum / width)))


def _bordered(
    pen: ImageDraw.ImageDraw,
    box: Box,
    *,
    scale: int,
    outline: tuple[int, int, int],
    width: int,
) -> None:
    """A rectangle traced exactly around one tile, at this picture's scale -- no offset
    needed since a border only ever appears on a single-line picture."""
    left, top, right, bottom = box
    pen.rectangle(
        (left * scale, top * scale, right * scale, bottom * scale),
        outline=outline,
        width=width,
    )


def draw(
    reels: Image.Image,
    lines: Sequence[DrawnLine],
    *,
    scale: int = 1,
) -> Image.Image:
    """The reels crop, enlarged, with every line drawn over it."""
    canvas = reels.convert("RGB")
    if scale > 1:
        canvas = canvas.resize(
            (canvas.width * scale, canvas.height * scale), Image.Resampling.LANCZOS
        )
    else:
        canvas = canvas.copy()

    pen = ImageDraw.Draw(canvas)
    # A plain coloured stroke, no dark backing -- a line is legible on its own
    # at this width, and the backing only ever made it look heavier than meant.
    stroke = max(3, round(scale * 1.6))
    border_width = max(3, round(scale * 1.6))
    spread = max(2, scale)

    for index, line in enumerate(lines):
        # Centred on zero, so a single line is drawn exactly on the symbols and
        # a group of them straddles the row rather than drifting off it.
        shift = round((index - (len(lines) - 1) / 2) * spread)
        points = [(x * scale, y * scale + shift) for x, y in line.points]

        if len(points) > 1:
            pen.line(points, fill=line.colour, width=stroke, joint="curve")

        if not line.detailed:
            # The combined overlay's whole picture: just the path. No tile
            # borders, no break -- those are what a line's own picture is for.
            continue

        trail = [(x * scale, y * scale + shift) for x, y in line.trail]
        dimmed = _dim(line.colour, _TRAIL_WEIGHT)
        # Joined onto the end of the confirmed run when there is one, so the
        # tail reads as a continuation of the same line rather than a floating
        # scrap. A line with no confirmed run at all (its very first pair
        # already failed) has no point to join from, so the tail stands alone.
        trail_path = [points[-1], *trail] if points and trail else trail
        if len(trail_path) > 1:
            pen.line(trail_path, fill=dimmed, width=max(2, stroke // 2))

        # Only the tiles that mattered get a border: the confirmed run in
        # green, and the one tile that ended it in red. Nothing past a break is
        # bordered -- once a line has broken, what comes after is not part of
        # the answer.
        for box in line.matched_boxes:
            _bordered(pen, box, scale=scale, outline=_PASS_OUTLINE, width=border_width)
        if line.break_box is not None:
            _bordered(
                pen,
                line.break_box,
                scale=scale,
                outline=_BREAK_OUTLINE,
                width=border_width,
            )

    return canvas
