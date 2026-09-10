"""Read the messages back out of a finished cyclic-messages clip.

The strip's *text* is never logged -- that is the premise the whole cyclic
messages feature rests on -- and a screenshot costs an OBS round trip of
1.5-7s, so the stills a run takes catch only a fraction of a pass. The clip is
the one record with every message in it. This cuts it into frames, crops the
caption out of each, and reads it.

It is the same joinery as ``services/roi.py`` and ``services/ocr.py``, one step
further along: ROI asks *is this rectangle aimed right*, OCR asks *what does
that crop say*, and this asks *what did the strip say over time* -- each
reading the previous one's answer rather than redoing it. So the region is a
named entry in the game config resolved by ``roi.content_box``/``Roi``, and the
options and the engine both come from ``services/ocr`` rather than being built
here. Nothing about the crop or the engine is this module's own.

Three things it exists to get right:

* **The frames are taken faster than the strip changes, and the dedupe is what
  counts messages.** A message dwells 1.3-1.8s on FortuneOx; sampling at about
  that rate aliases and drops messages *silently*, which is the one failure
  mode a reader could never detect. So frames are taken ~3x faster and
  consecutive identical readings collapse into one message afterwards.
* **A frame the recording ruined is reported, not dropped.** A caption the
  encoder smeared comes back as plausible nonsense -- "Line 32 Pays 25" as
  "Liss 32 Pups 29" -- at 45-61 confidence against 85-97 for a legible one. So
  every frame keeps its text *and* its figure, and ``reliable`` says which side
  of the floor it fell. Dropping the bad ones would leave a reading that looks
  complete and is missing a third of the pass.
* **It holds no state**, like ``roi``/``grid``/``paylines`` and unlike its own
  feature's run engine -- a clip on disk is the same clip every time it is
  read, so there is nothing to cache and no ``reset()``.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.config.runtime import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    BadRequestError,
    CyclicMessagesRunNotFoundError,
    GameConfigInvalidError,
)
from app.schemas.cyclic_messages import (
    CyclicRunDetail,
    CyclicRunVideo,
    CyclicTextFrame,
    CyclicTextMessage,
    CyclicTextReading,
)
from app.services import cyclic_messages as cyclic_service
from app.services import ocr as ocr_service
from app.services import roi as roi_service
from app.utils import image_roi, ocr, video_frames

logger = get_logger("cyclic_text")


@dataclass(frozen=True)
class _Read:
    """One frame's reading, before it is grouped with its neighbours."""

    index: int
    at_seconds: float
    text: str
    confidence: float

    @property
    def reliable(self) -> bool:
        return self.confidence >= settings.CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE


def _clip_of(detail: CyclicRunDetail, cycle: int | None) -> tuple[CyclicRunVideo, str]:
    """The clip to read and its file name: the one covering ``cycle``, or the
    run's only one.

    A run holds one clip per win presentation, so a run with several needs
    telling which -- refused rather than guessed, since reading the wrong win
    produces a complete-looking answer about the wrong spin. The name travels
    back beside the clip because a clip without one was never filed.
    """
    saved = [(video, video.file_name) for video in detail.videos if video.file_name]
    if not saved:
        raise CyclicMessagesRunNotFoundError(
            f"Cyclic message run {detail.run_id!r} has no clip to read. "
            "Only a run that saw a win records one."
        )
    available = ", ".join(str(video.cycle) for video, _ in saved)
    if cycle is not None:
        for video, name in saved:
            if video.cycle == cycle:
                return video, name
        raise CyclicMessagesRunNotFoundError(
            f"Cyclic message run {detail.run_id!r} has no clip for sequence "
            f"{cycle} (it has: {available})"
        )
    if len(saved) > 1:
        raise BadRequestError(
            f"Cyclic message run {detail.run_id!r} has {len(saved)} clips "
            f"(sequences {available}); name one with 'cycle'"
        )
    return saved[0]


def _config_for(game: str) -> GameConfig:
    """The config of the game the *run* played, which need not be the one
    selected now -- a clip is read long after the spin that made it."""
    path = settings.ideck_game_config_path_for(game)
    try:
        return load_game_config(path)
    except GameConfigError as exc:
        raise GameConfigInvalidError(
            f"Could not load game config {path}: {exc}"
        ) from exc


def _region_of(config: GameConfig, region: str) -> image_roi.Roi:
    """The caption's rectangle, or an explanation naming what the game does have."""
    try:
        return image_roi.named_roi(config.roi, region)
    except image_roi.RoiError as exc:
        known = ", ".join(sorted(config.roi)) or "none"
        raise GameConfigInvalidError(
            f"{config.path}: cannot read the cyclic message text without a "
            f"'roi.{region}' region ({exc}). Regions this game declares: {known}"
        ) from exc


@dataclass(frozen=True)
class _Caption:
    """One frame cut down to just its caption, before anything reads it."""

    index: int
    at_seconds: float
    image: Image.Image


def _captions(path: Path, region: image_roi.Roi, interval: float) -> list[_Caption]:
    """Decode the clip and keep only the caption out of each sampled frame.

    Cropping here rather than after is what makes the reading affordable: a
    1080x1920 frame is 6MB and a caption is a few kilobytes, so holding 180 of
    the first would be over a gigabyte and holding 180 of the second is nothing.

    The content box is found per frame rather than once for the clip. It costs
    little beside the decode, and a clip whose window changed shape part-way
    through would otherwise crop the wrong place for the rest of its length.
    """
    captions: list[_Caption] = []
    for frame in video_frames.sample(path, interval_seconds=interval):
        content = roi_service.content_box(frame.image)
        crop = frame.image.crop(region.to_box_within(content.box))
        captions.append(_Caption(frame.index, frame.at_seconds, crop))
    return captions


