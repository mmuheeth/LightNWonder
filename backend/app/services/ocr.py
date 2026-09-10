"""Reads the game's on-screen text with Tesseract, and the number printed on a
symbol orb with PaddleOCR -- see :func:`read_tile` for why that one crop uses a
different engine."""

from __future__ import annotations

import asyncio
import base64
import binascii
import dataclasses
import io
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.config.ocr import candidate_executables
from app.config.runtime import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    BadRequestError,
    GameConfigInvalidError,
    OcrEngineUnavailableError,
    OcrReadFailedError,
    OcrRegionNotFoundError,
)
from app.schemas.obs import ScreenshotRequest
from app.schemas.ocr import (
    OcrEngineState,
    OcrOptionOverrides,
    OcrReading,
    OcrReadRequest,
    OcrReadResult,
    OcrRegion,
    OcrRegionCatalog,
    OcrSource,
    OcrStatus,
    OcrWordBox,
)
from app.schemas.ocr import OcrOptions as OcrOptionsPayload
from app.services import event_capture as capture_service
from app.services import obs as obs_service
from app.services import roi as roi_service
from app.utils import image_roi, letterbox, ocr, paddle_ocr

logger = get_logger("ocr")

# The `ocr` block key a game overrides symbol-tile reading under. Not a region in
# `roi` -- a tile is cut by the reel grid, not by a named screen rectangle -- so
# it shares that block's shape without appearing in the region catalogue.
_TILE_REGION = "symbol_tile"


@dataclass(frozen=True)
class _Engine:
    """A Tesseract install that has been found and made to speak."""

    executable: Path
    version: str
    languages: tuple[str, ...]


_engine: _Engine | None = None


def reset() -> None:
    """Drop the cached engine. Tests, and after a settings change."""
    global _engine
    _engine = None


# --- the engine -----------------------------------------------------------


def _executable() -> Path:
    """The engine to run; raises ``OcrEngineUnavailableError`` (a 409, not a
    retryable failure) if OCR is disabled or no Tesseract was found."""
    if not settings.OCR_ENABLED:
        raise OcrEngineUnavailableError(
            "OCR is disabled; set OCR_ENABLED=true to turn it on"
        )
    executable = settings.ocr_tesseract_cmd
    if executable is None:
        looked = ", ".join(str(path) for path in candidate_executables())
        raise OcrEngineUnavailableError(
            "No Tesseract executable was found. Install Tesseract OCR or set "
            f"OCR_TESSERACT_CMD to where it is. Looked in: {looked or 'PATH'}"
        )
    if not executable.is_file():
        raise OcrEngineUnavailableError(
            f"OCR_TESSERACT_CMD points at {executable}, which is not a file"
        )
    return executable


def _identify(executable: Path) -> _Engine:
    """Ask the engine what it is, and remember the answer."""
    global _engine
    cached = _engine
    if cached is not None and cached.executable == executable:
        return cached
    engine = _Engine(
        executable=executable,
        version=ocr.engine_version(executable),
        languages=ocr.engine_languages(executable),
    )
    _engine = engine
    logger.info("OCR engine: %s (%s)", engine.version, engine.executable)
    return engine


async def engine_for_reading() -> Path:
    """The engine, identified once so a broken install fails before any cropping.

    Public because this module owns the question of *which* Tesseract, the way
    ``roi.py`` owns "the latest screenshot" -- a second caller resolving it
    again could disagree about whether OCR is available at all.
    """
    executable = _executable()
    try:
        return (await asyncio.to_thread(_identify, executable)).executable
    except ocr.OcrError as exc:
        raise OcrEngineUnavailableError(
            f"The Tesseract install at {executable} is not usable: {exc}"
        ) from exc


# --- options --------------------------------------------------------------


