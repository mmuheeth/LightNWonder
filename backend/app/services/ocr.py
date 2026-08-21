"""Reading the game's on-screen text with Tesseract.

The pieces this joins up already exist: the active game config names the parts of
the screen worth reading, :mod:`app.utils.image_roi` cuts one out of a frame at
whatever resolution the frame turned out to be, and :mod:`app.utils.ocr` runs the
engine over it. What is left here is the part that is actually about this
project -- where the frame comes from, which options a region is read with, and
what happens when one region of four cannot be read.

Four decisions are worth knowing before changing anything here.

**A frame is either taken now or read back off disk.** A live read asks OBS for a
screenshot inline, as base64, and never writes a file: an OCR frame is not
evidence and a folder of them would be litter. A read of a capture run's
screenshot is the same code over a file that is already there, which is what
makes a reading reproducible -- the same frame, read again, with one option
changed.

**Options come from three places and are applied in one order.** The environment
carries the defaults (``OCR_*``), the game config's ``ocr`` block overrides them
per region, and the request may override again for one read. Later wins. The
reading says what it ended up with, so tuning from the dashboard and then writing
the winner into the game config is a loop someone can actually close.

**The engine is looked up once, not per read.** Discovery walks the filesystem,
and the status card polls. The resolved path, version and language list are cached
until :func:`reset` drops them.

**One region failing does not fail the read.** Ask for four and a region whose
name is wrong is a rejected request, but a region the engine choked on comes back
beside the others with its ``error`` set. A 502 for the whole read would say
nothing about the three that worked.

**A region is aimed at the game, not at the canvas.** Where a region lands on a
frame is :mod:`app.services.roi`'s question, and the answer accounts for the
black bars a window capture arrives with -- so a resized simulator does not need
every region re-measured. The content box is found once per frame rather than
once per region, and reported on the result beside the frame size.

State is module-level, like the other services here, and callers use the
namespace rather than the functions::

    from app.services import ocr as ocr_service
    await ocr_service.read(OcrReadRequest(regions=["cash_meter"]))
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.config.ocr import candidate_executables
from app.core.config import settings
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
from app.utils import image_roi, letterbox, ocr

logger = get_logger("ocr")


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
    """The engine to run, or an explanation of why there is none.

    Raises:
        OcrEngineUnavailableError: if OCR is disabled, or no Tesseract was found.
            A 409: the caller fixes it by installing the engine or pointing
            ``OCR_TESSERACT_CMD`` at it, not by retrying.
    """
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
    """Ask the engine what it is, and remember the answer.

    Raises:
        ocr.OcrError: if it would not report a version.
    """
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


async def _engine_for_reading() -> Path:
    """The engine, identified once so a broken install fails before any cropping.

    Raises:
        OcrEngineUnavailableError: if there is no usable engine.
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
    """The options a request asked to change, without the ones it left alone.

    ``exclude_unset`` rather than ``exclude_none``, because ``threshold: null`` is
    a request to turn thresholding off and is not the same as not mentioning it.
    """
    if overrides is None:
        return {}
    return overrides.model_dump(exclude_unset=True)


def _options_for(
    config: GameConfig, region: str, overrides: OcrOptionOverrides | None
) -> ocr.OcrOptions:
    """Resolve the options one region is read with.

    Environment, then the game config's ``ocr`` block, then the request. The
    config's own block was validated when the config was read, so only the
    request's half can fail here.
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
    """Which regions to read, checked against the ones the game declares.

    A name that is not configured is a 404 rather than an empty reading: a typo in
    a region name and a meter the engine could not read are different problems and
    should not arrive looking the same.
    """
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
    """A screenshot taken from OBS now, decoded in memory.

    Asked for inline rather than as a file: an OCR frame is not evidence, and
    writing one per read would leave litter in the capture directory. Connecting
    is left to the OBS service, whose failures reach the caller unchanged.

    Raises:
        OcrReadFailedError: if OBS answered with something that is not an image.
        AppException: whatever the OBS service raises when it cannot answer.
    """
    await obs_service.connect()
    result = await obs_service.take_screenshot(
        ScreenshotRequest(image_format="png", width=settings.OCR_SCREENSHOT_WIDTH)
    )
    if not result.image_data:
        raise OcrReadFailedError("OBS returned a screenshot with no image data")
    return _decode(result.image_data)


def _decode(data_uri: str) -> Image.Image:
    """Turn OBS's base64 data URI into an open image.

    Raises:
        OcrReadFailedError: if it is not a data URI, or not a decodable image.
    """
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
    """One screenshot from a capture run, opened and closed.

    The path guards live in the capture service, which is the only place that
    turns a run id and a filename off the wire into a file.

    Raises:
        EventCaptureRunNotFoundError: if the run or the file is not there.
        OcrReadFailedError: if the file is not a readable image.
    """
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

    Runs in a worker thread: the engine is a subprocess and Pillow releases
    nothing while it works.

    ``content`` is the part of the frame the game fills, found once by the caller
    rather than per region -- every region of one frame is aimed at the same
    rectangle, and finding it is a pass over the pixels.
    """
    started = time.perf_counter()
    reading = OcrReading(region=region, options=_as_payload(options))
    try:
        roi = image_roi.named_roi(regions, region)
        box = roi.to_box_within(content.box)
        crop = frame.crop(box)
        result = ocr.read_image(crop, executable=executable, options=options)
    except image_roi.RoiError as exc:
        # The region is configured but unusable -- a config problem, reported on
        # the reading it spoils rather than failing every other region with it.
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


def _crop_data_uri(crop: Image.Image, options: ocr.OcrOptions) -> str | None:
    """The preprocessed crop as a data URI: what the engine actually saw.

    Never raises. This is a debugging aid, and losing the picture is not a reason
    to lose the reading it belongs to.
    """
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
    """Report whether text can be read. Never fails.

    A missing engine is a state, not an error: the dashboard card shows what to
    install and everything else keeps working, exactly as with OBS.
    """
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
    """The active game's readable regions and the options each will be read with.

    What the dashboard lists before anyone reads anything, and the answer to "why
    is this meter read at psm 11" without opening the config file.
    """
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
    """Read the named regions off one frame.

    Raises:
        OcrEngineUnavailableError: if there is no usable Tesseract install.
        OcrRegionNotFoundError: if a named region is not in the game config.
        BadRequestError: if the request's own options are unusable, or a run was
            named without a file.
        EventCaptureRunNotFoundError: if the named run or screenshot is not there.
        OcrReadFailedError: if the frame itself could not be obtained.
        AppException: whatever OBS raises for a live read it cannot serve.
    """
    name, config = _active_config()
    wanted = _requested_regions(config, request.regions)
    # Options are resolved before anything expensive happens, so a bad override
    # is a 400 rather than a screenshot followed by a 400.
    options = {
        region: _options_for(config, region, request.options) for region in wanted
    }
    executable = await _engine_for_reading()

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

    # Once for the frame, not once per region: the bars round a window capture
    # are a property of the shot, and every region is measured against what is
    # inside them.
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
