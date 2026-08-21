"""Reel grid payloads.

The result of a split is a matrix, and the shape of these models is chosen so
that a caller never has to reconstruct it. Every tile carries its own 1-indexed
``row``/``column`` and the ``name`` those make (``r1c1``), the tiles arrive
row-major, and :attr:`GridSplitResult.positions` is the same names already laid
out as rows -- so a consumer can index by position, iterate in reading order, or
render a grid, without chunking anything by hand.

**Every tile is the same pixel size**, and :attr:`GridSplitResult.tile_width`
and :attr:`GridSplitResult.tile_height` say what it is once rather than leaving
it to be inferred from fifteen equal numbers. Each :attr:`GridTile.box` keeps its
own position and takes that shared size.

**Every tile's ``roi`` is post-inset.** A configured border trim is part of what
a tile *is*, not a separate step, so the fractions and the pixel box already have
it applied -- and the effective trim is reported beside them so it is never a
number someone has to go and look up to explain a tile's size.

Two rectangles are in play and they are deliberately reported apart.
:attr:`GridSplitResult.roi` and :attr:`GridSplitResult.box` are the reels
region, in fractions of the game's part of the frame and in pixels of the frame.
:attr:`GridTile.roi` and :attr:`GridTile.box` are fractions and pixels of the
**reels crop**. Mixing the two is the mistake worth making impossible to make
silently, so neither is ever called just "the box".
:attr:`GridSplitResult.content_box` is the third: the part of the frame the game
filled, which is what the reels region's fractions were resolved against.

:class:`app.schemas.roi.RoiFrame` is reused for the source screenshot rather
than restated: it is the same file, found the same way, described by the same
code.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.roi import RoiFrame

# The region a reel grid is split out of. One region, not a request field: the
# ``reel_bounds`` block is singular in a game config, so there is nothing for a
# second name to pair with.
REELS_REGION = "reels"


class GridTile(BaseModel):
    """One symbol position, and where it came from."""

    row: int = Field(ge=1, description="1-indexed row; 1 is the top symbol.")
    column: int = Field(ge=1, description="1-indexed reel; 1 is the leftmost.")
    name: str = Field(description="Matrix position as a name, e.g. 'r1c1'.")
    file_name: str = Field(
        description="What the tile was written as, inside the run's 'tiles' folder."
    )
    roi: list[float] = Field(
        min_length=4,
        max_length=4,
        description=(
            "[left, top, right, bottom] as fractions of the **reels crop**, "
            "from the game config's 'reel_bounds' block with the border inset "
            "already applied."
        ),
    )
    box: list[int] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Pixel box [left, top, right, bottom] inside the reels crop -- not "
            "inside the frame. The position is this tile's own; the size is the "
            "grid's, shared by every tile, so it can sit up to a pixel inside "
            "what 'roi' alone would give at the right and bottom edges."
        ),
    )
    width: int = Field(
        ge=1,
        description=(
            "Tile width in pixels. The same for every tile of the split -- see "
            "'tile_width' on the result."
        ),
    )
    height: int = Field(
        ge=1, description="Tile height in pixels. The same for every tile."
    )
    image_data: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the tile, ready for an <img> src. Null when the "
            "request asked for files only."
        ),
    )


class GridLayout(BaseModel):
    """What the panel needs before anything is split.

    Reports a game that describes no reel grid as a state rather than a failure,
    the same way a malformed ROI region is listed with its reason: only half the
    shipped games have reels configured, and picking one of those is not an
    error to recover from.
    """

    game: str = Field(description="Game the grid belongs to.")
    region: str = Field(
        default=REELS_REGION, description="Region of the frame the grid is cut from."
    )
    roi: list[float] | None = Field(
        default=None,
        min_length=4,
        max_length=4,
        description=(
            "The reels region as fractions of the part of the frame the game "
            "fills, or null when the game does not declare one."
        ),
    )
    rows: int = Field(
        default=0, ge=0, description="Symbol positions per reel; 0 when unconfigured."
    )
    columns: int = Field(
        default=0, ge=0, description="Reels across; 0 when unconfigured."
    )
    positions: list[list[str]] = Field(
        default_factory=list,
        description=(
            "Tile names laid out row-major, e.g. [['r1c1', 'r1c2'], ['r2c1', "
            "'r2c2']]. Empty when the game describes no grid."
        ),
    )
    inset: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0, 0.0],
        min_length=4,
        max_length=4,
        description=(
            "Border trim applied to every tile as [left, top, right, bottom], "
            "each a fraction of the tile itself. All zeros when the game asks "
            "for none."
        ),
    )
    latest_frame: RoiFrame | None = Field(
        default=None,
        description=(
            "Newest screenshot in the screenshots directory, which is what a "
            "split with no 'file_name' will use. Null when none has been taken."
        ),
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why this game cannot be split, when it cannot -- a missing "
            "'roi.reels', a missing 'reel_bounds', or numbers that are unusable. "
            "Null when a split would work."
        ),
    )


class GridSplitRequest(BaseModel):
    """Which frame to split, and whether the pictures come back inline."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {},
                {"file_name": "screenshot-1787192135120.png"},
                {"include_images": False},
                {"inset": 0.03},
                {"inset": [0.04, 0.02]},
            ]
        },
    )

    file_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Screenshot in the screenshots directory to split. Must be a bare "
            "filename. Omit to use the newest one."
        ),
    )
    include_images: bool = Field(
        default=True,
        description=(
            "Return the crop and every tile as data URIs. The files are written "
            "either way; set false when only the files are wanted, which keeps a "
            "5x3 split from carrying sixteen base64 payloads."
        ),
    )
    inset: float | list[float] | None = Field(
        default=None,
        description=(
            "Override the game config's border trim for this one split. One "
            "number trims every edge, two are [horizontal, vertical], four are "
            "[left, top, right, bottom] -- each a fraction of the tile. Omit to "
            "use what the config declares; this is for finding the number to "
            "write into it."
        ),
    )


