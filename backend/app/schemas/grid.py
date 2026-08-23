"""Reel grid payloads. Tiles are row-major with a shared pixel size and a
post-inset ``roi``; ``GridSplitResult``/``GridTile`` report their rectangles
in different reference frames (frame vs. reels crop), so are never confused."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.roi import RoiFrame

# Not a request field: reel_bounds is singular in a game config.
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
        description="Pixel box [left, top, right, bottom] inside the reels crop.",
    )
    width: int = Field(ge=1, description="Tile width in pixels; see 'tile_width'.")
    height: int = Field(ge=1, description="Tile height in pixels; see 'tile_height'.")
    image_data: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the tile, ready for an <img> src. Null when the "
            "request asked for files only."
        ),
    )


class GridLayout(BaseModel):
    """What the panel needs before anything is split; a game with no reel
    grid reports as a state (``error`` set) rather than a failure."""

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
            "Return the crop and tiles as data URIs too. Files are written "
            "either way; set false to skip the payload."
        ),
    )
    inset: float | list[float] | None = Field(
        default=None,
        description=(
            "Override the config's border trim for this split: one number "
            "trims every edge, two are [horizontal, vertical], four are "
            "[left, top, right, bottom], each a fraction of the tile."
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
            "game filled; the whole frame when there was no letterboxing."
        ),
    )
    letterboxed: bool = Field(
        description="Whether any of the frame was letterbox rather than game.",
    )
    width: int = Field(ge=1, description="Reels crop width in pixels.")
    height: int = Field(ge=1, description="Reels crop height in pixels.")
    rows: int = Field(ge=1, description="Symbol positions per reel.")
    columns: int = Field(ge=1, description="Reels across.")
    inset: list[float] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Border trim applied to every tile, as [left, top, right, bottom] "
            "fractions of the tile. All zeros when nothing was trimmed."
        ),
    )
    tile_width: int = Field(ge=1, description="Width in pixels; shared by every tile.")
    tile_height: int = Field(
        ge=1, description="Height in pixels; shared by every tile."
    )
    output_dir: str = Field(description="Absolute directory the split was written to.")
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
