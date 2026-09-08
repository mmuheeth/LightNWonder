"""Splits the reels of a captured frame into a matrix of tiles."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.config.runtime import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    BadRequestError,
    GameConfigInvalidError,
    GridNotConfiguredError,
    GridSplitFailedError,
    PaylineSourceNotFoundError,
    RoiExtractFailedError,
)
from app.schemas.grid import (
    REELS_REGION,
    GridLayout,
    GridSplitRequest,
    GridSplitResult,
    GridTile,
)
from app.services import roi as roi_service
from app.utils import image_roi, paths, reel_grid

logger = get_logger("grid")

# Where a split lands, below the capture directory. One directory per source
# frame beneath it.
_OUTPUT_DIR = "grid"

# The whole reels crop, kept beside its tiles: the tiles are only meaningful
# against the picture they were cut out of.
_CROP_FILE = f"{REELS_REGION}.png"

# The tiles' own subdirectory, so the crop above is not one file among sixteen.
_TILES_DIR = "tiles"

# What a tile file is called. Used to clear stale tiles without touching
# anything else that may be in the directory, and to pick them out again when a
# split is read back.
_TILE_NAME = re.compile(r"^r\d+c\d+\.png$", re.IGNORECASE)

# The same name without its suffix, with the numbers captured: reading a split
# back has to get the matrix out of the filenames, since they are the only record
# of the shape that was split.
_TILE_POSITION = re.compile(r"r(\d+)c(\d+)", re.IGNORECASE)


# --- the active game ------------------------------------------------------


def _active_config() -> tuple[str, GameConfig]:
    """Load the selected game's config, or explain why it cannot be used."""
    name = settings.ideck_active_game
    path = settings.ideck_game_config_path_for(name)
    try:
        return name, load_game_config(path)
    except GameConfigError as exc:
        raise GameConfigInvalidError(
            f"Could not load game config {path}: {exc}"
        ) from exc


def _reels_region(config: GameConfig) -> image_roi.Roi:
    """The reels region of the active game."""
    if REELS_REGION not in config.roi:
        known = ", ".join(sorted(config.roi)) or "none"
        raise GridNotConfiguredError(
            f"The game config for {config.name!r} declares no "
            f"'roi.{REELS_REGION}' region to split (configured regions: {known})"
        )
    try:
        return image_roi.named_roi(config.roi, REELS_REGION)
    except image_roi.RoiError as exc:
        raise GameConfigInvalidError(f"{config.path}: {exc}") from exc


def _grid(config: GameConfig) -> reel_grid.ReelGrid:
    """The reel grid of the active game."""
    if not config.reel_bounds:
        raise GridNotConfiguredError(
            f"The game config for {config.name!r} declares no 'reel_bounds' "
            f"block, so its '{REELS_REGION}' region cannot be split into tiles"
        )
    try:
        return reel_grid.ReelGrid.from_mapping(config.reel_bounds)
    except reel_grid.ReelGridError as exc:
        raise GameConfigInvalidError(f"{config.path}: {exc}") from exc


def _override_inset(
    grid: reel_grid.ReelGrid, requested: float | list[float] | None
) -> reel_grid.ReelGrid:
    """Apply a request's own border trim, if it asked for one — a 400, not the
    config's 500, since this is something the caller can fix by asking differently."""
    if requested is None:
        return grid
    try:
        return grid.with_inset(reel_grid.Inset.from_value(requested, where="inset"))
    except reel_grid.ReelGridError as exc:
        raise BadRequestError(str(exc)) from exc


def _as_list(inset: reel_grid.Inset) -> list[float]:
    """The trim as the API reports it, in the same order as an ``roi``."""
    return [inset.left, inset.top, inset.right, inset.bottom]


# --- writing --------------------------------------------------------------


