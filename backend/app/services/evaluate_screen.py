"""Evaluate Screen: read one screen of the game and say what is on it.

**Nothing here is new logic.** Every reading this feature reports already existed
for Analyze Spin, and this module is the composition of those pieces over a
single frame rather than over a driven spin:

* the grid comes from :mod:`app.services.grid` (crop ``roi.reels``, cut it into
  tiles, write the split) and :mod:`app.services.image_classifier` (name every
  tile with the trained CNN);
* the figure on a scatter orb comes from :mod:`app.services.ocr`'s
  ``read_tile``, which is PaddleOCR;
* the cash meter comes from :mod:`app.services.roi`'s extract of
  ``roi.cash_meter``.

One thing differs from Analyze Spin deliberately, and it is the reason this is a
separate feature rather than a flag on that one: **the meter is read with
PaddleOCR**, through ``meter_service.read_paddle``. Analyze Spin's meter
validations were measured against Tesseract and stay on it.

What is *absent* is as deliberate. There is no spin, no log to follow, no
recording, and **no validation**: a check like "balance fell by the bet" needs
two readings of a meter and this feature has one. So it reports what the screen
says and stops -- the judging is Analyze Spin's job.

Unlike Analyze Spin this holds no run state, so there is no lock, no stream and
no ``reset()``: every request is answered from the frame it names or captures,
and two callers asking at once are two independent readings.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.config.runtime import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    AppException,
    GameConfigInvalidError,
    ScreenEvaluationFailedError,
)
from app.schemas.analyze_spin import SpinReelReading
from app.schemas.evaluate_screen import (
    EvaluateScreenMeter,
    EvaluateScreenRequest,
    EvaluateScreenResult,
    EvaluateScreenSource,
)
from app.schemas.grid import GridSplitRequest
from app.schemas.image_classifier import ClassifyRequest, ClassifyResult
from app.schemas.meter import MeterEngine, MeterMode
from app.schemas.obs import ScreenshotRequest
from app.schemas.roi import RoiExtractRequest
from app.services import analyze_spin as analyze_spin_service
from app.services import grid as grid_service
from app.services import image_classifier as classifier_service
from app.services import obs as obs_service
from app.services import roi as roi_service

logger = get_logger("evaluate_screen")

# The region the meter is read off. The same name Analyze Spin reads, because it
# is the same strip -- only the engine differs.
_METER_REGION = "cash_meter"

# Prefix for a frame this feature captures. Named so a screenshots directory
# shared with Analyze Spin and Event Capture still says which feature took what.
_FRAME_PREFIX = "screen"


def _active_config() -> tuple[str, GameConfig]:
    """The selected game and its config, or why it cannot be used. Raises rather
    than guessing: reading a screen means reading *this* game's regions, and
    there is nothing sensible to fall back to."""
    name = settings.ideck_active_game
    path = settings.ideck_game_config_path_for(name)
    try:
        return name, load_game_config(path)
    except GameConfigError as exc:
        raise GameConfigInvalidError(
            f"Could not load game config {path}: {exc}"
        ) from exc


async def _capture() -> tuple[Path, bool]:
    """Take a screenshot of the game as it is now, as ``(path, blank)``.

    Written to disk rather than kept in memory, unlike the OCR service's live
    frame: the grid split reads the frame back by name, and a reading somebody
    disagrees with is only arguable if the picture behind it still exists.
    """
    await obs_service.connect()
    result = await obs_service.take_screenshot(
        ScreenshotRequest(
            image_format="png",
            width=settings.ANALYZE_SPIN_SCREENSHOT_WIDTH,
            file_name=f"{_FRAME_PREFIX}-{int(time.time() * 1000)}",
            output_dir=settings.OBS_SCREENSHOT_SUBDIR,
        )
    )
    if result.file_path is None:
        raise ScreenEvaluationFailedError(
            "OBS returned the screenshot but wrote no file, so there is nothing to read"
        )
    path = Path(result.file_path)
    # Read back rather than trusted, for the reason Analyze Spin does the same:
    # OBS reports a successful write of a frame it rendered nothing into, so the
    # only way to know the capture happened is to look at it.
    blank = await asyncio.to_thread(roi_service.is_blank, path)
    return path, blank


def _source(path: Path, *, captured: bool, blank: bool) -> EvaluateScreenSource:
    """Describe the frame that was read, content box included.

    One decode for both answers: the content box is a property of this frame's
    shape at this moment, so it is found here rather than left for each region's
    own crop to rediscover.
    """
    image = roi_service.open_frame(path)
    frame = roi_service.describe(path, image)
    content = roi_service.content_box(image)
    return EvaluateScreenSource(
        file_name=frame.file_name,
        captured=captured,
        at=datetime.now(),
        width=frame.width,
        height=frame.height,
        blank=blank,
        content_box=list(content.box),
        letterboxed=content.letterboxed,
    )


async def _read_grid(
    file_name: str, architecture: str | None, *, include_images: bool
) -> tuple[SpinReelReading, ClassifyResult, Path]:
    """Crop the grid, cut it into tiles, and name every one of them.

    The split is written to disk because the tiles are what gets classified *and*
    what the scatter reader OCRs -- so the pixels Paddle sees are exactly the
    ones the classifier named the code from, which is the same reason Analyze
    Spin reads its orbs back off the split rather than re-cropping the frame.
    """
    split = await grid_service.split(
        GridSplitRequest(file_name=file_name, include_images=False)
    )
    result = await classifier_service.classify(
        ClassifyRequest(
            split=Path(split.output_dir).name,
            architecture=architecture,
            min_confidence=settings.ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE,
            # No per-tile pictures: fifteen data URIs nothing renders. The ringed
            # grid is the one picture this feature shows, and `include_overlay`
            # is what produces it.
            include_images=False,
            include_overlay=include_images,
        )
    )
    return analyze_spin_service.reading(result), result, Path(split.output_dir)


async def _read_meter(file_name: str, *, include_images: bool) -> EvaluateScreenMeter:
    """Read the cash meter strip with PaddleOCR.

    Never raises for a bad *reading* -- an unreadable strip comes back as
    ``error`` with the crop still attached, since the picture beside the failure
    is what makes it diagnosable. Only a region or frame it cannot reach at all
    is an exception, and the caller records that against the meter rather than
    failing the whole screen.
    """
    result = await roi_service.extract(
        RoiExtractRequest(
            region=_METER_REGION,
            file_name=file_name,
            engine=MeterEngine.PADDLE,
        )
    )
    values = result.meter
    balance = None
    if values is not None:
        # Whichever side the strip was drawing. Not summed or preferred: exactly
        # one of the two is set, and which one is what `mode` says.
        balance = values.cash if values.cash is not None else values.credits
    return EvaluateScreenMeter(
        engine=MeterEngine.PADDLE,
        mode=MeterMode.UNKNOWN if values is None else values.mode,
        currency=None if values is None else values.currency,
        balance=balance,
        win=None if values is None else values.win,
        bet=None if values is None else values.bet,
        values=values,
        crop_image=result.image_data if include_images else None,
        error=(
            "the cash meter region produced no reading"
            if values is None
            else values.error
        ),
    )


async def evaluate(
    request: EvaluateScreenRequest | None = None,
) -> EvaluateScreenResult:
    """Read one screen: the symbols on the grid, the figures on its scatters, and
    the cash meter.

    **Partial by design.** The grid and the meter are independent readings of one
    picture, so a failure of either is recorded and the other still returned --
    a screen whose meter is unreadable still has a grid worth seeing. Both
    failures leave a result carrying two errors and no readings, which is still
    a more useful answer than an exception saying only the first thing that went
    wrong.
    """
    started = time.perf_counter()
    payload = request or EvaluateScreenRequest()
    name, config = _active_config()
    errors: list[str] = []

    if payload.file_name is None:
        path, blank = await _capture()
        captured = True
    else:
        # Resolved through the ROI service so an unknown or non-bare filename is
        # its 404 rather than a path this module invented.
        path = await asyncio.to_thread(roi_service.resolve_frame, payload.file_name)
        blank = await asyncio.to_thread(roi_service.is_blank, path)
        captured = False

    source = await asyncio.to_thread(_source, path, captured=captured, blank=blank)
    if blank:
        # Recorded, not raised: every reading below is still attempted and still
        # returned, and this is why they will look wrong.
        errors.append(
            f"{source.file_name} has nothing in it. Check that OBS's "
            "window-capture source is pointed at the game and showing it."
        )

    reels: SpinReelReading | None = None
    reels_error: str | None = None
    grid_image: str | None = None
    try:
        reels, result, split_dir = await _read_grid(
            source.file_name,
            payload.architecture,
            include_images=payload.include_images,
        )
    except AppException as exc:
        reels_error = exc.message
        errors.append(f"Grid: {exc.message}")
    except Exception as exc:
        reels_error = f"{type(exc).__name__}: {exc}"
        errors.append(f"Grid: {reels_error}")
        logger.exception("Reading the grid of %s failed", name)
    else:
        grid_image = result.overlay_image
        # The scatters are part of *what landed*: the same split, annotated by a
        # second reader. A game declaring no scatter codes leaves both fields
        # empty, which is not a failure.
        scatters, summary = await analyze_spin_service.read_scatters(
            config, split_dir, reels
        )
        reels = reels.model_copy(
            update={"scatters": scatters, "scatter_summary": summary}
        )

    meter: EvaluateScreenMeter | None = None
    try:
        meter = await _read_meter(
            source.file_name, include_images=payload.include_images
        )
    except AppException as exc:
        errors.append(f"Cash meter: {exc.message}")
    except Exception as exc:
        errors.append(f"Cash meter: {type(exc).__name__}: {exc}")
        logger.exception("Reading the cash meter of %s failed", name)
    else:
        if meter.error:
            errors.append(f"Cash meter: {meter.error}")

    duration_ms = max(0, round((time.perf_counter() - started) * 1000))
    logger.info(
        "Evaluated the screen of %s from %s in %dms: %s | meter %s | %d error(s)",
        name,
        source.file_name,
        duration_ms,
        "no grid reading" if reels is None else reels.summary,
        "unread" if meter is None else meter.mode.value,
        len(errors),
    )
    return EvaluateScreenResult(
        game=name,
        label=config.name,
        source=source,
        duration_ms=duration_ms,
        reels=reels,
        reels_error=reels_error,
        meter=meter,
        grid_image=grid_image,
        errors=errors,
    )
