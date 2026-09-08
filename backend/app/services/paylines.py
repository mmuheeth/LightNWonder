"""Checks a split reel grid against the patterns that pay."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Collection, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.config.runtime import settings
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
    PaylineMethod,
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
    """The active game's reel grid, read from the config (``inset`` included) since only
    that gives a tile's true centre."""
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


@dataclasses.dataclass(frozen=True)
class _Comparison:
    """One adjacent pair, as whichever comparer decided it."""

    matched: bool
    similarity: float | None = None
    left_symbol: str | None = None
    right_symbol: str | None = None

    def flipped(self) -> _Comparison:
        """The same comparison with its two sides swapped."""
        return dataclasses.replace(
            self, left_symbol=self.right_symbol, right_symbol=self.left_symbol
        )


@dataclasses.dataclass(frozen=True)
class _LineRead:
    """One line, read left to right by whichever comparer holds the split."""

    steps: list[PaylineStep]

    covered: int
    """Positions from the left the run reached. 1 means it got nowhere; the pay
    floor is the caller's to apply."""

    break_position: str | None
    """Where the run stopped, or ``None`` when it reached the end of the line."""

    symbol: str | None = None
    """The code the run pays as, for a comparer that names tiles."""

    leading_wilds: int = 0
    """How many of the run's leading positions were the wild itself."""


class _Comparer:
    """Decides whether two tiles of one split are the same symbol."""

    method: PaylineMethod
    threshold: float | None = None

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], _Comparison] = {}

    def read(self, positions: Sequence[str]) -> _LineRead:
        """One line's steps, how far its leading run reached, and where it broke."""
        steps: list[PaylineStep] = []
        running = True
        covered = 1 if positions else 0
        break_position: str | None = None
        for left, right in pairwise(positions):
            found = self.compare(left, right)
            steps.append(
                PaylineStep(
                    left=left,
                    right=right,
                    similarity=found.similarity,
                    left_symbol=found.left_symbol,
                    right_symbol=found.right_symbol,
                    matched=found.matched,
                    counted=running,
                )
            )
            if running:
                if found.matched:
                    covered += 1
                else:
                    running = False
                    break_position = right
        return _LineRead(steps=steps, covered=covered, break_position=break_position)

    def compare(self, left: str, right: str) -> _Comparison:
        """How the two named tiles compare, in the order they were asked about."""
        key = (left, right) if left <= right else (right, left)
        if key not in self._cache:
            self._cache[key] = self._decide(key[0], key[1])
        found = self._cache[key]
        # The cache is keyed on the unordered pair, so a step whose tiles arrived
        # the other way round has to be reported the way it was asked -- else a
        # line's steps would carry the codes of its two tiles transposed.
        return found if key[0] == left else found.flipped()

    def _decide(self, left: str, right: str) -> _Comparison:
        raise NotImplementedError

    def symbol(self, name: str) -> str | None:  # noqa: ARG002 - base answers for all
        """The code read off one tile, or ``None`` when this comparer names none."""
        return None

    @property
    def wilds(self) -> payline_config.WildRule | None:
        """The substitution that was applied, or ``None`` when none was."""
        return None

    @property
    def distinct(self) -> list[_Comparison]:
        """Every distinct pair's verdict, for the run's own figures."""
        return list(self._cache.values())


class _SimilarityComparer(_Comparer):
    """Two tiles are the same symbol when their cosine similarity clears the cut."""

    method = PaylineMethod.SIMILARITY

    def __init__(self, split: grid_service.SplitOnDisk, threshold: float) -> None:
        super().__init__()
        self._vectors = _vectors(split)
        # Kept twice on purpose: `threshold` is the reported cut, typed nullable
        # on the base because a symbol comparison has none, and `_cut` is the
        # float this one actually compares against.
        self._cut = threshold
        self.threshold = threshold

    def _decide(self, left: str, right: str) -> _Comparison:
        try:
            first, second = self._vectors[left], self._vectors[right]
        except KeyError as exc:
            raise PaylineCheckFailedError(
                f"the split holds no tile {exc.args[0]!r} for a payline that "
                "runs through it"
            ) from exc
        if first.shape != second.shape:
            raise PaylineCheckFailedError(
                f"tiles {left} and {right} are different sizes, so they did not "
                "come from one split"
            )
        score = similarity.vector_cosine(first, second)
        return _Comparison(matched=score >= self._cut, similarity=round(score, 6))


