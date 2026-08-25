"""Cuts a configured region out of a captured frame.

Same joinery as :mod:`app.services.ocr`, one step short: OCR hands the crop to
Tesseract, this hands back the picture — checking a region's aim needs no engine.
The frame comes off disk (newest in ``obs_dashboard_screenshot_dir``), never a
live OBS capture, so the same crop extracted twice is the same picture. Crops
return as data URIs and nothing is written, except ``cash_meter``, which is also
kept on disk and read via :mod:`app.services.meter`. Regions resolve against
:func:`content_box` (the game's content, not the canvas), since letterbox bars
change width as the window resizes — see :mod:`app.utils.letterbox`.
:func:`resolve_frame`/:func:`open_frame`/:func:`is_blank`/:func:`content_box`/
:func:`resolve_box`/
:func:`describe`/:func:`describe_path`/:func:`encode_png` are public so
:mod:`app.services.grid` and :mod:`app.services.ocr` share this reading instead of
re-deriving it. Holds no state, so no ``reset()``.
"""

from __future__ import annotations

import asyncio
import base64
import io
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    BadRequestError,
    GameConfigInvalidError,
    RoiExtractFailedError,
    RoiFrameNotFoundError,
    RoiRegionNotFoundError,
)
from app.schemas.roi import (
    RoiCatalog,
    RoiExtractRequest,
    RoiExtractResult,
    RoiFrame,
    RoiRegionSummary,
)
from app.services import meter as meter_service
from app.utils import image_roi, letterbox, paths

logger = get_logger("roi")

# Frame candidates in the screenshot dir; a stray .txt/.json is skipped rather
# than offered as a frame and failing to open.
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})

# The one region whose crop is also kept on disk, for a running record over time.
_SAVED_REGION = "cash_meter"
_SAVED_REGION_DIR = "cash-meter"


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


def _region_names(config: GameConfig) -> list[str]:
    """The regions the game declares, sorted, or a 404 saying it declares none."""
    if not config.roi:
        raise RoiRegionNotFoundError(
            f"The game config for {config.name!r} declares no 'roi' regions"
        )
    return sorted(config.roi)


# --- the frame ------------------------------------------------------------


def _frame_dir() -> Path:
    """Directory the dashboard's screenshots land in."""
    return settings.obs_dashboard_screenshot_dir


def latest_path() -> Path | None:
    """Newest image in the screenshots directory, by mtime (not filename), or None."""
    directory = _frame_dir()
    if not directory.is_dir():
        return None
    frames = [
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
    ]
    if not frames:
        return None
    return max(frames, key=lambda path: path.stat().st_mtime)


def _named_path(file_name: str) -> Path:
    """Resolve a caller-supplied frame name inside the screenshots directory."""
    try:
        path = paths.resolve_within(_frame_dir(), file_name)
    except paths.UnsafeNameError as exc:
        raise BadRequestError(
            f"file_name is not a usable frame name: {exc.reason}"
        ) from exc
    if not path.is_file():
        raise RoiFrameNotFoundError(
            f"No screenshot named {file_name!r} is in {_frame_dir()}"
        )
    return path


def resolve_frame(file_name: str | None) -> Path:
    """The frame to crop: the one that was named, or the newest one."""
    if file_name is not None:
        return _named_path(file_name)
    latest = latest_path()
    if latest is None:
        raise RoiFrameNotFoundError(
            "No screenshot has been taken yet. Take one from the OBS panel; "
            f"they are written to {_frame_dir()}"
        )
    return latest


def open_frame(path: Path) -> Image.Image:
    """Read one frame fully, so the crop outlives the file handle."""
    try:
        with Image.open(path) as image:
            image.load()
            return image.copy()
    except OSError as exc:
        raise RoiExtractFailedError(
            f"{path.name} could not be read as an image: {exc}"
        ) from exc


def is_blank(path: Path) -> bool:
    """Whether the frame at ``path`` has nothing in it.

    Here rather than in the caller because this module already owns opening a
    frame, and because the threshold has to be the same one regions are resolved
    against -- a frame nothing can be cropped out of and a frame that is blank
    are the same measurement (:func:`app.utils.letterbox.is_blank`). An
    unreadable file is reported as *not* blank: that is a different failure with
    a different message, and whoever reads the frame next will give it.
    """
    try:
        with Image.open(path) as image:
            image.load()
            return letterbox.is_blank(
                image, threshold=settings.FRAME_LETTERBOX_THRESHOLD
            )
    except OSError:
        return False


def content_box(image: Image.Image) -> letterbox.ContentBox:
    """The part of one frame the game fills, which is what regions are aimed at.
    ``FRAME_LETTERBOX_TRIM=false`` disables detection, reverting to canvas fractions."""
    if not settings.FRAME_LETTERBOX_TRIM:
        return letterbox.ContentBox.whole(image.width, image.height)
    return letterbox.content_box(
        image,
        threshold=settings.FRAME_LETTERBOX_THRESHOLD,
        min_fraction=settings.FRAME_LETTERBOX_MIN_FRACTION,
    )