def _defaults() -> ocr.OcrOptions:
    """The options every read starts from, straight out of the environment."""
    return ocr.OcrOptions(
        language=settings.OCR_LANGUAGE,
        psm=settings.OCR_PSM,
        oem=settings.OCR_OEM,
        char_whitelist=settings.OCR_CHAR_WHITELIST,
        upscale=settings.OCR_UPSCALE,
        grayscale=settings.OCR_GRAYSCALE,
        autocontrast=settings.OCR_AUTOCONTRAST,
        invert=settings.OCR_INVERT,
        threshold=settings.OCR_THRESHOLD,
        dpi=settings.OCR_DPI,
        timeout_seconds=settings.OCR_TIMEOUT_SECONDS,
        tessdata_dir=settings.ocr_tessdata_dir,
    )


def _request_overrides(overrides: OcrOptionOverrides | None) -> Mapping[str, Any]:
    """The options a request asked to change. ``exclude_unset``, not
    ``exclude_none`` -- ``threshold: null`` means turn thresholding off."""
    if overrides is None:
        return {}
    return overrides.model_dump(exclude_unset=True)


def options_for(
    config: GameConfig, region: str, overrides: OcrOptionOverrides | None = None
) -> ocr.OcrOptions:
    """Resolve the options one region is read with: environment, then the game
    config's ``ocr`` block, then the request.

    Public for the same reason as :func:`engine_for_reading`: the precedence is
    this module's to state, and a caller rebuilding it would be a second answer.
    """
    resolved = _defaults().merged(config.ocr.get(region), where=f"ocr.{region}")
    try:
        return resolved.merged(_request_overrides(overrides), where="options")
    except ocr.OcrOptionsError as exc:
        raise BadRequestError(str(exc)) from exc


def _as_payload(options: ocr.OcrOptions) -> OcrOptionsPayload:
    """The options a reading was taken with, as the API reports them."""
    return OcrOptionsPayload(
        language=options.language,
        psm=options.psm,
        oem=options.oem,
        char_whitelist=options.char_whitelist,
        upscale=options.upscale,
        grayscale=options.grayscale,
        autocontrast=options.autocontrast,
        invert=options.invert,
        threshold=options.threshold,
        dpi=options.dpi,
    )


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


def _requested_regions(
    config: GameConfig, requested: Sequence[str] | None
) -> list[str]:
    """Which regions to read, checked against the ones the game declares."""
    if not config.roi:
        raise OcrRegionNotFoundError(
            f"The game config for {config.name!r} declares no 'roi' regions to read"
        )
    if requested is None:
        return sorted(config.roi)

    known = ", ".join(sorted(config.roi))
    missing = [name for name in requested if name not in config.roi]
    if missing:
        raise OcrRegionNotFoundError(
            f"The game config for {config.name!r} has no region named "
            f"{', '.join(repr(name) for name in missing)} "
            f"(configured regions: {known})"
        )
    if not requested:
        raise BadRequestError("Name at least one region to read, or omit 'regions'")
    return list(requested)


# --- the frame ------------------------------------------------------------


async def _live_frame() -> Image.Image:
    """A screenshot taken from OBS now, decoded in memory -- never written to
    disk, since an OCR frame isn't evidence."""
    await obs_service.connect()
    result = await obs_service.take_screenshot(
        ScreenshotRequest(image_format="png", width=settings.OCR_SCREENSHOT_WIDTH)
    )
    if not result.image_data:
        raise OcrReadFailedError("OBS returned a screenshot with no image data")
    return _decode(result.image_data)


def _decode(data_uri: str) -> Image.Image:
    """Turn OBS's base64 data URI into an open image."""
    _, _, encoded = data_uri.rpartition(",")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OcrReadFailedError(
            f"The screenshot OBS returned could not be decoded: {exc}"
        ) from exc
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except OSError as exc:
        raise OcrReadFailedError(
            f"The screenshot OBS returned is not a readable image: {exc}"
        ) from exc
    return image


def _run_frame(run_id: str, file_name: str) -> Image.Image:
    """One screenshot from a capture run, opened and closed. Path guards live in
    the capture service, the only place that turns a run id/filename into a file."""
    path = capture_service.screenshot_path(run_id, file_name)
    try:
        with Image.open(path) as image:
            image.load()
            return image.copy()
    except OSError as exc:
        raise OcrReadFailedError(
            f"{path} could not be read as an image: {exc}"
        ) from exc


# --- reading --------------------------------------------------------------


