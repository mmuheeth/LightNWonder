"""Checks a split reel grid against the patterns that pay.

One step past :mod:`app.services.grid`: grid asks *does it divide into the
symbols expected*, this asks *do those symbols line up*. Reads a written split
(via :func:`app.services.grid.resolve_split`/:func:`read_split`), never a
screenshot directly, so a check is repeatable at a new threshold without the
simulator still showing the same spin. Two tiles are "the same" by cosine
similarity (invariant to the brightness pulsing/glowing symbols add — see
:mod:`app.utils.similarity`); the per-game cut between match/no-match clusters
is why every result reports ``matched_min``/``rejected_max`` alongside
``PAYLINE_MATCH_THRESHOLD``. A line reads left to right and stops at the first
non-matching adjacent pair, so ``pays`` is the length of that *leading* run (0,
or 2+) — every pair is still scored (``counted`` marks which ones the run
reached) since the pairs after a break are evidence the break was real. Each
line gets its own picture with a break marker, paying or not; the combined
overlay draws only paying lines and never the break ring, since several lines
share it. Holds no state, so no ``reset()``.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import numpy as np
from PIL import Image

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    GameConfigInvalidError,
    GridNotConfiguredError,
    PaylineCheckFailedError,
    PaylinesNotConfiguredError,
    PaylineSourceNotFoundError,
    PaylineSourceStaleError,
    RoiExtractFailedError,
)
from app.schemas.paylines import (
    PaylineCheckRequest,
    PaylineCheckResult,
    PaylineLayout,
    PaylineLine,
    PaylineSetOption,
    PaylineSource,
    PaylineStats,
    PaylineStep,
)
from app.services import grid as grid_service
from app.services import roi as roi_service
from app.utils import payline_overlay, reel_grid, similarity
from app.utils import paylines as payline_config

logger = get_logger("paylines")

# Beside the tiles it was computed from, since crop/tiles/overlay are one record.
_OUTPUT_DIR = "paylines"

# What pays what is the game's paytable, not this service's business -- so the
# floor for "paying" is 2, and reporting it is left to whoever has the paytable.
_MIN_PAYING = 2


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


def _set_names(config: GameConfig) -> tuple[str, ...]:
    """The bet configurations the game declares."""
    if not config.paylines:
        raise PaylinesNotConfiguredError(
            f"The game config for {config.name!r} declares no 'paylines' block, "
            "so there are no winning patterns to check"
        )
    try:
        return payline_config.set_names(config.paylines)
    except payline_config.PaylineError as exc:
        raise GameConfigInvalidError(f"{config.path}: {exc}") from exc


def _default_set(names: tuple[str, ...]) -> str:
    """Which set a request that names none is asking for: ``PAYLINE_DEFAULT_SET``
    when the game declares it, else the first (already sorted numerically)."""
    wanted = settings.PAYLINE_DEFAULT_SET.strip()
    return wanted if wanted in names else names[0]


def _read_set(config: GameConfig, name: str) -> payline_config.PaylineSet:
    """Parse one bet configuration out of the active game's block."""
    names = _set_names(config)
    if name not in names:
        raise PaylinesNotConfiguredError(
            f"The game config for {config.name!r} declares no {name!r} payline "
            f"set (it declares: {', '.join(names)})"
        )
    try:
        return payline_config.read_set(config.paylines, name)
    except payline_config.PaylineError as exc:
        raise GameConfigInvalidError(f"{config.path}: {exc}") from exc


def _grid(config: GameConfig) -> reel_grid.ReelGrid:
    """The reel grid of the active game, for the tile geometry the lines need —
    read from the config (``inset`` included) rather than counted off filenames,
    since only the config gives a tile's true centre."""
    if not config.reel_bounds:
        raise GridNotConfiguredError(
            f"The game config for {config.name!r} declares no 'reel_bounds' "
            "block, so its paylines cannot be placed on a grid"
        )
    try:
        return reel_grid.ReelGrid.from_mapping(config.reel_bounds)
    except reel_grid.ReelGridError as exc:
        raise GameConfigInvalidError(f"{config.path}: {exc}") from exc


# --- comparing ------------------------------------------------------------


def _vectors(split: grid_service.SplitOnDisk) -> dict[str, np.ndarray]:
    """Every tile as a flat vector, converted once rather than once per comparison."""
    return {name: similarity.vector(tile) for name, tile in split.tiles.items()}


class _Scores:
    """Cosine similarity between tiles of one split, cached on the unordered pair
    since cosine is symmetric and lines within a set share many pairs."""

    def __init__(self, vectors: dict[str, np.ndarray]) -> None:
        self._vectors = vectors
        self._cache: dict[tuple[str, str], float] = {}

    def between(self, left: str, right: str) -> float:
        """How alike the two named tiles are."""
        key = (left, right) if left <= right else (right, left)
        if key not in self._cache:
            try:
                first, second = self._vectors[key[0]], self._vectors[key[1]]
            except KeyError as exc:
                raise PaylineCheckFailedError(
                    f"the split holds no tile {exc.args[0]!r} for a payline that "
                    "runs through it"
                ) from exc
            if first.shape != second.shape:
                raise PaylineCheckFailedError(
                    f"tiles {key[0]} and {key[1]} are different sizes, so they "
                    "did not come from one split"
                )
            self._cache[key] = similarity.vector_cosine(first, second)
        return self._cache[key]

    @property
    def measured(self) -> list[float]:
        """Every distinct pair's score, for the run's own distribution."""
        return list(self._cache.values())


