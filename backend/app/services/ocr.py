"""Reads text with PaddleOCR: the number printed on a symbol orb, and any
other named region a game config declares in its ``roi`` block."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image

from app.config.game_config import GameConfig, GameConfigError, load_game_config
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
from app.utils import image_roi, letterbox, paddle_ocr

logger = get_logger("ocr")

# The `ocr` block key a game overrides symbol-tile reading under. Not a region in
# `roi` -- a tile is cut by the reel grid, not by a named screen rectangle -- so
# it shares that block's shape without appearing in the region catalogue.
_TILE_REGION = "symbol_tile"


def reset() -> None:
    """Drop PaddleOCR's cached engine/recogniser. Tests, and after a settings change."""
    paddle_ocr.reset()


def ensure_available() -> None:
    """Raise :class:`OcrEngineUnavailableError` (a 409, not a retryable
    failure) unless a read can be attempted right now. Public so a caller
    resolving its own options once for several tiles (:func:`app.services.
    analyze_spin._read_scatters`) fails once for the whole grid rather than
    once per tile."""
    if not settings.OCR_ENABLED:
        raise OcrEngineUnavailableError(
            "OCR is disabled; set OCR_ENABLED=true to turn it on"
        )
    if not paddle_ocr.available():
        raise OcrEngineUnavailableError(
            "PaddleOCR is not installed. Install it with: pip install paddlepaddle "
            "-f https://www.paddlepaddle.org.cn/whl/windows.html && pip install "
            "paddleocr  (needs Python 3.13 or lower -- paddlepaddle publishes no "
            "wheels for 3.14)"
        )


async def paddle_line_engine_for_reading(options: paddle_ocr.PaddleLineOptions) -> str:
    """PaddleOCR's line recogniser, built before any cropping -- the line
    counterpart of :func:`ensure_available`, but this one actually builds the
    model rather than just checking it can, so a caller reading many frames
    (cyclic messages) fails once up front rather than once per frame."""
    if not settings.OCR_ENABLED:
        raise OcrEngineUnavailableError(
            "OCR is disabled; set OCR_ENABLED=true to turn it on"
        )
    try:
        model = await asyncio.to_thread(paddle_ocr.prepare_line, options)
    except paddle_ocr.OcrUnavailableError as exc:
        raise OcrEngineUnavailableError(str(exc)) from exc
    except paddle_ocr.OcrError as exc:
        raise OcrEngineUnavailableError(
            f"PaddleOCR is installed but would not start: {exc}"
        ) from exc
    logger.info("OCR engine: PaddleOCR line recogniser %s", model)
    return model


# --- named-region options ---------------------------------------------


def _region_defaults() -> paddle_ocr.PaddleOptions:
    """The options every named-region read starts from, straight out of the
    environment."""
    return paddle_ocr.PaddleOptions(
        language=settings.OCR_REGION_PADDLE_LANGUAGE,
        upscale=settings.OCR_REGION_PADDLE_UPSCALE,
        min_confidence=settings.OCR_REGION_PADDLE_MIN_CONFIDENCE,
    )


def _request_overrides(overrides: OcrOptionOverrides | None) -> Mapping[str, Any]:
    """The options a request asked to change. ``exclude_unset``, not
    ``exclude_none`` -- ``min_confidence: null`` would mean something
    different from "not given"."""
    if overrides is None:
        return {}
    return overrides.model_dump(exclude_unset=True)


def options_for(
    config: GameConfig, region: str, overrides: OcrOptionOverrides | None = None
) -> paddle_ocr.PaddleOptions:
    """Resolve the options one region is read with: environment, then the
    game config's ``ocr`` block, then the request. Public for the same reason
    as :func:`ensure_available` -- the precedence is this module's to state."""
    resolved = _region_defaults().merged(config.ocr.get(region), where=f"ocr.{region}")
    try:
        return resolved.merged(_request_overrides(overrides), where="options")
    except paddle_ocr.OcrOptionsError as exc:
        raise BadRequestError(str(exc)) from exc