def _output_dir(source: Path) -> Path:
    """The directory this frame's split is written to — named after the frame,
    so re-splitting one screenshot replaces its own record."""
    return settings.obs_capture_dir / _OUTPUT_DIR / source.stem


def _write(image: Image.Image, destination: Path) -> None:
    """Write one PNG, or say which file could not be written."""
    try:
        image.save(destination, format="PNG")
    except OSError as exc:
        raise GridSplitFailedError(
            f"{destination} could not be written: {exc}"
        ) from exc


def _encode(image: Image.Image) -> str:
    """One picture as a PNG data URI, translating ROI's error into this feature's own."""
    try:
        return roi_service.encode_png(image)
    except RoiExtractFailedError as exc:
        raise GridSplitFailedError(str(exc)) from exc


def _clear_stale_tiles(directory: Path, keep: set[str]) -> None:
    """Remove tiles of a previous, differently-shaped split, without touching
    anything else that may be in the directory."""
    if not directory.is_dir():
        return
    for path in directory.iterdir():
        if path.is_file() and _TILE_NAME.match(path.name) and path.name not in keep:
            try:
                path.unlink()
            except OSError:
                logger.warning("Could not remove the stale tile %s", path)


def _prepare(source: Path, tiles: list[reel_grid.PlacedTile]) -> tuple[Path, Path]:
    """Make the output directories, and clear any stale tiles from them."""
    directory = _output_dir(source)
    tiles_dir = directory / _TILES_DIR
    try:
        tiles_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GridSplitFailedError(f"{tiles_dir} could not be created: {exc}") from exc
    _clear_stale_tiles(tiles_dir, {tile.file_name for tile in tiles})
    return directory, tiles_dir


# --- reading a split back -------------------------------------------------
# This module named the directory, the crop and the tiles, so it also owns
# reading a split back rather than a second module re-deriving those names.


@dataclass(frozen=True)
class SplitOnDisk:
    """One written split, read back off disk. Shape comes from the tile filenames,
    not the game config — this describes what *was* split."""

    directory: Path
    """The split's own directory, named after the frame it came from."""

    name: str
    """Its directory name, which is the source frame's stem."""

    written_at: datetime
    """When the crop was written, which is when the split was made."""

    crop: Image.Image
    """The whole reels crop -- the picture the tiles were cut out of."""

    tiles: Mapping[str, Image.Image]
    """Every tile, keyed by its position name (``r1c1``)."""

    rows: int
    columns: int
    tile_width: int
    tile_height: int


def _split_root() -> Path:
    """Directory every split is written below."""
    return settings.obs_capture_dir / _OUTPUT_DIR


def latest_split() -> Path | None:
    """Newest split directory, by mtime (not directory name), or None."""
    root = _split_root()
    if not root.is_dir():
        return None
    directories = [path for path in root.iterdir() if path.is_dir()]
    if not directories:
        return None
    return max(directories, key=lambda path: path.stat().st_mtime)


def resolve_split(name: str | None) -> Path:
    """The split to read: the one that was named, or the newest one."""
    if name is not None:
        try:
            directory = paths.resolve_within(_split_root(), name)
        except paths.UnsafeNameError as exc:
            raise BadRequestError(
                f"split is not a usable split name: {exc.reason}"
            ) from exc
        if not directory.is_dir():
            raise PaylineSourceNotFoundError(
                f"No split named {name!r} is in {_split_root()}"
            )
        return directory

    latest = latest_split()
    if latest is None:
        raise PaylineSourceNotFoundError(
            "No reel grid has been split yet. Split one from the Reel grid "
            f"panel; they are written to {_split_root()}"
        )
    return latest