def _evaluate(
    line: payline_config.Payline, scores: _Scores, threshold: float
) -> tuple[list[PaylineStep], int, str | None]:
    """One line's steps, how many positions it pays on, and where it broke.
    ``counted`` marks the steps the left-to-right read actually reached; the run
    ends at the first counted step that doesn't match."""
    steps: list[PaylineStep] = []
    running = True
    run = 0
    break_position: str | None = None
    for left, right in line.steps:
        score = scores.between(left.name, right.name)
        matched = score >= threshold
        steps.append(
            PaylineStep(
                left=left.name,
                right=right.name,
                similarity=round(score, 6),
                matched=matched,
                counted=running,
            )
        )
        if running:
            if matched:
                run += 1
            else:
                running = False
                break_position = right.name
    # A run of N matched pairs covers N+1 positions; zero matched pairs covers none.
    pays = run + 1 if run else 0
    return steps, pays, break_position


# --- drawing and writing --------------------------------------------------


def _placed_tiles(
    grid: reel_grid.ReelGrid, split: grid_service.SplitOnDisk
) -> dict[str, reel_grid.PlacedTile]:
    """Every tile of the grid, positioned on this split's own crop — the exact
    box each tile was cut out with, so overlay borders line up with the symbol."""
    try:
        placed = grid.place(split.crop.width, split.crop.height)
    except reel_grid.ReelGridError as exc:
        raise PaylineCheckFailedError(
            f"{split.name} left no room to place tiles: {exc}"
        ) from exc
    return {tile.name: tile for tile in placed}


def _centre(box: tuple[int, int, int, int]) -> tuple[int, int]:
    """The middle of one tile's box, for the stroke a line is drawn as."""
    return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2


def _line_drawing(
    index: int,
    line: payline_config.Payline,
    pays: int,
    break_position: str | None,
    tiles: dict[str, reel_grid.PlacedTile],
) -> payline_overlay.DrawnLine:
    """One line, as the overlay wants it -- paying or not. ``points`` is the
    confirmed run and nothing more, empty when ``pays`` is 0."""
    boxes = {position.name: tiles[position.name].box for position in line.positions}
    centres = {name: _centre(box) for name, box in boxes.items()}

    matched = line.positions[:pays]
    rest = line.positions[pays:]

    return payline_overlay.DrawnLine(
        colour=payline_overlay.colour(index),
        points=[centres[position.name] for position in matched],
        trail=[centres[position.name] for position in rest],
        matched_boxes=[boxes[position.name] for position in matched],
        break_box=boxes[break_position] if break_position is not None else None,
    )


def _write(image: Image.Image, directory: Path, file_name: str) -> Path:
    """Write the annotated picture into the split's own directory."""
    destination = directory / file_name
    try:
        directory.mkdir(parents=True, exist_ok=True)
        image.save(destination, format="PNG")
    except OSError as exc:
        raise PaylineCheckFailedError(
            f"{destination} could not be written: {exc}"
        ) from exc
    return destination


def _encode(image: Image.Image) -> str:
    """One picture as a PNG data URI, translating ROI's error into this feature's own."""
    try:
        return roi_service.encode_png(image)
    except RoiExtractFailedError as exc:
        raise PaylineCheckFailedError(
            f"the annotated reels could not be encoded: {exc}"
        ) from exc


# --- reporting ------------------------------------------------------------


def _describe(split: grid_service.SplitOnDisk) -> PaylineSource:
    """What the API says about the split a check was run against."""
    return PaylineSource(
        split=split.name,
        written_at=split.written_at,
        rows=split.rows,
        columns=split.columns,
        tile_width=split.tile_width,
        tile_height=split.tile_height,
        width=split.crop.width,
        height=split.crop.height,
    )


def _summarise(lines: list[PaylineLine]) -> str:
    """The result as a sentence, e.g. ``Line 1 pays 2, Line 3 pays 4`` — paying
    lines only, in reading order."""
    paying = [f"{line.label} pays {line.pays}" for line in lines if line.paying]
    return ", ".join(paying) if paying else "No line pays"


def _stats(lines: list[PaylineLine], scores: _Scores, threshold: float) -> PaylineStats:
    """The run as a whole, including how well its scores separated. ``matched_min``
    and ``rejected_max`` are counted over distinct pairs, not steps, so a pair
    two lines share isn't weighted twice."""
    measured = scores.measured
    matched = [score for score in measured if score >= threshold]
    rejected = [score for score in measured if score < threshold]
    best = max(lines, key=lambda line: line.pays, default=None)
    return PaylineStats(
        lines=len(lines),
        paying=sum(1 for line in lines if line.paying),
        comparisons=len(measured),
        matches=len(matched),
        best_line=best.name if best is not None and best.paying else None,
        best_pays=best.pays if best is not None else 0,
        score_min=round(min(measured), 6) if measured else None,
        score_max=round(max(measured), 6) if measured else None,
        matched_min=round(min(matched), 6) if matched else None,
        rejected_max=round(max(rejected), 6) if rejected else None,
    )