def _read_region(
    frame: Image.Image,
    region: str,
    regions: Mapping[str, Any],
    *,
    content: letterbox.ContentBox,
    executable: Path,
    options: ocr.OcrOptions,
    include_crop: bool,
) -> OcrReading:
    """Read one region off a frame, reporting a failure rather than raising it.
    ``content`` is found once by the caller, not per region."""
    started = time.perf_counter()
    reading = OcrReading(region=region, options=_as_payload(options))
    try:
        roi = image_roi.named_roi(regions, region)
        box = roi.to_box_within(content.box)
        crop = frame.crop(box)
        result = ocr.read_image(crop, executable=executable, options=options)
    except image_roi.RoiError as exc:
        # Reported on the reading it spoils, not failing every other region.
        return reading.model_copy(
            update={
                "error": str(exc),
                "duration_ms": _elapsed_ms(started),
            }
        )
    except ocr.OcrError as exc:
        return reading.model_copy(
            update={"error": str(exc), "duration_ms": _elapsed_ms(started)}
        )

    numbers = [float(number) for number in result.numbers]
    return reading.model_copy(
        update={
            "text": result.text,
            "value": numbers[0] if numbers else None,
            "values": numbers,
            "confidence": result.confidence,
            "words": [
                OcrWordBox(
                    text=word.text,
                    confidence=word.confidence,
                    left=word.left,
                    top=word.top,
                    width=word.width,
                    height=word.height,
                )
                for word in result.words
            ],
            "crop": list(box),
            "crop_image": _crop_data_uri(crop, options) if include_crop else None,
            "duration_ms": _elapsed_ms(started),
        }
    )


# Options a symbol tile is read with, over the top of the environment's. A tile
# is not a meter strip, and the difference is `psm`: the figure on a prize tile is
# one run of large digits, so **8 ("a single word") is what reads it** -- measured
# against the written FortuneOx orbs, where 8 reads 600/300/240/150/100/50 with no
# thresholding at all and holds across every upscale. `psm` 11 ("sparse text"),
# the intuitive choice for digits floating over artwork, reads most of those same
# tiles as *nothing*; don't "improve" this back to it. The whitelist is digits
# only -- no `.` or `,`, which the engine otherwise offers for the filigree around
# the number -- and the upscale is larger because a tile is a hundred-odd pixels
# wide. No `threshold`: gold digits on a light orb have no single grey level that
# separates them, and 8 does not need one.
#
# `upscale` is 1.0 because :func:`read_tile` sizes each crop itself: a tile is
# 88..151px tall depending on the split, so a fixed multiplier puts the glyphs at
# a different size for every game and a thin leading `1` is resampled away.
_TILE_OPTIONS: Mapping[str, Any] = {
    "psm": 8,
    "char_whitelist": "0123456789",
    "upscale": 1.0,
    "grayscale": True,
    "autocontrast": False,
}

# The fewest digits a real prize figure is drawn with. `psm` 8 is told the crop
# *is* a word, so it answers with one even for a tile carrying no number, and this
# is what tells the two apart.
#
# **Not a confidence floor, which cannot work here.** Measured over every written
# split: all 16 misreads are a *single* digit (`1`, `7`, `3`, `0`, `4`) and all 21
# genuine figures are two or three (50 … 600) -- while the confidences of the two
# groups overlap outright, a stray `1` scoring 40.0 against a true `150` at 17.8
# and a true `100` at 19.1. So any floor rejects real prizes and admits noise;
# digit count separates the same sample perfectly. A prize is never one digit.
TILE_MIN_DIGITS = 2

