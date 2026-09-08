"""Ring the cells the classifier named, over the reels it read."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

from app.utils.payline_overlay import scale_for

__all__ = ["DrawnSymbol", "draw"]

# Green for a named tile, amber for one below the floor. Neither is the purple or
# gold these games are drawn in, so a ring never reads as artwork.
_NAMED = (46, 204, 113)
_UNKNOWN = (241, 196, 15)

_RING_WIDTH = 2


@dataclass(frozen=True)
class DrawnSymbol:
    """One cell's verdict, reduced to what the picture actually shows."""

    row: int
    """1-indexed."""
    column: int
    """1-indexed."""
    known: bool


def draw(
    reels: Image.Image,
    symbols: Sequence[DrawnSymbol],
    *,
    rows: int,
    columns: int,
    minimum_width: int = 960,
) -> Image.Image:
    """The reels crop, enlarged, with a ring around every cell's verdict."""
    if rows < 1 or columns < 1:
        return reels.convert("RGB")

    scale = scale_for(reels.width, minimum_width)
    canvas = reels.convert("RGB").resize(
        (reels.width * scale, reels.height * scale), Image.Resampling.LANCZOS
    )
    pen = ImageDraw.Draw(canvas)

    cell_width = canvas.width / columns
    cell_height = canvas.height / rows

    for entry in symbols:
        if not (1 <= entry.row <= rows and 1 <= entry.column <= columns):
            continue
        left = round((entry.column - 1) * cell_width)
        top = round((entry.row - 1) * cell_height)
        right = round(entry.column * cell_width) - 1
        bottom = round(entry.row * cell_height) - 1
        pen.rectangle(
            (left, top, right, bottom),
            outline=_NAMED if entry.known else _UNKNOWN,
            width=_RING_WIDTH,
        )

    return canvas
