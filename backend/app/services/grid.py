"""Splitting the reels of a captured frame into a matrix of tiles.

Every piece this joins up already existed. :mod:`app.services.roi` knows which
screenshot is meant by "the latest one" and how to open it,
:mod:`app.utils.image_roi` cuts a region out of a frame at whatever resolution
the frame turned out to be, and :mod:`app.utils.reel_grid` reads the game
config's ``reel_bounds`` block. What is left here is the part that is about this
project: that a tile is a region of the *reels crop* and not of the frame, and
that the split is written down.

This is one step past :mod:`app.services.roi`, the way OCR is one step past it in
the other direction. ROI answers *is this rectangle aimed at the right part of
the screen*; this answers *and does it divide into the symbols I expect*. Two
crops, in sequence, and the second set of fractions is measured against the
first crop -- so a reel window that moved on screen is a change to ``roi.reels``
alone, and nothing about the tiles needs re-measuring.

**The frame comes off disk, newest first, and the reels are found inside the
game rather than inside the canvas.** Both are delegated whole to the ROI
service, which is where those questions are documented. A split with no
``file_name`` uses the shot the dashboard's Screenshot button just took, and
nothing here asks OBS for a frame -- the same frame split twice has to give the
same tiles. ``roi.reels`` resolves against
:func:`app.services.roi.content_box`, so resizing the simulator moves the reels
crop with it and neither block needs re-measuring.

**The split is written down, unlike an ROI extraction.** Fifteen tiles are not
something to read off a screen and discard: they are the input to whatever looks
at symbols next. They go to ``<capture dir>/grid/<frame stem>/``, one directory
per source screenshot, so splitting two frames leaves two records rather than
one overwritten one -- and splitting the *same* frame twice is idempotent, which
is what makes it safe to re-run after editing the bounds.

**Every tile is the same number of pixels.** Rounding each tile's edges on its
own is right for a single region and wrong for a grid: reel spans of 76.6 pixels
round to 74, 74, 73, 74, 74 depending only on where each boundary falls, and
tiles that differ by a pixel cannot be stacked or fed to anything expecting one
input size. :meth:`app.utils.reel_grid.ReelGrid.place` keeps each tile's own
rounded position and gives them all one shared size, so nothing drifts off the
symbol it was aimed at.

**The border trim is part of the tile, not a step after it.** The bounds divide
the crop edge to edge, so a tile takes everything between its neighbours --
including the frame the game draws inside a reel to highlight a win, which lies
across the symbol's own edge rather than in a gap the bounds could have skipped.
``reel_bounds.inset`` shrinks every tile towards its centre, and a request may
override it for one split: that is how the number gets found before it is written
into the config, the same loop OCR's per-request options serve. What a config
declares is a 500 if it is unusable and what a request asks for is a 400.

**Stale tiles are cleared, and only tiles.** Re-splitting a frame after adding a
sixth reel to the config would otherwise leave the old ``r1c5`` behind, looking
exactly like part of the current answer. Only files matching a tile's own name
pattern are removed, so nothing else that happens to be in the directory is.

Like :mod:`app.services.roi` this service holds no state -- no client, no lock,
no cached engine -- so it has no ``reset()`` and ``tests/conftest.py`` has
nothing to clean up between tests. Callers use the namespace::

    from app.services import grid as grid_service
    await grid_service.split(GridSplitRequest())
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.core.config import settings
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
    """The reels region of the active game.

    Raises:
        GridNotConfiguredError: if the game declares no ``roi.reels``.
        GameConfigInvalidError: if it declares one with unusable numbers.
    """
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
    """The reel grid of the active game.

    Raises:
        GridNotConfiguredError: if the game declares no ``reel_bounds``.
        GameConfigInvalidError: if it declares one that cannot be read.
    """
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
    """Apply a request's own border trim, if it asked for one.

    A 400 rather than the 500 the config's own inset would raise: the same
    numbers are wrong in a different place, and only one of the two is something
    the caller can fix by asking differently. Tuning the trim against a frame
    that does not move and then writing the winner into the config is the loop
    this exists for, the same one OCR's per-request options serve.
    """
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
    """The directory this frame's split is written to.

    Named after the frame rather than the moment, so re-splitting one screenshot
    replaces its own record instead of adding another one beside it. The stem
    comes from a filename that has already been through
    :func:`app.utils.paths.resolve_within`, so it carries no separators.
    """
    return settings.obs_capture_dir / _OUTPUT_DIR / source.stem


def _write(image: Image.Image, destination: Path) -> None:
    """Write one PNG, or say which file could not be written.

    Raises:
        GridSplitFailedError: if the file could not be written.
    """
    try:
        image.save(destination, format="PNG")
    except OSError as exc:
        raise GridSplitFailedError(
            f"{destination} could not be written: {exc}"
        ) from exc


def _encode(image: Image.Image) -> str:
    """One picture as a PNG data URI, as this feature's own failure.

    :func:`app.services.roi.encode_png` raises ROI's error, which is right
    for ROI and would read as the wrong feature here. Translated rather than
    reimplemented: the encoding itself is one reading of "PNG regardless of
    the source format", and it belongs in one place.

    Raises:
        GridSplitFailedError: if the picture could not be encoded.
    """
    try:
        return roi_service.encode_png(image)
    except RoiExtractFailedError as exc:
        raise GridSplitFailedError(str(exc)) from exc


def _clear_stale_tiles(directory: Path, keep: set[str]) -> None:
    """Remove tiles of a previous, differently-shaped split.

    Scoped to names that match a tile's own pattern and are not part of this
    split, so a directory someone put something else in keeps it.
    """
    if not directory.is_dir():
        return
    for path in directory.iterdir():
        if path.is_file() and _TILE_NAME.match(path.name) and path.name not in keep:
            try:
                path.unlink()
            except OSError:
                # Not worth failing a split that otherwise worked; the stale
                # tile is visible in the response's absence of it.
                logger.warning("Could not remove the stale tile %s", path)


def _prepare(source: Path, tiles: list[reel_grid.PlacedTile]) -> tuple[Path, Path]:
    """Make the output directories, and clear any stale tiles from them.

    Returns:
        The split's own directory, and its ``tiles`` subdirectory.

    Raises:
        GridSplitFailedError: if the directories could not be created.
    """
    directory = _output_dir(source)
    tiles_dir = directory / _TILES_DIR
    try:
        tiles_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GridSplitFailedError(f"{tiles_dir} could not be created: {exc}") from exc
    _clear_stale_tiles(tiles_dir, {tile.file_name for tile in tiles})
    return directory, tiles_dir


# --- reading a split back -------------------------------------------------
# The written split is the deliverable, so reading one back belongs here rather
# than in whatever reads it next -- this module is what named the directory, the
# crop and the tiles, and a second module spelling those three the same way is a
# second module to change when one of them moves. The same reason
# :mod:`app.services.roi` owns "the latest screenshot" for the grid and for OCR.


@dataclass(frozen=True)
class SplitOnDisk:
    """One written split, read back off disk.

    The shape comes from the tile filenames rather than from the game config on
    purpose: this describes what *was* split, which is the thing a later check is
    actually looking at, and whether that still matches what the game declares is
    the caller's question to ask.
    """

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
    """Newest split directory, or None if nothing has been split yet.

    Modification time, like :func:`app.services.roi.latest_path` and for the same
    reason: the directory names carry the frame's timestamp today, and sorting
    them as strings is a bug waiting for the day they do not.
    """
    root = _split_root()
    if not root.is_dir():
        return None
    directories = [path for path in root.iterdir() if path.is_dir()]
    if not directories:
        return None
    return max(directories, key=lambda path: path.stat().st_mtime)


def resolve_split(name: str | None) -> Path:
    """The split to read: the one that was named, or the newest one.

    Raises:
        BadRequestError: if a named split is not a bare directory name.
        PaylineSourceNotFoundError: if the named split, or any split at all, is
            not there.
    """
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
    """The matrix a set of tile filenames describes.

    Rows and columns from the largest of each, and then every position in that
    rectangle has to be present: a split missing ``r2c3`` would otherwise be read
    as a complete 3x5 grid with a hole in it, and a hole in the middle of a
    payline is a line that silently cannot be evaluated.

    Raises:
        PaylineSourceNotFoundError: if the names describe no complete matrix.
    """
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
    """Open one written split: its crop, its tiles, and the shape they make.

    Every tile is opened, because every tile is what a comparison needs -- and
    because a split with an unreadable tile in it is worth failing on rather than
    silently evaluating around.

    Raises:
        PaylineSourceNotFoundError: if the crop or the tiles directory is not
            there, or the tiles do not make a complete matrix.
        RoiExtractFailedError: if the crop or a tile is not a readable image.
        GridSplitFailedError: if two tiles are different sizes, which means they
            did not come from one split.
    """
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
        # Reported rather than raised: half the shipped games have no reels, and
        # a panel that shows an error card for choosing one of them says the
        # dashboard is broken when it is only unconfigured.
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
    """The active game's grid, and the frame a split would use.

    What the panel renders before anything is split: the shape of the matrix and
    the name of the shot it would come out of. A game that describes no grid
    comes back with ``error`` set rather than as a failed request.

    Raises:
        GameConfigInvalidError: if the active game's config cannot be loaded.
        RoiExtractFailedError: if the newest screenshot is not a readable image.
    """
    return await asyncio.to_thread(_layout)


def _split(request: GridSplitRequest) -> GridSplitResult:
    """Blocking half of :func:`split`."""
    name, config = _active_config()
    region = _reels_region(config)
    grid = _override_inset(_grid(config), request.inset)

    path = roi_service.resolve_frame(request.file_name)
    frame = roi_service.open_frame(path)
    try:
        # Against the game rather than the canvas, the same way an ROI
        # extraction resolves: the bars round a window capture change width when
        # the window is resized, and the reels are inside them.
        box, content = roi_service.resolve_box(region, frame)
    except image_roi.RoiError as exc:
        raise GameConfigInvalidError(f"{config.path}: {exc}") from exc
    crop = frame.crop(box)

    # Placed rather than resolved one at a time: the tiles' fractions are of the
    # crop, and `place` is what turns them into boxes that all share one pixel
    # size instead of rounding each edge on its own.
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
    """Cut the reels out of one frame, divide them into tiles, and write both.

    Raises:
        BadRequestError: if a named frame is not a bare filename, or the
            request's own ``inset`` is unusable.
        GridNotConfiguredError: if the active game declares no ``roi.reels`` or
            no ``reel_bounds``.
        GameConfigInvalidError: if the config cannot be loaded, or either block
            is declared with unusable numbers.
        RoiFrameNotFoundError: if the frame to split is not on disk.
        RoiExtractFailedError: if the frame is not a readable image.
        GridSplitFailedError: if the crop or a tile could not be written, or
            could not be encoded for the response.
    """
    return await asyncio.to_thread(_split, request)