class _SymbolComparer(_Comparer):
    """Two tiles are the same symbol when a classifier read the same code off both -- or
    when one of them is the wild, standing in for the other."""

    method = PaylineMethod.SYMBOL

    def __init__(
        self,
        symbols: Mapping[str, str | None],
        wilds: payline_config.WildRule = payline_config.NO_WILDS,
    ) -> None:
        super().__init__()
        # Lower-cased because a split's tiles are keyed by their file stem while a
        # line's positions come from `reel_grid.position_name`. The two agree
        # today, and this is the one place that would notice if they stopped.
        self._symbols = {name.lower(): code for name, code in symbols.items()}
        self._wilds = wilds

    def symbol(self, name: str) -> str | None:
        return self._symbols.get(name.lower())

    @property
    def wilds(self) -> payline_config.WildRule | None:
        return self._wilds if self._wilds.active else None

    def read(self, positions: Sequence[str]) -> _LineRead:
        codes = [self.symbol(name) for name in positions]
        run = payline_config.read_run(codes, self._wilds)
        steps: list[PaylineStep] = []
        for index, (left, right) in enumerate(pairwise(positions)):
            # Compared even past the break: the cache this fills is what the
            # run's distinct-pair figures are counted off, and it is the only
            # thing an uncounted step can be said to have decided.
            pair = self.compare(left, right)
            counted = index < run.covered
            steps.append(
                PaylineStep(
                    left=left,
                    right=right,
                    similarity=None,
                    left_symbol=codes[index],
                    right_symbol=codes[index + 1],
                    line_symbol=run.line_symbols[index] if counted else None,
                    # For a counted step this is "the run continued here", which
                    # the line as a whole settles; for one past the break it is
                    # the pairwise question, the only one still meaningful.
                    matched=(index + 1) < run.covered if counted else pair.matched,
                    counted=counted,
                )
            )
        return _LineRead(
            steps=steps,
            covered=run.covered,
            break_position=(
                positions[run.covered] if run.covered < len(positions) else None
            ),
            symbol=run.symbol,
            leading_wilds=run.leading_wilds,
        )

    def _decide(self, left: str, right: str) -> _Comparison:
        first, second = self.symbol(left), self.symbol(right)
        return _Comparison(
            matched=self._alike(first, second),
            left_symbol=first,
            right_symbol=second,
        )

    def _alike(self, first: str | None, second: str | None) -> bool:
        """Whether two codes are one symbol *pairwise* -- all a lone pair can say."""
        if first is None or second is None:
            return False
        if first == second:
            return True
        return (self._wilds.is_wild(first) and self._wilds.stands_in_for(second)) or (
            self._wilds.is_wild(second) and self._wilds.stands_in_for(first)
        )


_ComparerFor = Callable[[grid_service.SplitOnDisk, GameConfig], _Comparer]
"""Builds the comparer for one split. A factory rather than a comparer because
the split is read inside :func:`_evaluate_set`, which is also where its shape is
checked -- one built before that would be built against an unvetted split. It is
handed the active game's config too, since the wild's substitution list lives
there and :func:`check_symbols` is called from a caller that has not loaded it."""


def _by_similarity(threshold: float | None) -> _ComparerFor:
    """Compare tiles by cosine similarity, at ``threshold`` or the configured cut."""
    cut = threshold if threshold is not None else settings.PAYLINE_MATCH_THRESHOLD
    return lambda split, _config: _SimilarityComparer(split, cut)


def _by_symbol(symbols: Mapping[str, str | None]) -> _ComparerFor:
    """Compare tiles by the symbol codes a classifier read off them, substituting
    the wild for whatever the active game declares it stands in for."""
    return lambda _split, config: _SymbolComparer(symbols, _wilds(config))


def _wilds(config: GameConfig) -> payline_config.WildRule:
    """The active game's substitution rule."""
    return payline_config.WildRule.of(config.wild_card_replacement)


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
    """One line, as the overlay wants it -- paying or not. ``points`` is the confirmed
    run and nothing more, empty when ``pays`` is 0."""
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