def resolve_box(
    roi: image_roi.Roi, image: Image.Image
) -> tuple[tuple[int, int, int, int], letterbox.ContentBox]:
    """Where one region lands on one frame, in the frame's own pixels — the box
    comes back too, since a misplaced crop is either a bad region or a misdetected
    box, and only the pair says which. For several regions on one frame, call
    :func:`content_box` once and :meth:`image_roi.Roi.to_box_within` per region
    instead."""
    content = content_box(image)
    return roi.to_box_within(content.box), content


def describe(path: Path, image: Image.Image) -> RoiFrame:
    """What the API says about the frame a crop came from."""
    return RoiFrame(
        file_name=path.name,
        captured_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        width=image.width,
        height=image.height,
    )


def describe_path(path: Path) -> RoiFrame:
    """Describe a frame without decoding it — ``Image.open`` reads only the header."""
    try:
        with Image.open(path) as image:
            return describe(path, image)
    except OSError as exc:
        raise RoiExtractFailedError(
            f"{path.name} could not be read as an image: {exc}"
        ) from exc


def _save_crop(crop: Image.Image, source: Path) -> None:
    """Write a cash meter crop to disk, named after the frame it came from."""
    directory = settings.obs_capture_dir / _SAVED_REGION_DIR
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{source.stem}.png"
    try:
        crop.save(destination, format="PNG")
    except OSError as exc:
        raise RoiExtractFailedError(
            f"{destination} could not be written: {exc}"
        ) from exc
    logger.info(
        "Saved roi.%s crop of %s to %s", _SAVED_REGION, source.name, destination
    )


# --- public API -----------------------------------------------------------


def _catalog() -> RoiCatalog:
    """Blocking half of :func:`catalog`."""
    name, config = _active_config()
    summaries: list[RoiRegionSummary] = []
    for region in _region_names(config):
        try:
            roi = image_roi.named_roi(config.roi, region)
        except image_roi.RoiError as exc:
            # Listed with its reason rather than dropped, so it isn't mistaken
            # for a region the config never declared.
            summaries.append(
                RoiRegionSummary(
                    region=region,
                    label=f"roi.{region}",
                    roi=[0.0, 0.0, 1.0, 1.0],
                    error=str(exc),
                )
            )
            continue
        summaries.append(
            RoiRegionSummary(
                region=region,
                label=f"roi.{region}",
                roi=[roi.left, roi.top, roi.right, roi.bottom],
            )
        )

    latest = latest_path()
    return RoiCatalog(
        game=name,
        regions=summaries,
        latest_frame=describe_path(latest) if latest is not None else None,
    )


async def catalog() -> RoiCatalog:
    """The active game's regions, and the frame an extraction would use."""
    return await asyncio.to_thread(_catalog)


def encode_png(crop: Image.Image) -> str:
    """The crop as a PNG data URI — PNG regardless of source format, since JPEG
    artefacts would make a region look badly aimed. Unlike OCR's equivalent, an
    encode failure is fatal here: the picture *is* the answer."""
    buffer = io.BytesIO()
    try:
        crop.save(buffer, format="PNG")
    except (OSError, ValueError) as exc:
        raise RoiExtractFailedError(f"The crop could not be encoded: {exc}") from exc
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _extract(request: RoiExtractRequest) -> RoiExtractResult:
    """Blocking half of :func:`extract`."""
    name, config = _active_config()
    known = _region_names(config)
    if request.region not in known:
        raise RoiRegionNotFoundError(
            f"The game config for {config.name!r} has no region named "
            f"{request.region!r} (configured regions: {', '.join(known)})"
        )

    path = resolve_frame(request.file_name)
    frame = open_frame(path)
    try:
        roi = image_roi.named_roi(config.roi, request.region)
        box, content = resolve_box(roi, frame)
        crop = frame.crop(box)
    except image_roi.RoiError as exc:
        raise GameConfigInvalidError(f"{config.path}: {exc}") from exc

    logger.info(
        "Extracted roi.%s of %s from %s (%dx%d) within content box %s",
        request.region,
        name,
        path.name,
        crop.width,
        crop.height,
        content.box,
    )
    values = None
    if request.region == _SAVED_REGION:
        _save_crop(crop, path)
        values = meter_service.read(crop, game=name, profile=config.meter)
    return RoiExtractResult(
        game=name,
        region=request.region,
        roi=[roi.left, roi.top, roi.right, roi.bottom],
        source=describe(path, frame),
        box=list(box),
        content_box=list(content.box),
        letterboxed=content.letterboxed,
        width=crop.width,
        height=crop.height,
        image_data=encode_png(crop),
        meter=values,
    )


async def extract(request: RoiExtractRequest) -> RoiExtractResult:
    """Cut one region out of one frame and hand back the picture."""
    return await asyncio.to_thread(_extract, request)
