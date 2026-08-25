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

:func:`check` reads the set out of the active game's ``paylines`` block;
:func:`check_lines` takes one it was handed, which is how
:mod:`app.services.analyze_spin` checks the lines the *running* game declares
in its own ``winGeometry.xml``. Both go through :func:`_evaluate_set`, so a
line set from either source is scored, drawn and written identically.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Literal

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
    PaylineOverlay,
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
    positions: Sequence[str],
    pays: int,
    break_position: str | None,
    tiles: dict[str, reel_grid.PlacedTile],
) -> payline_overlay.DrawnLine:
    """One line, as the overlay wants it -- paying or not. ``points`` is the
    confirmed run and nothing more, empty when ``pays`` is 0.

    Takes position *names* rather than a parsed line, so a caller holding only a
    finished :class:`PaylineCheckResult` can rebuild the same drawing -- which is
    what :func:`redraw` does, and why the two cannot draw a line differently."""
    boxes = {name: tiles[name].box for name in positions}
    centres = {name: _centre(box) for name, box in boxes.items()}

    matched = positions[:pays]
    rest = positions[pays:]

    return payline_overlay.DrawnLine(
        colour=payline_overlay.colour(index),
        points=[centres[name] for name in matched],
        trail=[centres[name] for name in rest],
        matched_boxes=[boxes[name] for name in matched],
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


@dataclasses.dataclass(frozen=True)
class ImageOptions:
    """Which pictures one check comes back with.

    Two knobs rather than one flag because the two pictures answer different
    questions and cost wildly different amounts: the combined overlay is one
    image, and per-line pictures are one *per line* -- forty of them for a
    forty-line set, which is fine to return to a panel that asked for them and
    not fine to attach to something polling. ``"paying"`` is the middle ground:
    the lines a reader would actually open.
    """

    overlay: bool = True
    """Whether the combined picture of every paying line comes back inline. It
    is written to disk either way."""

    lines: Literal["all", "paying", "none"] = "all"
    """Which lines get their own tracking picture."""


def _wants_line_image(options: ImageOptions, *, paying: bool) -> bool:
    """Whether one line's own picture is being asked for."""
    return options.lines == "all" or (options.lines == "paying" and paying)


def _evaluate_set(
    game: str,
    config: GameConfig,
    line_set: payline_config.PaylineSet,
    *,
    split_name: str | None,
    threshold: float | None,
    images: ImageOptions,
) -> PaylineCheckResult:
    """Compare one set of lines against one written split.

    Takes the set rather than reading it, so the same comparison serves a set
    declared in this repo's game config *and* one assembled from the running
    game's own ``winGeometry.xml`` (see :mod:`app.services.analyze_spin`).
    Everything below the set -- the grid geometry, the tiles, the threshold, the
    drawing -- is identical either way, and having two copies of it is how the
    two would drift apart.
    """
    grid = _grid(config)
    cut = threshold if threshold is not None else settings.PAYLINE_MATCH_THRESHOLD

    split = grid_service.read_split(grid_service.resolve_split(split_name))
    if (split.rows, split.columns) != (grid.row_count, grid.column_count):
        # A conflict, not a 500 -- the config may be right and the split merely old.
        raise PaylineSourceStaleError(
            f"{split.name} is a {split.rows}x{split.columns} split but "
            f"{game} now declares a {grid.row_count}x{grid.column_count} grid -- "
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
        steps, pays, break_position = _evaluate(line, scores, cut)
        drawing = _line_drawing(
            index,
            [position.name for position in line.positions],
            pays,
            break_position,
            tiles,
        )
        paying = pays >= _MIN_PAYING
        if paying:
            # Just the path on the combined picture -- several lines share it.
            paying_drawings.append(dataclasses.replace(drawing, detailed=False))

        line_image = (
            payline_overlay.draw(split.crop, [drawing], scale=scale)
            if _wants_line_image(images, paying=paying)
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
    file_name = f"{line_set.name}.png"
    _write(overlay, directory, file_name)

    stats = _stats(lines, scores, cut)
    logger.info(
        "Checked the %s payline set of %s against %s at threshold %.4f: %s "
        "(%d of %d pairs matched)",
        line_set.name,
        game,
        split.name,
        cut,
        _summarise(lines),
        stats.matches,
        stats.comparisons,
    )
    return PaylineCheckResult(
        game=game,
        set=line_set.name,
        threshold=cut,
        source=_describe(split),
        summary=_summarise(lines),
        lines=lines,
        stats=stats,
        output_dir=str(directory),
        output_file=file_name,
        overlay_image=_encode(overlay) if images.overlay else None,
    )


def _check(request: PaylineCheckRequest) -> PaylineCheckResult:
    """Blocking half of :func:`check`."""
    name, config = _active_config()
    chosen = request.set or _default_set(_set_names(config))
    return _evaluate_set(
        name,
        config,
        _read_set(config, chosen),
        split_name=request.split,
        threshold=request.threshold,
        images=(
            ImageOptions(overlay=True, lines="all")
            if request.include_images
            else ImageOptions(overlay=False, lines="none")
        ),
    )


async def check(request: PaylineCheckRequest) -> PaylineCheckResult:
    """Evaluate one bet configuration's lines against one split reel grid."""
    return await asyncio.to_thread(_check, request)


def _check_lines(
    line_set: payline_config.PaylineSet,
    *,
    split: str | None,
    threshold: float | None,
    images: ImageOptions,
) -> PaylineCheckResult:
    """Blocking half of :func:`check_lines`."""
    name, config = _active_config()
    return _evaluate_set(
        name,
        config,
        line_set,
        split_name=split,
        threshold=threshold,
        images=images,
    )


async def check_lines(
    line_set: payline_config.PaylineSet,
    *,
    split: str | None = None,
    threshold: float | None = None,
    images: ImageOptions | None = None,
) -> PaylineCheckResult:
    """Evaluate a caller-supplied set of lines against one split reel grid.

    The public door for lines that did not come from the active game's
    ``paylines`` block -- :mod:`app.services.analyze_spin` builds one out of the
    running game's ``winGeometry.xml``. The active game still supplies the grid
    geometry (``reel_bounds``), because that is where the tiles are, not which
    patterns pay.
    """
    return await asyncio.to_thread(
        _check_lines,
        line_set,
        split=split,
        threshold=threshold,
        images=images or ImageOptions(),
    )


def _redraw(
    result: PaylineCheckResult, names: Collection[str], *, file_name: str
) -> PaylineOverlay:
    """Blocking half of :func:`redraw`."""
    _, config = _active_config()
    grid = _grid(config)
    split = grid_service.read_split(grid_service.resolve_split(result.source.split))
    tiles = _placed_tiles(grid, split)
    scale = payline_overlay.scale_for(
        split.crop.width, settings.PAYLINE_OVERLAY_MIN_WIDTH
    )

    wanted = set(names)
    drawings = [
        # `index` has to be the line's place in the *whole* set, not in the
        # subset being drawn, or a filtered overlay would recolour the lines it
        # kept and stop matching the swatches beside them.
        _line_drawing(index, line.positions, line.pays, None, tiles)
        for index, line in enumerate(result.lines)
        if line.name in wanted
    ]
    overlay = payline_overlay.draw(
        split.crop,
        [dataclasses.replace(one, detailed=False) for one in drawings],
        scale=scale,
    )
    directory = split.directory / _OUTPUT_DIR
    _write(overlay, directory, file_name)
    logger.info(
        "Redrew the %s overlay of %s as %d of %d line(s): %s",
        result.set,
        split.name,
        len(drawings),
        len(result.lines),
        directory / file_name,
    )
    return PaylineOverlay(
        output_dir=str(directory),
        output_file=file_name,
        image_data=_encode(overlay),
    )


async def redraw(
    result: PaylineCheckResult,
    names: Collection[str],
    *,
    file_name: str | None = None,
) -> PaylineOverlay:
    """Draw the combined overlay again, over a chosen subset of the same lines.

    For the caller that can only decide which lines matter *after* the check has
    run -- :mod:`app.services.analyze_spin` needs the measured pairs before it can
    read the game's reel stops, and only then knows which runs the paytable
    actually pays. Rebuilt from the result rather than re-evaluated, so the
    picture cannot disagree with the numbers it came from, and it goes through
    the same :func:`_line_drawing` so a redrawn line is drawn identically to a
    first-pass one, colour included.

    ``file_name`` defaults to the one the check already wrote, which **replaces**
    it: one picture per (split, set) is the whole convention there, and leaving a
    superseded overlay beside the current one is how a reader ends up looking at
    the wrong evidence.
    """
    return await asyncio.to_thread(
        _redraw, result, names, file_name=file_name or result.output_file
    )