def _stats(lines: list[PaylineLine], comparer: _Comparer) -> PaylineStats:
    """The run as a whole, including how well its scores separated -- when there were
    scores."""
    distinct = comparer.distinct
    measured = [one.similarity for one in distinct if one.similarity is not None]
    matched = [
        one.similarity for one in distinct if one.matched and one.similarity is not None
    ]
    rejected = [
        one.similarity
        for one in distinct
        if not one.matched and one.similarity is not None
    ]
    best = max(lines, key=lambda line: line.pays, default=None)
    return PaylineStats(
        lines=len(lines),
        paying=sum(1 for line in lines if line.paying),
        comparisons=len(distinct),
        matches=sum(1 for one in distinct if one.matched),
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
    """The sets the active game declares, and the split a check would read."""
    return await asyncio.to_thread(_layout)


@dataclasses.dataclass(frozen=True)
class ImageOptions:
    """Which pictures one check comes back with."""

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
    comparer_for: _ComparerFor,
    images: ImageOptions,
) -> PaylineCheckResult:
    """Compare one set of lines against one written split."""
    grid = _grid(config)

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

    comparer = comparer_for(split, config)
    tiles = _placed_tiles(grid, split)
    scale = payline_overlay.scale_for(
        split.crop.width, settings.PAYLINE_OVERLAY_MIN_WIDTH
    )

    lines: list[PaylineLine] = []
    paying_drawings: list[payline_overlay.DrawnLine] = []
    for index, line in enumerate(line_set.lines):
        names = list(line.names)
        read = comparer.read(names)
        # A read that got only one position in covers no adjacent pair, so it
        # pays nothing -- the length is reported as 0 rather than 1 so `pays` is
        # never a number no combo could match.
        pays = read.covered if read.covered >= _MIN_PAYING else 0
        steps = read.steps
        break_position = read.break_position
        drawing = _line_drawing(index, names, pays, break_position, tiles)
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
                matched_positions=names[:pays],
                symbols=[comparer.symbol(name) for name in names]
                if comparer.method is PaylineMethod.SYMBOL
                else [],
                # What the run pays as, which is not `symbols[0]` once a wild is
                # in play -- a line landing a wild on reel 1 is not a line of
                # wilds. Only set for a run that pays: a line that got nowhere
                # has nothing to price, and `symbols` already says what its
                # first tile was.
                symbol=read.symbol if paying else None,
                leading_wilds=read.leading_wilds,
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

    stats = _stats(lines, comparer)
    wilds = comparer.wilds
    logger.info(
        "Checked the %s payline set of %s against %s by %s (%s, %s): %s "
        "(%d of %d pairs matched)",
        line_set.name,
        game,
        split.name,
        comparer.method.value,
        "no threshold"
        if comparer.threshold is None
        else f"threshold {comparer.threshold:.4f}",
        "no wild"
        if wilds is None
        else f"{wilds.code} stands in for {len(wilds.replaces)} symbol(s)",
        _summarise(lines),
        stats.matches,
        stats.comparisons,
    )
    return PaylineCheckResult(
        game=game,
        set=line_set.name,
        method=comparer.method,
        threshold=comparer.threshold,
        wild_symbol=None if wilds is None else wilds.code,
        wild_replaces=[] if wilds is None else sorted(wilds.replaces),
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
        comparer_for=_by_similarity(request.threshold),
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
    comparer_for: _ComparerFor,
    images: ImageOptions,
) -> PaylineCheckResult:
    """Blocking half of :func:`check_lines` and :func:`check_symbols`."""
    name, config = _active_config()
    return _evaluate_set(
        name,
        config,
        line_set,
        split_name=split,
        comparer_for=comparer_for,
        images=images,
    )


async def check_lines(
    line_set: payline_config.PaylineSet,
    *,
    split: str | None = None,
    threshold: float | None = None,
    images: ImageOptions | None = None,
) -> PaylineCheckResult:
    """Evaluate a caller-supplied set of lines against one split reel grid."""
    return await asyncio.to_thread(
        _check_lines,
        line_set,
        split=split,
        comparer_for=_by_similarity(threshold),
        images=images or ImageOptions(),
    )


async def check_symbols(
    line_set: payline_config.PaylineSet,
    symbols: Mapping[str, str | None],
    *,
    split: str | None = None,
    images: ImageOptions | None = None,
) -> PaylineCheckResult:
    """Evaluate a set of lines against symbol codes already read off the tiles."""
    return await asyncio.to_thread(
        _check_lines,
        line_set,
        split=split,
        comparer_for=_by_symbol(symbols),
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
    """Draw the combined overlay again, over a chosen subset of the same lines."""
    return await asyncio.to_thread(
        _redraw, result, names, file_name=file_name or result.output_file
    )
