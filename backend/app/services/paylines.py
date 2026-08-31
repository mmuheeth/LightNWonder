"""Checks a split reel grid against the patterns that pay.

One step past :mod:`app.services.grid`: grid asks *does it divide into the
symbols expected*, this asks *do those symbols line up*. Reads a written split
(via :func:`app.services.grid.resolve_split`/:func:`read_split`), never a
screenshot directly, so a check is repeatable without the simulator still
showing the same spin. A line reads left to right and stops at the first
non-matching adjacent pair, so ``pays`` is the length of that *leading* run (0,
or 2+) — every pair is still compared (``counted`` marks which ones the run
reached) since the pairs after a break are evidence the break was real. Each
line gets its own picture with a break marker, paying or not; the combined
overlay draws only paying lines and never the break ring, since several lines
share it. Holds no state, so no ``reset()``.

**Two tiles are "the same symbol" in one of two ways, and which one was used
travels on the answer** (``method``, :class:`app.schemas.paylines.PaylineMethod`):

* :data:`PaylineMethod.SIMILARITY` — cosine similarity between the two pictures,
  cut at ``PAYLINE_MATCH_THRESHOLD`` (invariant to the brightness pulsing and
  glowing symbols add — see :mod:`app.utils.similarity`). The per-game cut
  between the match and no-match clusters is why a result read this way reports
  ``matched_min``/``rejected_max`` beside the threshold. It says the tiles are
  alike and never which symbol they are.
* :data:`PaylineMethod.SYMBOL` — the codes a classifier read off each tile
  (:mod:`app.services.image_classifier`), compared for equality. No threshold,
  no scores, and the symbol is *named*, which is what lets a caller holding the
  paytable price a run exactly instead of narrowing it to every row that pays at
  that length. Two unnamed tiles never match: "I could not tell" twice is not
  "the same symbol".

Everything below the comparison — the grid geometry, the tiles, the drawing, the
files written — is identical either way, which is the whole reason both go
through :func:`_evaluate_set`.

:func:`check` reads the set out of the active game's ``paylines`` block and
compares by similarity; :func:`check_lines` takes a set it was handed;
:func:`check_symbols` takes a set *and* the codes to read it by, which is how
:mod:`app.services.analyze_spin` checks the lines the *running* game declares in
its own ``winGeometry.xml`` against the symbols the classifier named.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Collection, Mapping, Sequence
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


@dataclasses.dataclass(frozen=True)
class _Comparison:
    """One adjacent pair, as whichever comparer decided it.

    Both kinds of evidence are optional because the two comparers measure
    different things: a similarity pair has a score and cannot name either tile,
    a symbol pair names both and has no score. Carrying one shape with holes in
    it, rather than two shapes, is what keeps :func:`_evaluate` unaware of which
    comparison it is reading.
    """

    matched: bool
    similarity: float | None = None
    left_symbol: str | None = None
    right_symbol: str | None = None

    def flipped(self) -> _Comparison:
        """The same comparison with its two sides swapped."""
        return dataclasses.replace(
            self, left_symbol=self.right_symbol, right_symbol=self.left_symbol
        )


class _Comparer:
    """Decides whether two tiles of one split are the same symbol.

    Caches on the *unordered* pair: both ways of deciding are symmetric, and
    lines within a set share many pairs -- so a pair two lines run through is one
    comparison, which is also what keeps ``comparisons`` a count of distinct
    pairs rather than of steps.
    """

    method: PaylineMethod
    threshold: float | None = None

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], _Comparison] = {}

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
    def distinct(self) -> list[_Comparison]:
        """Every distinct pair's verdict, for the run's own figures."""
        return list(self._cache.values())


class _SimilarityComparer(_Comparer):
    """Two tiles are the same symbol when their cosine similarity clears the cut.

    Says nothing about *which* symbol: the vectors are pixels, and two crops of
    one symbol score alike whatever that symbol is. Closing that gap is the
    caller's problem -- or :class:`_SymbolComparer`'s.
    """

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
    """Two tiles are the same symbol when a classifier read the same code off both.

    No threshold and no scores -- two codes are equal or they are not. **Two
    unnamed tiles are not a match**: a classifier below its confidence floor said
    "I could not tell", and twice over that is not evidence of a run. A line
    through such a tile stops there, which is the honest answer and the reason
    the tile's own confidence has to travel back to the caller.
    """

    method = PaylineMethod.SYMBOL

    def __init__(self, symbols: Mapping[str, str | None]) -> None:
        super().__init__()
        # Lower-cased because a split's tiles are keyed by their file stem while a
        # line's positions come from `reel_grid.position_name`. The two agree
        # today, and this is the one place that would notice if they stopped.
        self._symbols = {name.lower(): code for name, code in symbols.items()}

    def symbol(self, name: str) -> str | None:
        return self._symbols.get(name.lower())

    def _decide(self, left: str, right: str) -> _Comparison:
        first, second = self.symbol(left), self.symbol(right)
        return _Comparison(
            matched=first is not None and first == second,
            left_symbol=first,
            right_symbol=second,
        )


_ComparerFor = Callable[[grid_service.SplitOnDisk], _Comparer]
"""Builds the comparer for one split. A factory rather than a comparer because
the split is read inside :func:`_evaluate_set`, which is also where its shape is
checked -- one built before that would be built against an unvetted split."""


def _by_similarity(threshold: float | None) -> _ComparerFor:
    """Compare tiles by cosine similarity, at ``threshold`` or the configured cut."""
    cut = threshold if threshold is not None else settings.PAYLINE_MATCH_THRESHOLD
    return lambda split: _SimilarityComparer(split, cut)


def _by_symbol(symbols: Mapping[str, str | None]) -> _ComparerFor:
    """Compare tiles by the symbol codes a classifier read off them."""
    return lambda _split: _SymbolComparer(symbols)


def _evaluate(
    line: payline_config.Payline, comparer: _Comparer
) -> tuple[list[PaylineStep], int, str | None]:
    """One line's steps, how many positions it pays on, and where it broke.
    ``counted`` marks the steps the left-to-right read actually reached; the run
    ends at the first counted step that doesn't match."""
    steps: list[PaylineStep] = []
    running = True
    run = 0
    break_position: str | None = None
    for left, right in line.steps:
        found = comparer.compare(left.name, right.name)
        steps.append(
            PaylineStep(
                left=left.name,
                right=right.name,
                similarity=found.similarity,
                left_symbol=found.left_symbol,
                right_symbol=found.right_symbol,
                matched=found.matched,
                counted=running,
            )
        )
        if running:
            if found.matched:
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