class GridSplitResult(BaseModel):
    """The split: where it was written, and what each tile is."""

    game: str = Field(description="Game whose grid was split.")
    region: str = Field(description="Region of the frame the grid was cut from.")
    roi: list[float] = Field(
        min_length=4,
        max_length=4,
        description=(
            "The reels region as fractions of the part of the frame the game fills."
        ),
    )
    source: RoiFrame = Field(description="Frame the grid was taken from.")
    box: list[int] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Pixel box [left, top, right, bottom] the reels region resolved to "
            "on this frame."
        ),
    )
    content_box: list[int] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Pixel box [left, top, right, bottom] of the part of the frame the "
            "game filled, which the reels region's fractions were resolved "
            "against. The whole frame when the capture had no letterboxing."
        ),
    )
    letterboxed: bool = Field(
        description=(
            "Whether any of the frame was letterbox rather than game. False "
            "means the reels region resolved exactly as fractions of the canvas."
        ),
    )
    width: int = Field(ge=1, description="Reels crop width in pixels.")
    height: int = Field(ge=1, description="Reels crop height in pixels.")
    rows: int = Field(ge=1, description="Symbol positions per reel.")
    columns: int = Field(ge=1, description="Reels across.")
    inset: list[float] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Border trim that was applied to every tile, as [left, top, right, "
            "bottom] fractions of the tile -- the config's, or the request's "
            "override. All zeros when nothing was trimmed."
        ),
    )
    tile_width: int = Field(
        ge=1,
        description=(
            "Width in pixels of every tile. One number because they are all the "
            "same size: rounding each tile's edges on its own would vary them by "
            "a pixel, which is what stops a grid being stacked or diffed."
        ),
    )
    tile_height: int = Field(
        ge=1, description="Height in pixels of every tile. All tiles share it."
    )
    output_dir: str = Field(
        description=(
            "Absolute directory the split was written to. The crop is at its "
            "root and the tiles are in its 'tiles' subdirectory."
        )
    )
    crop_file: str = Field(
        description="What the whole reels crop was written as, e.g. 'reels.png'."
    )
    crop_image: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the whole reels crop -- what was split. Null "
            "when the request asked for files only."
        ),
    )
    positions: list[list[str]] = Field(
        description="Tile names laid out row-major, mirroring the matrix."
    )
    tiles: list[GridTile] = Field(
        description="Every tile, row-major: the order the matrix is read in."
    )
