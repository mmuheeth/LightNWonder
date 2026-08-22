"""Checking a split reel grid against the patterns that pay.

One step past :mod:`app.services.grid`, and the step it takes is the reason the
grid writes its tiles down. ROI answers *is this rectangle aimed at the right
part of the screen*; grid answers *and does it divide into the symbols I expect*;
this answers *and do those symbols line up*. Each reads the previous one's
output rather than redoing it.

**The input is a written split, not a screenshot.** Nothing here resolves a
frame, finds a content box or crops a region -- it opens a directory the grid
already wrote, which is what makes a check repeatable: the same split checked
twice at the same threshold gives the same answer, and re-checking after
adjusting the threshold does not depend on the simulator still showing the same
spin. Which split, and what is in it, is
:func:`app.services.grid.resolve_split` and :func:`app.services.grid.read_split`
-- that module named the directory and the files, so it is the one that reads
them back.

**Two symbols are "the same" by cosine similarity, and the threshold is the whole
game.** The same pot of gold on two reels is never the same pixels: a slot game
glows, pulses and scales its symbols continuously, so an exact comparison says
"different" for every pair and a naive difference says "different" for a bright
frame. Cosine similarity treats brightness as vector length rather than
direction, which is exactly the invariance wanted -- see
:mod:`app.utils.similarity`, which also explains why the scores cluster high.
The cut between the clusters is per-game, so every result carries the
distribution it measured -- ``matched_min`` and ``rejected_max`` -- so
``PAYLINE_MATCH_THRESHOLD`` can be tuned from evidence rather than intuition.

**A line is read from the left, one adjacent pair at a time, and stops at the
first pair that does not match.** That is how a slot pays: three pots on reels
1-3 pay whatever a pot pays for three, and a fourth pot on reel 5 with something
else on reel 4 pays nothing extra. So the pay count is the length of the *leading*
run of like symbols -- 0 if reels 1 and 2 differ, otherwise 2 or more. The
comparison chains, tile to tile rather than every tile back to the first: A~B and
B~C is taken as A~B~C, which is what makes a slow drift across five reels
possible in principle and is worth knowing when a run looks one longer than it
should.

**Every adjacent pair is scored, including the ones after the run broke.** They
cost nothing -- the pair is compared once and cached across the lines that share
it -- and a line that pays 2 is much easier to trust when the two scores that
follow are visibly low. The verdict uses only the counted ones; the rest are
evidence.

**The picture is part of the answer, and there are two kinds of it.** A pay
count cannot be checked by reading it, so the reels come back once as a combined
overlay with every *paying* line drawn on it, written to disk beside the tiles
it was computed from -- and once more per line, paying or not, as
``image_data`` on that line's own entry, so a line that pays nothing still shows
where its run stopped. Only the second kind carries the red break ring: the
combined picture is several lines at once, and "here is where this one broke" is
a question about one of them, which is what its own picture is for. The palette
is :mod:`app.utils.payline_overlay`'s, and each line's colour travels in the
result so a swatch in the panel matches the drawing.

Like :mod:`app.services.roi` and :mod:`app.services.grid` this service holds no
state -- no client, no lock, no cache -- so it has no ``reset()`` and
``tests/conftest.py`` has nothing to clean up between tests. Callers use the
namespace::

    from app.services import paylines as paylines_service
    await paylines_service.check(PaylineCheckRequest())
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

# Where an annotated picture lands inside the split's own directory. Beside the
# tiles it was computed from rather than in a directory of its own, because the
# three -- crop, tiles, overlay -- are one record of one frame.
_OUTPUT_DIR = "paylines"

# The fewest positions a line has to match to have paid anything. Two, not
# three: what pays what is the game's paytable and not this service's business,
# and reporting a two-reel run as "pays 2" leaves the paytable to whoever has it.
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
    """The bet configurations the game declares.

    Raises:
        PaylinesNotConfiguredError: if it declares none.
        GameConfigInvalidError: if the block is not an object.
    """
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
    """Which set a request that names none is asking for.

    ``PAYLINE_DEFAULT_SET`` when the game declares it, and otherwise the first --
    which :func:`app.utils.paylines.set_names` has already sorted numerically, so
    it is the five-line set rather than whichever key JSON happened to list
    first. A configured set the game does not have falls back rather than
    failing: the setting is deployment-wide and the games are not all the same.
    """
    wanted = settings.PAYLINE_DEFAULT_SET.strip()
    return wanted if wanted in names else names[0]


def _read_set(config: GameConfig, name: str) -> payline_config.PaylineSet:
    """Parse one bet configuration out of the active game's block.

    Raises:
        PaylinesNotConfiguredError: if the game declares no set by that name.
        GameConfigInvalidError: if the set it declares is malformed.
    """
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
    """The reel grid of the active game, for the tile geometry the lines need.

    Read here as well as in :mod:`app.services.grid` because the overlay draws on
    the crop and needs to know where a tile's centre is inside it, which the
    filenames of a written split cannot say. Reused rather than re-derived: the
    ``col_bounds`` gaps are where the reel strips actually are, so a centre taken
    from them lands on the symbol and one taken from an even division does not.

    Raises:
        GridNotConfiguredError: if the game declares no ``reel_bounds``.
        GameConfigInvalidError: if it declares one that cannot be read.
    """
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
    """Every tile as a flat vector, converted once.

    Once per tile rather than once per comparison: a forty-line set compares each
    pair several times over, and converting fifteen pictures beats converting a
    hundred and sixty.
    """
    return {name: similarity.vector(tile) for name, tile in split.tiles.items()}


class _Scores:
    """Cosine similarity between tiles of one split, computed on demand.

    Cached on the unordered pair, because cosine is symmetric and the sets share
    their lines: every one of FortuneOx's three sets starts with the same middle
    row, and the diagonals overlap heavily. Forty lines of four steps is a
    hundred and sixty questions about at most a hundred and five distinct pairs.
    """

    def __init__(self, vectors: dict[str, np.ndarray]) -> None:
        self._vectors = vectors
        self._cache: dict[tuple[str, str], float] = {}

    def between(self, left: str, right: str) -> float:
        """How alike the two named tiles are.

        Raises:
            PaylineCheckFailedError: if either name is not a tile of the split.
        """
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

    Every step is scored; ``counted`` marks the ones the left-to-right read
    actually reached. The run ends at the first counted step that does not match,
    so the pay count is ``matched adjacent pairs + 1`` while they hold and 0 once
    the first pair fails. The break position is that step's right-hand tile --
    the first one that did not continue the match -- or None when the whole line
    paid and nothing broke.
    """
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
    # A run of matched pairs covers one more position than it has pairs, and no
    # matched pair at all covers none: a line that fails on reels 1 and 2 pays
    # nothing, not one.
    pays = run + 1 if run else 0
    return steps, pays, break_position