def _split_shape(names: list[str]) -> tuple[int, int]:
    """The matrix a set of tile filenames describes; every position in the
    rows x columns rectangle must be present, or a hole reads as a complete grid."""
    positions = [
        (int(match.group(1)), int(match.group(2)))
        for match in (_TILE_POSITION.fullmatch(name) for name in names)
        if match is not None
    ]
    if not positions:
        raise PaylineSourceNotFoundError("the split holds no tiles")

    rows = max(row for row, _ in positions)
    columns = max(column for _, column in positions)
    missing = {
        reel_grid.position_name(row, column)
        for row in range(1, rows + 1)
        for column in range(1, columns + 1)
    } - set(names)
    if missing:
        raise PaylineSourceNotFoundError(
            f"the split is missing {len(missing)} of its {rows * columns} tiles "
            f"({', '.join(sorted(missing)[:5])})"
        )
    return rows, columns


def read_split(directory: Path) -> SplitOnDisk:
    """Open one written split: its crop, its tiles, and the shape they make."""
    crop_path = directory / _CROP_FILE
    tiles_dir = directory / _TILES_DIR
    if not crop_path.is_file():
        raise PaylineSourceNotFoundError(
            f"{directory.name} holds no {_CROP_FILE}, so it is not a split"
        )
    if not tiles_dir.is_dir():
        raise PaylineSourceNotFoundError(
            f"{directory.name} holds no '{_TILES_DIR}' directory of tiles"
        )

    files = sorted(path for path in tiles_dir.iterdir() if _TILE_NAME.match(path.name))
    # Lowercased, because the position names every caller asks with come from
    # `reel_grid.position_name` and a hand-renamed `R1C1.png` is still that tile.
    try:
        rows, columns = _split_shape([path.stem.lower() for path in files])
    except PaylineSourceNotFoundError as exc:
        raise PaylineSourceNotFoundError(f"{directory.name}: {exc.message}") from exc

    crop = roi_service.open_frame(crop_path)
    tiles = {path.stem.lower(): roi_service.open_frame(path) for path in files}
    sizes = {tile.size for tile in tiles.values()}
    if len(sizes) != 1:
        raise GridSplitFailedError(
            f"{directory.name} holds tiles of {len(sizes)} different sizes "
            f"({', '.join(f'{w}x{h}' for w, h in sorted(sizes))}), so they are "
            "not one split -- split the frame again"
        )
    width, height = sizes.pop()

    return SplitOnDisk(
        directory=directory,
        name=directory.name,
        written_at=datetime.fromtimestamp(crop_path.stat().st_mtime, tz=UTC),
        crop=crop,
        tiles=tiles,
        rows=rows,
        columns=columns,
        tile_width=width,
        tile_height=height,
    )


# --- placing the grid on a frame that was never written -------------------
# The split above is the written record of one screenshot. This is the same
# geometry answered for a frame that is only ever in memory -- the frames of a
# per-tile clip, which arrive from OBS several times a second and are cut up and
# thrown away. It lives here rather than in the caller because *where a tile is*
# is this module's question, and answering it twice is how the clips and the
# split would come to disagree about which pixels are r1c1.


@dataclass(frozen=True)
class FramePlacement:
    """Where the reels and their tiles land on one frame, in that frame's pixels."""

    game: str
    box: tuple[int, int, int, int]
    letterboxed: bool
    rows: int
    columns: int
    tile_width: int
    tile_height: int
    tiles: list[reel_grid.PlacedTile]


def place_on(image: Image.Image) -> FramePlacement:
    """Resolve the active game's reel grid onto one open frame."""
    name, config = _active_config()
    region = _reels_region(config)
    grid = _grid(config)
    try:
        box, content = roi_service.resolve_box(region, image)
    except image_roi.RoiError as exc:
        raise GameConfigInvalidError(f"{config.path}: {exc}") from exc

    width, height = box[2] - box[0], box[3] - box[1]
    try:
        tiles = grid.place(width, height)
    except reel_grid.ReelGridError as exc:
        raise GridSplitFailedError(
            f"a {image.width}x{image.height} frame left no room to place tiles: {exc}"
        ) from exc
    tile_width, tile_height = grid.tile_size(width, height)
    return FramePlacement(
        game=name,
        box=box,
        letterboxed=content.letterboxed,
        rows=grid.row_count,
        columns=grid.column_count,
        tile_width=tile_width,
        tile_height=tile_height,
        tiles=tiles,
    )