def _stats(lines: list[PaylineLine], comparer: _Comparer) -> PaylineStats:
    """The run as a whole, including how well its scores separated -- when there
    were scores. Counted over distinct pairs, not steps, so a pair two lines
    share isn't weighted twice. A symbol comparison leaves the four score figures
    null rather than filling them with 0 and 1: there is no distribution to
    separate, and a printed threshold nothing was measured against is worse than
    a blank."""
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
    comparer_for: _ComparerFor,
    images: ImageOptions,
) -> PaylineCheckResult:
    """Compare one set of lines against one written split.

    Takes the set rather than reading it, so the same comparison serves a set
    declared in this repo's game config *and* one assembled from the running
    game's own ``winGeometry.xml`` (see :mod:`app.services.analyze_spin`). Takes
    the comparer for the same reason: whether two tiles are one symbol is decided
    by cosine similarity or by a classifier's codes, and everything below that --
    the grid geometry, the tiles, the drawing, the files written -- is identical
    either way. Two copies of this is how the two would drift apart.
    """
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

    comparer = comparer_for(split)
    tiles = _placed_tiles(grid, split)
    scale = payline_overlay.scale_for(
        split.crop.width, settings.PAYLINE_OVERLAY_MIN_WIDTH
    )

    lines: list[PaylineLine] = []
    paying_drawings: list[payline_overlay.DrawnLine] = []
    for index, line in enumerate(line_set.lines):
        steps, pays, break_position = _evaluate(line, comparer)
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
                symbols=[comparer.symbol(position.name) for position in line.positions]
                if comparer.method is PaylineMethod.SYMBOL
                else [],
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
    logger.info(
        "Checked the %s payline set of %s against %s by %s (%s): %s "
        "(%d of %d pairs matched)",
        line_set.name,
        game,
        split.name,
        comparer.method.value,
        "no threshold"
        if comparer.threshold is None
        else f"threshold {comparer.threshold:.4f}",
        _summarise(lines),
        stats.matches,
        stats.comparisons,
    )
    return PaylineCheckResult(
        game=game,
        set=line_set.name,
        method=comparer.method,
        threshold=comparer.threshold,
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
    """Evaluate a caller-supplied set of lines against one split reel grid.

    The public door for lines that did not come from the active game's
    ``paylines`` block. The active game still supplies the grid geometry
    (``reel_bounds``), because that is where the tiles are, not which patterns
    pay. Compares by cosine similarity: see :func:`check_symbols` for the same
    door read by a classifier's codes instead.
    """
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
    """Evaluate a set of lines against symbol codes already read off the tiles.

    The classifier's door, and how :mod:`app.services.analyze_spin` reads a spin:
    ``symbols`` maps a tile position (``r1c1``) to the code a classifier named it
    with, or to ``None`` for one it was not sure enough of. Two tiles match when
    their codes are equal, so a run is *named* as well as counted -- which is what
    lets the caller price it against one paytable row instead of every row that
    pays at that length.

    Nothing here reads a threshold, and the result's ``threshold`` comes back
    null: two codes are equal or they are not. Everything else about the answer
    -- the lines, the drawings, the files under the split's own directory -- is
    what :func:`check_lines` produces, because both go through
    :func:`_evaluate_set`.
    """
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