# --- reading the figure off a prize tile ----------------------------------
#
# A tile is not a region of a screen: it is a *decorated* picture with a number
# drawn in the middle of it, and reading it with one crop and one scale is what
# made this unreliable. Measured over the 74 written FortuneOx scatter tiles,
# handing the whole tile to Tesseract at a fixed 4x reads 53 of them (71%) --
# the other 21 come back either blank or, once, as a wrong figure. The three
# things that cost those readings, each confirmed against the written splits:
#
# 1. **No quiet zone.** Tesseract segments glyphs against blank margin, and the
#    tile crop runs to the orb's edge. Padding alone recovers a whole run of
#    tiles that otherwise read as nothing.
# 2. **The filigree.** The gold flourishes around the orb are as dark as the
#    digits, so a crop that includes them offers the engine glyph-shaped noise:
#    a spike on the left becomes a leading `1` (`150` read as `1150`), and a
#    contaminated crop will do this *confidently*.
# 3. **One scale.** A thin `1` survives at one glyph height and vanishes at
#    another, which is what turns `1250` into `250`.
#
# So each tile is read several ways and the readings are weighed against each
# other. Measured the same way, this reads 73 of the 74 (98%) with no wrong
# figure at all -- the one it misses is a tile the grid split left two orbs in,
# which no OCR setting can fix.
#
# Every variant is a Tesseract *subprocess*, so the number of them is a latency
# decision as much as an accuracy one: eight of them read one tile in ~3.5s
# against the ~0.5s of the single crop this replaces. `_TILE_INK_LEVELS` and
# `_TILE_INSETS` are the smallest sets that still read all 73 -- trimming further
# starts inventing figures, and the sizes either side of them are recorded there.
#
# The tiles of a grid are read concurrently rather than one after another (see
# `_read_scatters`), which is what makes that per-tile cost bearable: measured on
# the five-orb split of run 11-39-23, sequential is 17.8s and concurrent is 7.1s
# for the same five readings. Pull that lever before cutting variants.

# Crops cut to the digit ink itself (see `_ink_band`) are weighed above crops cut
# to a fixed rectangle, which still hold filigree. This is the difference between
# reading a `300` and reading a `500`: on three written tiles the ink-cut crops
# agreed on `300` eight times over while one fixed crop said `500` slightly more
# confidently, and weighing them equally is what let the wrong answer win.
_BAND_WEIGHT = 2.0
_FIX_WEIGHT = 1.0

# A candidate below this contributes no support to its reading. **This is not the
# confidence floor that `TILE_MIN_DIGITS` documents as unworkable**: it does not
# reject a reading for scoring low, it stops a low read from being *evidence* for
# one. A tile carrying no figure is read as a 0.0-confidence `58`, and without
# this that scrap becomes a prize.
_MIN_PEAK_CONFIDENCE = 25.0

# The weighted support a reading needs before it is believed, i.e. more than one
# ink-cut crop or two agreeing fixed ones. Below it the tile reads as nothing:
# a visible gap in the run record is worth more than a wrong prize figure in it.
_MIN_SUPPORT = 1.5

# Glyph heights each crop is read at, and the white margin put around it.
_TILE_TARGET_HEIGHTS: tuple[int, ...] = (190, 300)
_TILE_PAD = 30

# Fractional inset for the fallback crop -- the one to reach for when the ink
# search finds nothing, which is a tile whose digits do not separate from the orb
# at any of the levels below.
#
# **One, not a family of four.** Every variant is a Tesseract subprocess, and the
# cost is what decides how many are worth keeping. Measured over the 74 written
# tiles: four insets and four ink levels read 73 of them at ~6.1s a tile, and the
# three ink levels with this single inset read *the same 73* at ~2.9s. The other
# three insets change no answer and cost half the time -- they are crops that
# still hold filigree, so where they disagree with the ink-cut crops they are
# usually the ones that are wrong (see `_BAND_WEIGHT`).
_TILE_INSETS: tuple[tuple[float, float], ...] = ((0.72, 0.46),)

# Grey levels the digit ink is looked for at. Several, because the digits are
# drawn with a metallic gradient: no single level separates them on every tile.
#
# Three, again by measurement: dropping to two costs two readings *and* admits
# two wrong figures for ~40ms a tile, which is the wrong trade in both
# directions. The fourth level (160) changed no answer.
_TILE_INK_LEVELS: tuple[int, ...] = (70, 100, 130)


def _flattened(tile: Image.Image) -> Image.Image:
    """``tile`` over white, so a transparent split has a background rather than
    whatever Pillow leaves under the alpha channel."""
    if tile.mode == "RGBA":
        flat = Image.new("RGB", tile.size, (255, 255, 255))
        flat.paste(tile, mask=tile.getchannel("A"))
        return flat
    return tile if tile.mode in ("RGB", "L") else tile.convert("RGB")