# --- public API -----------------------------------------------------------


def _layout() -> PaylineLayout:
    """Blocking half of :func:`layout`."""
    name, config = _active_config()
    threshold = settings.PAYLINE_MATCH_THRESHOLD

    try:
        names = _set_names(config)
        grid = _grid(config)
    except (PaylinesNotConfiguredError, GridNotConfiguredError) as exc:
        # Reported rather than raised: an unconfigured game isn't a broken panel.
        return PaylineLayout(game=name, threshold=threshold, error=exc.message)

    directory = grid_service.latest_split()
    source: PaylineSource | None = None
    error: str | None = None
    if directory is None:
        error = "No reel grid has been split yet. Split one from the Reel grid panel."
    else:
        try:
            source = _describe(grid_service.read_split(directory))
        except (PaylineSourceNotFoundError, RoiExtractFailedError) as exc:
            # Reported, not raised — the panel's job is to say why Check is disabled.
            error = exc.message

    return PaylineLayout(
        game=name,
        sets=[PaylineSetOption(name=entry, label=f"{entry} lines") for entry in names],
        default_set=_default_set(names),
        threshold=threshold,
        rows=grid.row_count,
        columns=grid.column_count,
        latest_split=source,
        error=error,
    )


async def layout() -> PaylineLayout:
    """The sets the active game declares, and the split a check would read. An
    unconfigured game or a checkout with nothing split yet comes back with
    ``error`` set on a 200, not as a failed request."""
    return await asyncio.to_thread(_layout)


def _check(request: PaylineCheckRequest) -> PaylineCheckResult:
    """Blocking half of :func:`check`."""
    name, config = _active_config()
    grid = _grid(config)
    chosen = request.set or _default_set(_set_names(config))
    line_set = _read_set(config, chosen)
    threshold = (
        request.threshold
        if request.threshold is not None
        else settings.PAYLINE_MATCH_THRESHOLD
    )

    split = grid_service.read_split(grid_service.resolve_split(request.split))
    if (split.rows, split.columns) != (grid.row_count, grid.column_count):
        # A conflict, not a 500 — the config may be right and the split merely old.
        raise PaylineSourceStaleError(
            f"{split.name} is a {split.rows}x{split.columns} split but "
            f"{name} now declares a {grid.row_count}x{grid.column_count} grid -- "
            "split the frame again"
        )
    try:
        line_set.within(split.rows, split.columns)
    except payline_config.PaylineError as exc:
        raise GameConfigInvalidError(f"{config.path}: {exc}") from exc

    scores = _Scores(_vectors(split))
    tiles = _placed_tiles(grid, split)
    scale = payline_overlay.scale_for(
        split.crop.width, settings.PAYLINE_OVERLAY_MIN_WIDTH
    )

    lines: list[PaylineLine] = []
    paying_drawings: list[payline_overlay.DrawnLine] = []
    for index, line in enumerate(line_set.lines):
        steps, pays, break_position = _evaluate(line, scores, threshold)
        drawing = _line_drawing(index, line, pays, break_position, tiles)
        paying = pays >= _MIN_PAYING
        if paying:
            # Just the path on the combined picture -- several lines share it.
            paying_drawings.append(dataclasses.replace(drawing, detailed=False))

        line_image = (
            payline_overlay.draw(split.crop, [drawing], scale=scale)
            if request.include_images
            else None
        )
        lines.append(
            PaylineLine(
                name=line.name,
                label=line.label,
                positions=[position.name for position in line.positions],
                pays=pays,
                paying=paying,
                matched_positions=[position.name for position in line.positions[:pays]],
                color=payline_overlay.colour(index),
                steps=steps,
                break_position=break_position,
                image_data=_encode(line_image) if line_image is not None else None,
            )
        )

    overlay = payline_overlay.draw(split.crop, paying_drawings, scale=scale)
    directory = split.directory / _OUTPUT_DIR
    file_name = f"{chosen}.png"
    _write(overlay, directory, file_name)

    stats = _stats(lines, scores, threshold)
    logger.info(
        "Checked the %s payline set of %s against %s at threshold %.4f: %s "
        "(%d of %d pairs matched)",
        chosen,
        name,
        split.name,
        threshold,
        _summarise(lines),
        stats.matches,
        stats.comparisons,
    )
    return PaylineCheckResult(
        game=name,
        set=chosen,
        threshold=threshold,
        source=_describe(split),
        summary=_summarise(lines),
        lines=lines,
        stats=stats,
        output_dir=str(directory),
        output_file=file_name,
        overlay_image=_encode(overlay) if request.include_images else None,
    )


async def check(request: PaylineCheckRequest) -> PaylineCheckResult:
    """Evaluate one bet configuration's lines against one split reel grid."""
    return await asyncio.to_thread(_check, request)