def _as_payload(options: paddle_ocr.PaddleOptions) -> OcrOptionsPayload:
    """The options a reading was taken with, as the API reports them."""
    return OcrOptionsPayload(
        language=options.language,
        upscale=options.upscale,
        min_confidence=options.min_confidence,
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


# --- the frame --------------------------------------------------------------


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


# --- reading a named region -------------------------------------------------


def _word_box(word: paddle_ocr.PaddleWord) -> OcrWordBox:
    """One recognised word as the API reports it: crop pixels, and Paddle's
    0-1 confidence rescaled onto Tesseract's inherited 0-100."""
    left, top, right, bottom = word.box or (0, 0, 0, 0)
    return OcrWordBox(
        text=word.text,
        confidence=round(word.confidence * 100.0, 1),
        left=left,
        top=top,
        width=right - left,
        height=bottom - top,
    )


def _read_region(
    frame: Image.Image,
    region: str,
    regions: Mapping[str, Any],
    *,
    content: letterbox.ContentBox,
    options: paddle_ocr.PaddleOptions,
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
        result = paddle_ocr.read_image(crop, options=options)
    except image_roi.RoiError as exc:
        # Reported on the reading it spoils, not failing every other region.
        return reading.model_copy(
            update={
                "error": str(exc),
                "duration_ms": _elapsed_ms(started),
            }
        )
    except paddle_ocr.OcrError as exc:
        return reading.model_copy(
            update={"error": str(exc), "duration_ms": _elapsed_ms(started)}
        )

    # `words` reports everything the detector found, rejected or not -- the
    # same "show what was read, even refused" rule the scatter/prize reader
    # follows -- but `text`/`value`/`values` are built only from what cleared
    # `min_confidence`, so a stray word off the artwork doesn't corrupt them.
    believable = [
        word for word in result.words if word.confidence >= options.min_confidence
    ]
    text = " ".join(word.text for word in believable)
    numbers = [float(number) for number in paddle_ocr.parse_numbers(text)]
    confidence = (
        sum(word.confidence for word in believable) / len(believable) * 100.0
        if believable
        else None
    )
    return reading.model_copy(
        update={
            "text": text,
            "value": numbers[0] if numbers else None,
            "values": numbers,
            "confidence": confidence,
            "words": [_word_box(word) for word in result.words],
            "crop": list(box),
            "crop_image": _crop_data_uri(crop) if include_crop else None,
            "duration_ms": _elapsed_ms(started),
        }
    )


def _crop_data_uri(crop: Image.Image) -> str | None:
    """The crop as a data URI: what the engine actually saw (Paddle reads a
    region as given, so unlike Tesseract's preprocessed crop this is the crop
    itself). Never raises -- a debugging aid, not worth losing the reading over."""
    try:
        buffer = io.BytesIO()
        crop.convert("RGB").save(buffer, format="PNG")
    except (OSError, ValueError):
        logger.warning("Could not encode the crop for the response")
        return None
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _elapsed_ms(started: float) -> int:
    """Milliseconds since ``started``, floored at zero."""
    return max(0, round((time.perf_counter() - started) * 1000))


# --- reading the figure off a prize tile ------------------------------------
#
# A tile is a decorated picture with a number in the middle; PaddleOCR is the
# only reader of it because Tesseract measurably could not (see this module's
# docstring). `min_digits` is the floor: a figureless orb answers with either
# nothing or a bare stray digit, so a reading needs at least two digits to
# count as a prize -- waived for a currency mark, which the filigree never
# draws (`paddle_ocr._AMOUNT_MARKS`).

# The fewest digits a real prize figure is drawn with -- see the module-level
# note above. Shared with :func:`_paddle_options` below and with
# `paddle_ocr.PaddleOptions.min_digits`'s own default, so the two stay in sync.
TILE_MIN_DIGITS = 2


@dataclass(frozen=True)
class TileReading:
    """What a prize tile read as, and how sure the reading is. ``text`` is
    ``None`` for both a tile with no figure and one that could not be read --
    deliberately not told apart, since neither is a prize."""

    text: str | None
    confidence: float | None
    candidates: tuple[tuple[str, float], ...]
    """Every word PaddleOCR found on the tile, as ``(text, confidence)`` --
    kept so a surprising answer can be explained without re-running the read."""

    label: str | None = None
    """The jackpot tier printed on the orb where it carries a word instead of a
    figure -- 'MAJOR', 'MINI', whatever the game draws."""


def _orb_defaults() -> paddle_ocr.PaddleOptions:
    """The options an orb is read with, straight out of the environment."""
    return paddle_ocr.PaddleOptions(
        language=settings.OCR_ORB_PADDLE_LANGUAGE,
        upscale=settings.OCR_ORB_PADDLE_UPSCALE,
        min_confidence=settings.OCR_ORB_PADDLE_MIN_CONFIDENCE,
        min_digits=TILE_MIN_DIGITS,
        timeout_seconds=settings.OCR_ORB_PADDLE_TIMEOUT_SECONDS,
    )


def read_tile_options(
    config: GameConfig, region: str = _TILE_REGION
) -> paddle_ocr.PaddleOptions:
    """The options a symbol tile is read with: the environment's orb defaults,
    then the game config's ``ocr`` block for ``region`` -- so a game whose
    orbs are drawn dark can say so without a code change."""
    return _orb_defaults().merged(config.ocr.get(region), where=f"ocr.{region}")


def read_tile(
    tile: Image.Image,
    *,
    options: paddle_ocr.PaddleOptions,
) -> TileReading:
    """Read the prize figure on one symbol tile with PaddleOCR -- the one crop
    Tesseract measurably could not manage (0.9998 vs nothing, on a tile
    reading ``160``). Never falls back: an engine that cannot start fails the
    read, via :class:`paddle_ocr.OcrError`, rather than returning a
    Tesseract-voted guess this project no longer carries the code to make."""
    value, label, result = paddle_ocr.read_prize(tile, options=options)

    # Scaled to the 0-100 the API reports regardless of which reading produced it.
    candidates = tuple((word.text, word.confidence * 100.0) for word in result.words)
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
        logger.debug(
            "PaddleOCR read no figure on an orb (candidates: %s)",
            ", ".join(f"{text} @{confidence:.1f}" for text, confidence in candidates)
            or "none",
        )
        return TileReading(text=None, confidence=None, candidates=candidates)
    # `str(Decimal)` so a figure keeps the digits it was read with.
    digits = "".join(character for character in str(value) if character.isdigit())
    confidence = (result.confidence or 0.0) * 100.0
    logger.debug("PaddleOCR read %s on an orb at %.1f", digits, confidence)
    return TileReading(text=digits, confidence=confidence, candidates=candidates)


# --- public API -----------------------------------------------------------


async def status() -> OcrStatus:
    """Report whether text can be read. Never fails -- a missing engine is a
    state, not an error, exactly as with OBS."""
    options = _as_payload(_region_defaults())
    if not settings.OCR_ENABLED:
        return OcrStatus(
            state=OcrEngineState.DISABLED,
            detail="OCR is disabled; set OCR_ENABLED=true to turn it on",
            options=options,
        )
    if not paddle_ocr.available():
        return OcrStatus(
            state=OcrEngineState.NOT_INSTALLED,
            detail=(
                "PaddleOCR is not installed. Install it with: pip install "
                "paddlepaddle -f https://www.paddlepaddle.org.cn/whl/windows.html "
                "&& pip install paddleocr  (needs Python 3.13 or lower)"
            ),
            options=options,
        )

    try:
        version = await asyncio.to_thread(
            paddle_ocr.prepare,
            paddle_ocr.PaddleOptions(language=settings.OCR_REGION_PADDLE_LANGUAGE),
        )
    except paddle_ocr.OcrError as exc:
        return OcrStatus(state=OcrEngineState.ERROR, detail=str(exc), options=options)
    return OcrStatus(
        state=OcrEngineState.READY,
        version=version,
        languages=[settings.OCR_REGION_PADDLE_LANGUAGE],
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
                    _region_defaults().merged(overrides, where=f"ocr.{region}")
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
    ensure_available()

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
    content = await asyncio.to_thread(roi_service.content_box, frame, config=config)
    readings = [
        await asyncio.to_thread(
            _read_region,
            frame,
            region,
            config.roi,
            content=content,
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
