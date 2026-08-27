"""Ring the cells the classifier named, over the reels it read.

Deliberately no text. The codes and confidences are already on the response and
rendered as a table beside this picture, so drawing them here too would duplicate
data at the one size where it is least readable -- over busy artwork, at a tile's
own scale. What the picture is uniquely good for is *where*: a green ring where a
tile was named and an amber one where nothing cleared the confidence floor makes
a transposed matrix or a mislabelled reel obvious at a glance, which is the same
reason the grid writes ``reels.png`` and the payline check writes its overlay.

The two colours are drawn differently on purpose -- a tile below the floor is a
*reported* outcome, not a gap.

Cells are laid out by dividing the crop evenly, not by asking the game config for
the reel bounds. The classifier deliberately does not depend on that config, and
an even division puts a ring on the right cell regardless of the inset the tiles
were trimmed by.
"""

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
    """One cell's verdict, reduced to what the picture actually shows.

    Only whether it was named, and where. The symbol and its confidence are not
    here because they are not drawn -- carrying them would be a field that looks
    like it matters and does not.
    """

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
