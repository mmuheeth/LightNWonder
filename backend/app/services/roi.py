"""Cutting a configured region out of a captured frame.

Every piece this joins up already existed. The active game config names the
parts of the screen worth looking at, :mod:`app.utils.image_roi` cuts one out of
a frame at whatever resolution the frame turned out to be, and the OBS service
already writes screenshots into a directory. What is left here is the part that
is about this project: which frame is meant by "the latest one", and what a
region looks like once it has been cut out.

This is the same joinery :mod:`app.services.ocr` does, stopping one step short.
OCR crops a region and hands the crop to Tesseract; this crops a region and
hands back the picture. That step is worth having on its own, because the two
questions fail independently -- a meter that reads as punctuation is either
aimed at the wrong rectangle or preprocessed badly, and only one of those is
visible in the crop. Checking the rectangle needs no engine installed.

**The frame comes off disk, newest first.** The dashboard's Screenshot button
writes into :attr:`settings.obs_dashboard_screenshot_dir`, so an extraction with
no ``file_name`` means the shot that was just taken. Nothing here asks OBS for a
frame: a region is checked against evidence that is still on disk to be looked
at again, and a live capture would make "the same crop, extracted twice" mean
two different pictures.

**Nothing is written.** The crop goes back as a data URI, like OCR's own
``include_crop``. A folder of crops of crops is litter, and the source frame is
already saved. The cash meter is the one exception, kept on disk for a record of
what the meter read over time.

**The cash meter is also read.** That one region hands its crop to
:mod:`app.services.meter` and returns the numbers beside the picture, because
cropping the meter and reading it are one action from the panel's point of view --
and cropping separately for each would let two extractions of the same strip
disagree. Reading cannot fail the extraction: the failure arrives as an ``error``
on the reading, so a host with no Tesseract still gets its crop.

**A region is aimed at the game, not at the canvas.** OBS writes every frame at
its canvas size and fits the window capture inside it, so a portrait simulator
arrives with black bars down both sides -- and their width is a property of the
window's shape at that moment, which means a region measured against the canvas
comes unaimed the moment the simulator is resized. :func:`content_box` finds the
part of the frame the game fills and :func:`resolve_box` resolves a region
against that, so the same four fractions describe the same part of the game at
every window size. :mod:`app.utils.letterbox` is where the detection and its
guardrails are documented.

**The frame plumbing is public, and :mod:`app.services.grid` and
:mod:`app.services.ocr` share it.** :func:`resolve_frame`, :func:`open_frame`,
:func:`content_box`, :func:`resolve_box`, :func:`describe`,
:func:`describe_path` and :func:`encode_png` are the answer to "which shot, what
was in it, and where does a region land on it", and this module is where that
answer is documented -- so the grid splitter and the reader call them rather than
growing a second reading of "the latest screenshot" or a second reading of a
letterboxed frame. They raise
:class:`~app.exceptions.base.RoiFrameNotFoundError` and
:class:`~app.exceptions.base.RoiExtractFailedError` whoever the caller is,
because the thing that is missing or unreadable is the frame either way.

Unlike its neighbours this service holds no state -- no client, no lock, no
cached engine -- so it has no ``reset()`` and ``tests/conftest.py`` has nothing
to clean up between tests. Callers still use the namespace::

    from app.services import roi as roi_service
    await roi_service.extract(RoiExtractRequest(region="cash_meter"))
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

# What OBS can be asked to write, which is what may turn up in the directory.
# A stray .txt or .json alongside the frames is skipped rather than offered as a
# frame and then failing to open.
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})

# The one region whose crop is also kept on disk, for a running record of what
# the meter read over time rather than only the single latest crop the API
# returns.
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
    """Newest image in the screenshots directory, or None if there is none.

    Modification time rather than name: the names carry a timestamp today, but
    sorting pictures by string is a bug waiting for the day they do not.
    """
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
    """Resolve a caller-supplied frame name inside the screenshots directory.

    Raises:
        BadRequestError: if the name is not a bare filename.
        RoiFrameNotFoundError: if no such frame is there.
    """
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
    """The frame to crop: the one that was named, or the newest one.

    Raises:
        BadRequestError: if a named frame is not a bare filename.
        RoiFrameNotFoundError: if the named frame, or any frame at all, is gone.
    """
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
    """Read one frame fully, so the crop outlives the file handle.

    Raises:
        RoiExtractFailedError: if the file is not a readable image. Pillow's
            ``UnidentifiedImageError`` is an ``OSError``, so "not an image" and
            "unreadable" arrive by one door.
    """
    try:
        with Image.open(path) as image:
            image.load()
            return image.copy()
    except OSError as exc:
        raise RoiExtractFailedError(
            f"{path.name} could not be read as an image: {exc}"
        ) from exc


def content_box(image: Image.Image) -> letterbox.ContentBox:
    """The part of one frame the game fills, which is what regions are aimed at.

    OBS writes every frame at its canvas size and fits the window capture inside
    it, so a portrait simulator arrives with black bars whose width is a property
    of the window's shape at that moment. Resolving a region against this box
    rather than against the canvas is what makes the same four fractions right
    for every window size -- see :mod:`app.utils.letterbox`.

    ``FRAME_LETTERBOX_TRIM=false`` turns it off, which puts every region back to
    fractions of the whole canvas.
    """
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
    """Where one region lands on one frame, in the frame's own pixels.

    The single answer to "which pixels is this region" for ROI, OCR and the reel
    grid alike, so the three cannot drift on how a letterboxed frame is read.
    The content box comes back beside the pixel box because it is what explains
    it: a crop that looks misplaced is either a bad region or a misdetected box,
    and only the pair tells them apart.

    Reading several regions off one frame should find the box once with
    :func:`content_box` and resolve each region with
    :meth:`app.utils.image_roi.Roi.to_box_within`; this is the single-region
    shorthand for the two calls.

    Raises:
        image_roi.RoiError: if the content box has no area, which needs a frame
            with none.
    """
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
    """Describe a frame without decoding it.

    ``Image.open`` reads only the header, so listing the newest frame in the
    catalog costs a header read rather than a full decode of an image nobody has
    asked to crop yet.
    """
    try:
        with Image.open(path) as image:
            return describe(path, image)
    except OSError as exc:
        raise RoiExtractFailedError(
            f"{path.name} could not be read as an image: {exc}"
        ) from exc


def _save_crop(crop: Image.Image, source: Path) -> None:
    """Write a cash meter crop to disk, named after the frame it came from.

    Raises:
        RoiExtractFailedError: if the crop could not be written.
    """
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
            # Listed with its reason rather than dropped: a region missing from
            # the dropdown looks like a config that never declared it, which
            # sends whoever is looking to the wrong file.
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
    """The active game's regions, and the frame an extraction would use.

    What the panel renders before anything is extracted: the dropdown's options
    and the name of the shot they would be cut out of.

    Raises:
        GameConfigInvalidError: if the active game's config cannot be loaded.
        RoiRegionNotFoundError: if it declares no regions.
        RoiExtractFailedError: if the newest screenshot is not a readable image.
    """
    return await asyncio.to_thread(_catalog)


def encode_png(crop: Image.Image) -> str:
    """The crop as a PNG data URI.

    PNG regardless of the source format: a crop of a meter is about to be looked
    at closely, and re-encoding it as JPEG would add exactly the artefacts that
    make a region look badly aimed.

    Raises:
        RoiExtractFailedError: if the crop could not be encoded. Unlike OCR's
            equivalent this is fatal, because here the picture *is* the answer.
    """
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
        # Against the game rather than the canvas: the bars round a window
        # capture change width when the window is resized, and the region is
        # aimed at what is inside them.
        box, content = resolve_box(roi, frame)
        crop = frame.crop(box)
    except image_roi.RoiError as exc:
        # The region is declared but unusable. A config problem, so it names the
        # file it came out of rather than only the region.
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
    """Cut one region out of one frame and hand back the picture.

    Raises:
        BadRequestError: if a named frame is not a bare filename.
        GameConfigInvalidError: if the config cannot be loaded, or the region is
            declared with unusable numbers.
        RoiRegionNotFoundError: if the active game declares no such region.
        RoiFrameNotFoundError: if the frame to crop is not on disk.
        RoiExtractFailedError: if the frame is not a readable image, or the crop
            could not be encoded.
    """
    return await asyncio.to_thread(_extract, request)