def _ink_band(grey: Image.Image, ink_level: int) -> tuple[int, int, int, int] | None:
    """The box around the digits, grown outward from the middle of the tile.

    Outward from the centre rather than over the whole tile: the filigree is as
    dark as the digits, so scanning everything finds a box that includes it. The
    decoration is separated from the number by clear orb, so a walk that stops at
    the first run of empty columns stops before reaching it.
    """
    width, height = grey.size
    pixel = grey.load()
    top, bottom = int(height * 0.22), int(height * 0.78)
    left_limit, right_limit = int(width * 0.10), int(width * 0.90)
    if bottom <= top or right_limit <= left_limit:
        return None

    def column_ink(x: int) -> int:
        return sum(1 for y in range(top, bottom) if pixel[x, y] <= ink_level)

    needed = max(1, (bottom - top) // 12)
    # A digit is not solid to its neighbour, so a short empty run is the gap
    # between two of them rather than the end of the number.
    allowed_gap = width * 0.06
    centre = width // 2

    def walk(step: int, stop: int) -> int:
        edge = centre
        gap = 0
        for x in range(centre, stop, step):
            if column_ink(x) >= needed:
                edge, gap = x, 0
            else:
                gap += 1
                if gap > allowed_gap:
                    break
        return edge

    left = walk(-1, left_limit - 1)
    right = walk(1, right_limit)
    if right - left < 8:
        return None

    needed_row = max(1, (right - left) // 12)
    rows = [
        y
        for y in range(top, bottom)
        if sum(1 for x in range(left, right + 1) if pixel[x, y] <= ink_level)
        >= needed_row
    ]
    if not rows:
        return None
    margin = 6
    return (
        max(0, left - margin),
        max(0, min(rows) - margin),
        min(width, right + 1 + margin),
        min(height, max(rows) + 1 + margin),
    )


def _tile_variants(tile: Image.Image) -> list[tuple[Image.Image, float]]:
    """The pictures of ``tile`` to read, each with the weight its reading carries.

    Deduplicated by crop box: when the ink search hits the tile edge every level
    returns the same box, and counting one picture four times is what let a
    phantom leading `1` outvote the correct reading.
    """
    grey = _flattened(tile).convert("L")
    seen: set[tuple[int, int, int, int]] = set()
    variants: list[tuple[Image.Image, float]] = []

    for ink_level in _TILE_INK_LEVELS:
        box = _ink_band(grey, ink_level)
        if box is not None and box not in seen:
            seen.add(box)
            variants.append((grey.crop(box), _BAND_WEIGHT))

    width, height = grey.size
    for horizontal, vertical in _TILE_INSETS:
        box = (
            int(width * (1 - horizontal) / 2),
            int(height * (1 - vertical) / 2),
            int(width * (1 + horizontal) / 2),
            int(height * (1 + vertical) / 2),
        )
        if box not in seen:
            seen.add(box)
            variants.append((grey.crop(box), _FIX_WEIGHT))
    return variants


def _prepared_variant(crop: Image.Image, target_height: int) -> Image.Image:
    """One crop at the glyph height the engine is given it at, padded with white."""
    scale = max(1.0, target_height / max(1, crop.height))
    resized = crop.resize(
        (round(crop.width * scale), round(crop.height * scale)),
        Image.Resampling.LANCZOS,
    )
    return ImageOps.expand(
        ImageOps.autocontrast(resized), border=_TILE_PAD, fill=255
    )


@dataclass(frozen=True)
class TileReading:
    """What a prize tile read as, and how sure the reading is.

    ``text`` is ``None`` when nothing was read well enough to believe -- which is
    both a tile carrying no figure and a tile whose figure could not be made out,
    deliberately not told apart: neither is a prize.
    """

    text: str | None
    confidence: float | None
    candidates: tuple[tuple[str, float, float], ...]
    """Every reading taken, as ``(text, confidence, weight)`` -- kept so a
    surprising answer can be explained without re-running the read."""

    label: str | None = None
    """The jackpot tier printed on the orb where it carries a word instead of a
    figure -- 'MAJOR', 'MINI', whatever the game draws. Set only by the Paddle
    reader: the Tesseract fallback is given a digits-only whitelist, so it cannot
    see a word to report. Never set at the same time as ``text``."""


def _paddle_options() -> paddle_ocr.PaddleOptions:
    """The options an orb is read with, straight out of the environment."""
    return paddle_ocr.PaddleOptions(
        language=settings.OCR_ORB_PADDLE_LANGUAGE,
        upscale=settings.OCR_ORB_PADDLE_UPSCALE,
        min_confidence=settings.OCR_ORB_PADDLE_MIN_CONFIDENCE,
        min_digits=TILE_MIN_DIGITS,
        timeout_seconds=settings.OCR_ORB_PADDLE_TIMEOUT_SECONDS,
    )


def orb_engine_is_paddle() -> bool:
    """Whether an orb will be read by PaddleOCR rather than Tesseract. Public so
    a caller can say which engine read a spin's orbs: Tesseract is identified
    before the orb loop either way, for the fallback, so its own startup line
    otherwise reads as if it had been the one to answer."""
    return settings.OCR_ORB_PADDLE_ENABLED and paddle_ocr.available()


def _read_tile_paddle(
    tile: Image.Image, *, min_digits: int
) -> TileReading | None:
    """Read the figure on one orb with PaddleOCR, or ``None`` when Paddle cannot
    answer and Tesseract should be asked instead.

    ``None`` is returned only when the *engine* is unavailable or errored -- not
    when it ran and found no number. An orb with no figure on it is a real
    reading of "nothing" (a feature scatter is drawn without a prize), and
    falling back to the Tesseract voting reader for those would reintroduce
    exactly the invented digits that reader has to weigh away.
    """
    options = _paddle_options()
    try:
        value, label, result = paddle_ocr.read_prize(
            tile, options=dataclasses.replace(options, min_digits=min_digits)
        )
    except ocr.OcrUnavailableError as exc:
        # Not installed: one log line, then Tesseract. Debug rather than warning
        # because a host that never installed Paddle would otherwise say this
        # once per orb, forever.
        logger.debug("PaddleOCR is unavailable, falling back to Tesseract: %s", exc)
        return None
    except ocr.OcrError as exc:
        logger.warning("PaddleOCR could not read an orb, falling back: %s", exc)
        return None

    candidates = tuple(
        # Scaled to Tesseract's 0-100 so `TileReading.confidence` means one
        # thing regardless of which engine filled it in.
        (word.text, word.confidence * 100.0, _BAND_WEIGHT)
        for word in result.words
    )
    if value is None:
        # A word rather than a figure: a jackpot orb says MAJOR/MINI/GRAND where
        # a prize orb says a number, and that is a reading, not a failure.
        if label is not None:
            logger.debug("PaddleOCR read the jackpot tier %s on an orb", label)
            return TileReading(
                text=None,
                confidence=(result.confidence or 0.0) * 100.0,
                candidates=candidates,
                label=label,
            )
        # Which engine answered is not obvious from the outside -- Tesseract is
        # still identified before the orb loop, for the fallback -- so the reader
        # says so itself. Debug, not info: fifteen tiles a spin.
        logger.debug(
            "PaddleOCR read no figure on an orb (candidates: %s)",
            ", ".join(f"{text} @{confidence:.1f}" for text, confidence, _ in candidates)
            or "none",
        )
        return TileReading(text=None, confidence=None, candidates=candidates)
    # `str(Decimal)` so a figure keeps the digits it was read with.
    digits = "".join(character for character in str(value) if character.isdigit())
    confidence = (result.confidence or 0.0) * 100.0
    logger.debug("PaddleOCR read %s on an orb at %.1f", digits, confidence)
    return TileReading(
        text=digits,
        confidence=confidence,
        candidates=candidates,
    )


def read_tile(
    tile: Image.Image,
    *,
    executable: Path,
    options: ocr.OcrOptions,
    min_digits: int = TILE_MIN_DIGITS,
) -> TileReading:
    """Read the prize figure printed on one symbol tile.

    PaddleOCR reads the orb when it is installed and ``OCR_ORB_PADDLE_ENABLED``
    is on: it is the one crop in this service Tesseract measurably cannot manage,
    reading ``160`` at 0.9998 off a tile Tesseract returns nothing for. **Only the
    orb moved** -- meters, named regions and whole frames are still Tesseract's,
    which is why this function keeps its ``executable`` and ``options``.

    Without Paddle it falls back to reading the tile several ways with Tesseract
    -- cut to the digit ink at a few grey levels, cut to fixed insets, each at a
    couple of glyph heights -- and returns the reading the evidence supports, or
    nothing when it supports none. See the block comment above for why one crop
    is not enough there.
    """
    if settings.OCR_ORB_PADDLE_ENABLED:
        reading = _read_tile_paddle(tile, min_digits=min_digits)
        if reading is not None:
            return reading

    candidates: list[tuple[str, float, float]] = []
    for crop, weight in _tile_variants(tile):
        for target_height in _TILE_TARGET_HEIGHTS:
            prepared = _prepared_variant(crop, target_height)
            try:
                result = ocr.read_image(
                    prepared, executable=executable, options=options
                )
            except ocr.OcrError:
                # One variant failing is not the tile failing; the others still
                # have something to say.
                continue
            digits = "".join(
                character for character in result.text if character.isdigit()
            )
            if len(digits) >= min_digits:
                candidates.append((digits, result.confidence or 0.0, weight))

    if not candidates:
        return TileReading(text=None, confidence=None, candidates=())

    support: dict[str, float] = {}
    weighted: dict[str, float] = {}
    peak: dict[str, float] = {}
    for text, confidence, weight in candidates:
        # Confidence gates the support a crop lends, so agreement between
        # readings none of which was any good stays worth nothing.
        support[text] = support.get(text, 0.0) + (
            weight if confidence >= _MIN_PEAK_CONFIDENCE else 0.0
        )
        weighted[text] = weighted.get(text, 0.0) + weight * confidence
        peak[text] = max(peak.get(text, 0.0), confidence)

    best = max(support, key=lambda text: (support[text], weighted[text]))
    # Strongest first, so a caller reporting a refused reading names the figure
    # that came closest rather than whichever variant happened to run first.
    ranked = tuple(
        sorted(candidates, key=lambda entry: entry[1], reverse=True)
    )
    if peak[best] < _MIN_PEAK_CONFIDENCE or support[best] < _MIN_SUPPORT:
        return TileReading(text=None, confidence=None, candidates=ranked)
    return TileReading(text=best, confidence=peak[best], candidates=ranked)



def read_tile_options(config: GameConfig, region: str = _TILE_REGION) -> ocr.OcrOptions:
    """The options a symbol tile is read with: the environment's, then the tile
    defaults above, then whatever the game config's ``ocr`` block says for
    ``region`` -- so a game whose orbs are drawn dark can say so without a code
    change, exactly as it can for a named screen region."""
    base = _defaults().merged(_TILE_OPTIONS, where="ocr.tile defaults")
    return base.merged(config.ocr.get(region), where=f"ocr.{region}")


async def engine_for_reading() -> Path:
    """The Tesseract executable, identified once. Public because a caller reading
    many crops wants to fail before the first of them rather than per crop."""
    return await _engine_for_reading()


def read_crop(
    crop: Image.Image,
    *,
    executable: Path,
    options: ocr.OcrOptions,
) -> ocr.OcrResult:
    """Read one already-cut picture. The whole of what this module adds over
    :func:`app.utils.ocr.read_image` is resolving the engine and the options,
    which the two helpers above do -- so a caller that holds its own crop (a
    symbol tile, not a named region of a frame) has a door in that does not make
    it re-derive either. Raises :class:`app.utils.ocr.OcrError`."""
    return ocr.read_image(crop, executable=executable, options=options)


def _crop_data_uri(crop: Image.Image, options: ocr.OcrOptions) -> str | None:
    """The preprocessed crop as a data URI: what the engine actually saw. Never
    raises -- a debugging aid, not worth losing the reading over."""
    try:
        buffer = io.BytesIO()
        ocr.preprocess(crop, options).save(buffer, format="PNG")
    except (OSError, ValueError):
        logger.warning("Could not encode the preprocessed crop for the response")
        return None
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _elapsed_ms(started: float) -> int:
    """Milliseconds since ``started``, floored at zero."""
    return max(0, round((time.perf_counter() - started) * 1000))


# --- public API -----------------------------------------------------------


async def status() -> OcrStatus:
    """Report whether text can be read. Never fails -- a missing engine is a
    state, not an error, exactly as with OBS."""
    options = _as_payload(_defaults())
    if not settings.OCR_ENABLED:
        return OcrStatus(
            state=OcrEngineState.DISABLED,
            detail="OCR is disabled; set OCR_ENABLED=true to turn it on",
            options=options,
        )

    executable = settings.ocr_tesseract_cmd
    if executable is None or not executable.is_file():
        looked = ", ".join(str(path) for path in candidate_executables())
        return OcrStatus(
            state=OcrEngineState.NOT_INSTALLED,
            executable=str(executable) if executable is not None else None,
            detail=(
                "No Tesseract executable was found. Install Tesseract OCR or set "
                f"OCR_TESSERACT_CMD to where it is. Looked in: {looked or 'PATH'}"
            ),
            options=options,
        )

    try:
        engine = await asyncio.to_thread(_identify, executable)
    except ocr.OcrError as exc:
        return OcrStatus(
            state=OcrEngineState.ERROR,
            executable=str(executable),
            detail=str(exc),
            options=options,
        )
    return OcrStatus(
        state=OcrEngineState.READY,
        executable=str(engine.executable),
        version=engine.version,
        languages=list(engine.languages),
        options=options,
    )


def regions() -> OcrRegionCatalog:
    """The active game's readable regions and the options each will be read with."""
    name, config = _active_config()
    catalog: list[OcrRegion] = []
    for region in sorted(config.roi):
        overrides = dict(config.ocr.get(region, {}))
        try:
            roi = image_roi.named_roi(config.roi, region)
        except image_roi.RoiError as exc:
            raise GameConfigInvalidError(f"{config.path}: {exc}") from exc
        catalog.append(
            OcrRegion(
                region=region,
                roi=[roi.left, roi.top, roi.right, roi.bottom],
                options=_as_payload(
                    _defaults().merged(overrides, where=f"ocr.{region}")
                ),
                overrides=overrides,
            )
        )
    return OcrRegionCatalog(game=name, regions=catalog)


async def read(request: OcrReadRequest) -> OcrReadResult:
    """Read the named regions off one frame."""
    name, config = _active_config()
    wanted = _requested_regions(config, request.regions)
    # Resolved before anything expensive happens, so a bad override is a 400
    # rather than a screenshot followed by a 400.
    options = {
        region: options_for(config, region, request.options) for region in wanted
    }
    executable = await engine_for_reading()

    if request.run_id is not None:
        if not request.file_name:
            raise BadRequestError(
                "Name the screenshot to read with 'file_name' when 'run_id' is given"
            )
        source = OcrSource.RUN
        frame = await asyncio.to_thread(_run_frame, request.run_id, request.file_name)
    else:
        if request.file_name:
            raise BadRequestError(
                "'file_name' names a screenshot inside a run, so 'run_id' is "
                "required with it"
            )
        source = OcrSource.LIVE
        frame = await _live_frame()

    # Once for the frame, not once per region -- every region is measured
    # against the same content box.
    content = await asyncio.to_thread(roi_service.content_box, frame)
    readings = [
        await asyncio.to_thread(
            _read_region,
            frame,
            region,
            config.roi,
            content=content,
            executable=executable,
            options=options[region],
            include_crop=request.include_crop,
        )
        for region in wanted
    ]
    logger.info(
        "Read %d region(s) of %s off a %s frame",
        len(readings),
        name,
        source.value,
    )
    return OcrReadResult(
        source=source,
        game=name,
        run_id=request.run_id,
        file_name=request.file_name,
        frame_width=frame.width,
        frame_height=frame.height,
        content_box=list(content.box),
        letterboxed=content.letterboxed,
        readings=readings,
    )
