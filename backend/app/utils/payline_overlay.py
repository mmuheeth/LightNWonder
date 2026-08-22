"""Draw evaluated paylines over the reels they were read from.

The numbers a payline check produces -- line 3 pays 4, at these similarity
scores -- are only trustworthy next to the picture they came from. A line that
"pays 4" because two adjacent pots really are two pots and a line that pays 4
because the threshold is too low read identically as a number and not at all
identically as a drawing over the reels.

**One colour per line, and the colour is this module's to decide.** The panel
shows a swatch beside every line's row and the overlay draws the same line in the
same colour, so the two must agree; the only way to guarantee that is for one
place to own the palette and for the answer to travel with the result. Hence
:func:`colour`, keyed by the line's position in its set so it is stable across
runs of the same set.

**The palette avoids purple and deep red on purpose.** These games are drawn in
purple, gold and red -- a violet payline over a violet reel is an invisible
payline. The twelve colours here are bright, cool-to-warm, and none of them is
the background. Past twelve lines they cycle -- a forty-line set drawn all at
once is busy whatever the palette does, and nothing here tries to fix that with
text; the picture is the path, not a legend.

**Two pictures, two jobs, and ``detailed`` is what tells them apart.** The
combined overlay of every paying line is an overview -- just the paths, in
their own colours, nothing else -- because it is already several lines at once
and a tile border on each would be several things competing for one glance. A
line's own picture is the opposite: one line, so there is room to show *why* it
paid what it paid -- a green border on every tile the run confirmed, red on the
one that ended it. Nothing past a break is bordered: once a line has broken,
what follows is not part of the answer, and a border around it would only be
one more thing to parse for no new information. ``DrawnLine.detailed`` is that
switch, and it is the caller's to set: the combined overlay is built from the
exact same ``DrawnLine`` objects as the per-line pictures, just with
``detailed=False``.

**The reels crop is enlarged before anything is drawn on it.** A split of a
1280x720 capture leaves reels around 455 pixels wide, and a stroke needs that
enlargement to stay legible rather than dissolving into the artwork under it.

Nothing here knows what a similarity score is or why a line stopped where it
did. It takes pictures, points and boxes -- no text, on either picture.
"""

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

# Twelve bright colours, none of them the purple or the gold these games are
# drawn in. Ordered so that neighbouring lines -- which is what a run of paying
# lines usually is -- land far apart in hue.
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

# Mixed into the unmatched tail's stroke. Not used for anything drawn under a
# stroke or label any more, since neither carries a dark backing now.
_OUTLINE = (12, 8, 20)

# The two semantic tile-border colours, neither a member of PALETTE so neither
# is ever mistaken for a line's own colour: green borders a tile the run
# confirmed, red borders the one tile that ended it. Nothing past a break gets
# a border at all -- once a line has broken, what comes after is not part of
# the answer. A line's own colour still tells lines apart in the stroke; these
# two read the same on every line, on purpose.
_PASS_OUTLINE = (46, 204, 113)
_BREAK_OUTLINE = (231, 76, 60)

# How much of a line's own colour survives into its unmatched tail's stroke.
# Enough to tell four tails apart, little enough that the tail never reads as
# the confirmed run.
_TRAIL_WEIGHT = 0.45

# The largest a crop is enlarged by. Six times a 455-pixel crop is already a
# 2730-pixel picture, and a data URI of one is a megabyte of response.
_MAX_SCALE = 6

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class DrawnLine:
    """One line to draw: its path, its tiles, and how much detail to show."""

    colour: str
    """Hex colour, as :func:`colour` hands it out."""

    points: Sequence[tuple[int, int]]
    """Tile centres of the confirmed run, in the *unenlarged* crop's pixels,
    left to right. Empty when nothing was confirmed -- a line whose very first
    pair failed has no points, only a :attr:`trail`. This is the one field the
    combined overlay reads: with ``detailed=False`` it is the whole picture,
    a plain stroke through these points and nothing else.

    Unenlarged because the caller measures them against the crop it split, and
    making it also know the enlargement factor would put the same multiplication
    in two places.
    """

    trail: Sequence[tuple[int, int]] = ()
    """Centres the line runs through but did not confirm, continuing from the
    last of :attr:`points` when there is one. Ignored unless :attr:`detailed`."""

    matched_boxes: Sequence[Box] = ()
    """Pixel box of every tile in :attr:`points`, bordered green. Ignored
    unless :attr:`detailed`."""

    break_box: Box | None = None
    """Pixel box of the tile that ended the run, bordered red, or None when
    the whole line paid. Ignored unless :attr:`detailed`. Nothing past this
    tile is bordered -- once a line has broken, what follows is not part of
    the answer."""

    detailed: bool = True
    """False draws only the plain stroke through :attr:`points` -- no tile
    borders, no break. Set to False for the combined overlay, which is several
    lines at once and has no room for several things to look at per line; a
    line's own picture leaves this True."""


def colour(index: int) -> str:
    """The colour of the line at this position in its set, 0-indexed."""
    return PALETTE[index % len(PALETTE)]


def _dim(hex_colour: str, weight: float) -> tuple[int, int, int]:
    """One palette colour mixed towards the outline, for the unmatched tail.

    The tail has to read as *the same line, not paying* -- which a neutral dark
    stroke does not: on a picture with four lines on it, four identical grey
    tails say nothing about which line each belongs to. Mixed rather than made
    transparent because the drawing is one opaque layer.
    """
    value = hex_colour.lstrip("#")
    channels = (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    return tuple(  # type: ignore[return-value]
        round(channel * weight + base * (1 - weight))
        for channel, base in zip(channels, _OUTLINE, strict=True)
    )


def scale_for(width: int, minimum: int) -> int:
    """How many times to enlarge a crop of this width to annotate it legibly.

    A whole number, so enlarging is a clean pixel multiple and a tile boundary
    stays a tile boundary. At least 1: a capture already wider than the minimum
    is drawn on as it is rather than shrunk.
    """
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
    """A rectangle traced exactly around one tile, at this picture's scale.

    No perpendicular offset the way overlapping strokes get one: a border only
    ever appears on a line's own picture, which draws one line at a time, so
    there is nothing for it to collide with.
    """
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
    """The reels crop, enlarged, with every line drawn over it.

    Lines are offset from each other by a few pixels perpendicular to the reels,
    because the straight lines of a five-line set share a row with nothing but
    each other and two exactly coincident strokes are one stroke. The offset is
    small next to a tile, so a stroke still reads as being on its own symbols.

    An empty ``lines`` gives back the enlarged crop unmarked, which is the
    picture for "nothing paid" and is more use than no picture at all.
    """
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