def _read_caption(
    caption: _Caption, *, executable: Path, options: ocr.OcrOptions
) -> _Read:
    """Read one caption, reporting a failure as a blank frame rather than
    abandoning the clip over it."""
    try:
        result = ocr.read_image(caption.image, executable=executable, options=options)
    except ocr.OcrError as exc:
        logger.warning("Frame %d of the clip could not be read: %s", caption.index, exc)
        return _Read(caption.index, caption.at_seconds, "", 0.0)

    text = result.text.strip()
    # The *lowest* word confidence, not the mean: one mangled word is what makes
    # a caption wrong, and an average hides it behind the words that read well.
    confidence = min((word.confidence for word in result.words), default=0.0)
    return _Read(caption.index, caption.at_seconds, text, confidence)


def _read_all(
    captions: list[_Caption], *, executable: Path, options: ocr.OcrOptions
) -> list[_Read]:
    """Read every caption, several at a time.

    Concurrent for the reason ``utils/meter.py`` gives: each read is a Tesseract
    *subprocess* and ``subprocess.run`` releases the GIL while it waits, so
    threads genuinely overlap here. Serially a 90s clip is about ninety seconds
    of reading; this is what keeps it in the same order as the decode.
    """
    if not captions:
        return []
    workers = min(len(captions), settings.cyclic_messages_text_workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(
            pool.map(
                lambda caption: _read_caption(
                    caption, executable=executable, options=options
                ),
                captions,
            )
        )


def _group(reads: list[_Read]) -> list[CyclicTextMessage]:
    """Collapse *consecutive* identical readings into one message each.

    A blank frame ends the message before it without starting one. That is the
    part worth getting right: the strip goes empty between presentations and
    the pass repeats, so the same line pays again on the next cycle round. If a
    blank were merely skipped, those two showings would fold into one message
    spanning the gap -- one entry claiming to have been on screen for a minute,
    and one showing of the line lost.
    """
    messages: list[CyclicTextMessage] = []
    open_at: int | None = None
    """Where in ``messages`` the message still on screen is, or None if the
    strip is currently blank."""

    for read in reads:
        if not read.text:
            open_at = None
            continue
        running = messages[open_at] if open_at is not None else None
        if running is not None and running.text == read.text:
            messages[open_at] = running.model_copy(  # type: ignore[index]
                update={
                    "last_seen": read.at_seconds,
                    "frames": running.frames + 1,
                    "confidence": max(running.confidence, read.confidence),
                    "reliable": running.reliable or read.reliable,
                }
            )
            continue
        messages.append(
            CyclicTextMessage(
                text=read.text,
                first_seen=read.at_seconds,
                last_seen=read.at_seconds,
                frames=1,
                confidence=read.confidence,
                reliable=read.reliable,
            )
        )
        open_at = len(messages) - 1
    return messages


def _read(
    run_id: str, cycle: int | None, interval: float, executable: Path
) -> CyclicTextReading:
    """Blocking half of :func:`read_run`: decode, crop, OCR, group."""
    detail = cyclic_service.get_run(run_id)
    clip, file_name = _clip_of(detail, cycle)
    path = cyclic_service.file_path(run_id, file_name)

    config = _config_for(detail.game)
    region_name = settings.CYCLIC_MESSAGES_TEXT_REGION
    region = _region_of(config, region_name)
    options = ocr_service.options_for(config, region_name)

    info = video_frames.probe(path)
    captions = _captions(path, region, interval)
    reads = _read_all(captions, executable=executable, options=options)

    messages = _group(reads)
    unreadable = sum(1 for read in reads if read.text and not read.reliable)
    logger.info(
        "Read %d frames of %s at %.2fs: %d messages, %d frames below the "
        "confidence floor",
        len(reads),
        file_name,
        interval,
        len(messages),
        unreadable,
    )
    return CyclicTextReading(
        run_id=run_id,
        game=detail.game,
        file_name=file_name,
        cycle=clip.cycle,
        region=region_name,
        interval_seconds=interval,
        duration_seconds=info.duration_seconds,
        frames_sampled=len(reads),
        frames_unreadable=unreadable,
        messages=messages,
        frames=[
            CyclicTextFrame(
                at_seconds=read.at_seconds,
                frame_index=read.index,
                text=read.text,
                confidence=read.confidence,
                reliable=read.reliable,
            )
            for read in reads
        ],
    )


async def read_run(
    run_id: str, *, cycle: int | None = None, interval_seconds: float | None = None
) -> CyclicTextReading:
    """Read every message out of one run's clip.

    The engine is resolved first and allowed to fail the request: a reading
    with no OCR behind it is a list of blank frames, which is worse than a 409
    saying Tesseract is not installed.
    """
    executable = await ocr_service.engine_for_reading()
    interval = interval_seconds or settings.CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS
    # Decoding a 90s clip and shelling out to Tesseract ~180 times is squarely
    # blocking work, so it goes to a thread rather than stalling the loop --
    # the i-deck and the game clicks share this process.
    return await asyncio.to_thread(_read, run_id, cycle, interval, executable)