# --- public API -----------------------------------------------------------


def _layout() -> GridLayout:
    """Blocking half of :func:`layout`."""
    name, config = _active_config()
    latest = roi_service.latest_path()
    frame = roi_service.describe_path(latest) if latest is not None else None

    try:
        region = _reels_region(config)
        grid = _grid(config)
    except (GridNotConfiguredError, GameConfigInvalidError) as exc:
        # Reported rather than raised: an unconfigured game isn't a broken panel.
        return GridLayout(game=name, latest_frame=frame, error=exc.message)

    return GridLayout(
        game=name,
        roi=[region.left, region.top, region.right, region.bottom],
        rows=grid.row_count,
        columns=grid.column_count,
        positions=grid.positions(),
        inset=_as_list(grid.inset),
        latest_frame=frame,
    )


async def layout() -> GridLayout:
    """The active game's grid, and the frame a split would use. A game with no
    grid comes back with ``error`` set rather than as a failed request."""
    return await asyncio.to_thread(_layout)


def _split(request: GridSplitRequest) -> GridSplitResult:
    """Blocking half of :func:`split`."""
    name, config = _active_config()
    region = _reels_region(config)
    grid = _override_inset(_grid(config), request.inset)

    path = roi_service.resolve_frame(request.file_name)
    frame = roi_service.open_frame(path)
    try:
        box, content = roi_service.resolve_box(region, frame)
    except image_roi.RoiError as exc:
        raise GameConfigInvalidError(f"{config.path}: {exc}") from exc
    crop = frame.crop(box)

    try:
        tiles = grid.place(crop.width, crop.height)
    except reel_grid.ReelGridError as exc:
        raise GridSplitFailedError(
            f"{path.name} left no room to place tiles: {exc}"
        ) from exc
    tile_width, tile_height = grid.tile_size(crop.width, crop.height)

    directory, tiles_dir = _prepare(path, tiles)
    _write(crop, directory / _CROP_FILE)

    reported: list[GridTile] = []
    for tile in tiles:
        image = crop.crop(tile.box)
        _write(image, tiles_dir / tile.file_name)
        reported.append(
            GridTile(
                row=tile.row,
                column=tile.column,
                name=tile.name,
                file_name=tile.file_name,
                roi=[tile.roi.left, tile.roi.top, tile.roi.right, tile.roi.bottom],
                box=list(tile.box),
                width=image.width,
                height=image.height,
                image_data=_encode(image) if request.include_images else None,
            )
        )

    logger.info(
        "Split roi.%s of %s from %s (content box %s) into %dx%d tiles of "
        "%dx%d px under %s",
        REELS_REGION,
        name,
        path.name,
        content.box,
        grid.row_count,
        grid.column_count,
        tile_width,
        tile_height,
        directory,
    )
    return GridSplitResult(
        game=name,
        region=REELS_REGION,
        roi=[region.left, region.top, region.right, region.bottom],
        source=roi_service.describe(path, frame),
        box=list(box),
        content_box=list(content.box),
        letterboxed=content.letterboxed,
        width=crop.width,
        height=crop.height,
        rows=grid.row_count,
        columns=grid.column_count,
        inset=_as_list(grid.inset),
        tile_width=tile_width,
        tile_height=tile_height,
        output_dir=str(directory),
        crop_file=_CROP_FILE,
        crop_image=_encode(crop) if request.include_images else None,
        positions=grid.positions(),
        tiles=reported,
    )


async def split(request: GridSplitRequest) -> GridSplitResult:
    """Cut the reels out of one frame, divide them into tiles, and write both."""
    return await asyncio.to_thread(_split, request)