# --- drawing and writing --------------------------------------------------


def _placed_tiles(
    grid: reel_grid.ReelGrid, split: grid_service.SplitOnDisk
) -> dict[str, reel_grid.PlacedTile]:
    """Every tile of the grid, positioned on this split's own crop.

    From :meth:`app.utils.reel_grid.ReelGrid.place` against the crop that was
    written, so a tile's box here is the exact box it was cut out with -- the
    overlay's borders line up with the symbol they are drawn around rather than
    with an independently-rounded approximation of it.

    Raises:
        PaylineCheckFailedError: if the crop leaves no room for tiles.
    """
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
    """One line, as the overlay wants it -- paying or not.

    ``points`` is the confirmed run and nothing more: empty when ``pays`` is 0,
    because a line whose very first pair failed confirmed nothing. The tile
    boxes that go with it -- green for the confirmed run, red for the one that
    ended it -- are what a line's own picture borders; nothing past a break is
    bordered, and the combined overlay ignores all of it via ``detailed=False``.
    No text is drawn on either picture -- the line's name is already on the
    response, in ``PaylineLine.label``, and the picture is the path.
    """
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
    """Write the annotated picture into the split's own directory.

    Raises:
        PaylineCheckFailedError: if it could not be written.
    """
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
    """One picture as a PNG data URI, as this feature's own failure.

    Translated rather than reimplemented, the same way :mod:`app.services.grid`
    translates it: the encoding belongs in one place and ROI's error name would
    read as the wrong feature here.
    """
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
    """The result as the sentence a person would say.

    ``Line 1 pays 2, Line 3 pays 4`` -- paying lines only, in the order they are
    numbered, because that is the order they are read in.
    """
    paying = [f"{line.label} pays {line.pays}" for line in lines if line.paying]
    return ", ".join(paying) if paying else "No line pays"


def _stats(lines: list[PaylineLine], scores: _Scores, threshold: float) -> PaylineStats:
    """The run as a whole, including how well its scores separated.

    ``matched_min`` and ``rejected_max`` are the two numbers a working threshold
    sits between. Both are counted over the *distinct* pairs rather than over the
    steps, so a pair two lines share is not weighted twice.
    """
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
        # Reported rather than raised, like the grid panel's own unconfigured
        # state: a game without the block is a choice someone made, not a
        # request that went wrong.
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
            # A half-written or hand-edited directory is a state to report, not a
            # failure: the panel's job here is to say why Check is disabled.
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
    """The sets the active game declares, and the split a check would read.

    What the panel renders before anything is checked. A game that declares no
    paylines, or no reel grid to place them on, and a checkout that has split
    nothing yet, all come back with ``error`` set on a 200 rather than as failed
    requests.

    Raises:
        GameConfigInvalidError: if the active game's config cannot be loaded, or
            declares a ``paylines`` or ``reel_bounds`` block that cannot be read.
    """
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
        # The split and the config disagree about the shape of the reels, so the
        # coordinates in the payline block do not describe these tiles. A
        # conflict rather than a 500: the config may well be right and the split
        # merely old, which is fixed by splitting again.
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
            # Just the path on the combined picture: no tags, no tile borders,
            # no break -- several lines share it, and one line's own picture
            # (below) is where all of that belongs.
            paying_drawings.append(dataclasses.replace(drawing, detailed=False))

        # Every line gets its own picture -- unlike the combined overlay, which
        # only draws the ones that paid -- because a line that pays nothing is
        # exactly the one whose break is worth seeing on its own.
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
    """Evaluate one bet configuration's lines against one split reel grid.

    Raises:
        BadRequestError: if a named split is not a bare directory name.
        PaylinesNotConfiguredError: if the active game declares no ``paylines``,
            or no set by the requested name.
        GridNotConfiguredError: if it declares no ``reel_bounds`` to place them
            on.
        GameConfigInvalidError: if the config cannot be loaded, or either block
            is declared with unusable numbers.
        PaylineSourceNotFoundError: if there is no split to check, or the one
            named is incomplete.
        PaylineSourceStaleError: if the split's shape is not the one the game now
            declares.
        RoiExtractFailedError: if the crop or a tile is not a readable image.
        PaylineCheckFailedError: if a line runs through a tile the split does not
            hold, or the annotated picture could not be written or encoded.
    """
    return await asyncio.to_thread(_check, request)
