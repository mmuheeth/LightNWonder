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
named entry in the game config resolved by ``roi.content_box``/``Roi``, and
*which engine* reads is still ``services/ocr``'s question, asked through
``engine_for_reading``/``paddle_line_engine_for_reading``. Nothing about the
crop is this module's own.

Four things it exists to get right:

* **The frames are taken faster than the strip changes, and the dedupe is what
  counts messages.** What the interval has to clear is the *shortest* dwell,
  not the typical one: a sample lands inside every window at least as long as
  the interval, so sampling slower than the strip moves drops messages
  *silently* -- the one failure mode a reader could never detect. A message
  dwells 1.3-1.8s on FortuneOx and frames are taken every 1.0s, so every one
  of them is still caught; consecutive readings of one caption then collapse
  into one message, and it is that collapse -- never the interval -- that
  decides how many messages there were. What counts as "one caption" is
  :func:`_key`, and it is deliberately narrower than it could be.
* **Two engines, and the reading says which one answered.** PaddleOCR reads a
  caption by default and Tesseract is still selectable
  (``CYCLIC_MESSAGES_TEXT_ENGINE``), because they want opposite things of a
  crop: Tesseract needs it flattened to greyscale and upscaled 3x, which on
  text drawn over the game's artwork discards the colour separating the two,
  while Paddle's recogniser takes the crop as it is. They also differ in how
  they are *run* -- see :func:`_read_all` -- and in what a confidence means, so
  ``CyclicTextReading.engine`` travels on every reading rather than being
  inferred from the settings at the time someone reads it back.

  The one thing this module does own about an engine is Paddle's own options,
  which come from its settings mixin rather than from ``ocr.options_for``: a
  ``PaddleLineOptions`` shares no field with the ``ocr`` block a game
  overrides Tesseract with, so there is nothing there to resolve. Which
  *model* it names is the setting that decides both how right a caption is
  read and whether the clip can be read inside one request -- measured on real
  frames in ``CYCLIC_MESSAGES_TEXT_PADDLE_MODEL``.
* **A frame the recording ruined is reported, not dropped.** A caption the
  encoder smeared comes back as plausible nonsense -- "Line 32 Pays 25" as
  "Liss 32 Pups 29" -- at 45-61 confidence against 85-97 for a legible one on
  Tesseract. So every frame keeps its text *and* its figure, and ``reliable``
  says which side of the floor it fell. Dropping the bad ones would leave a
  reading that looks complete and is missing a third of the pass. Paddle's
  0-1 score is scaled onto that same 0-100 so one floor serves both -- but the
  floor was measured on Tesseract, so see the settings note before reading
  much into a Paddle frame's ``reliable``.
* **It holds no state**, like ``roi``/``grid``/``paylines`` and unlike its own
  feature's run engine -- a clip on disk is the same clip every time it is
  read, so there is nothing to cache and no ``reset()``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
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
    CyclicTextCaption,
    CyclicTextFrame,
    CyclicTextMessage,
    CyclicTextReading,
)
from app.services import cyclic_messages as cyclic_service
from app.services import ocr as ocr_service
from app.services import roi as roi_service
from app.utils import caption_text, image_roi, ocr, paddle_ocr, video_frames

logger = get_logger("cyclic_text")

PADDLE = "paddle"
TESSERACT = "tesseract"


@dataclass(frozen=True)
class _Read:
    """One frame's reading, before it is grouped with its neighbours."""

    index: int
    at_seconds: float
    text: str
    """Verbatim, as the engine returned it."""

    confidence: float
    """0-100. Paddle's own 0-1 is scaled onto Tesseract's range at the point of
    reading, so one floor serves both and a payload means one thing."""

    @property
    def repaired(self) -> str:
        """The reading put right against the strip's known wording.

        A property rather than a field so the raw text is the one thing a
        reader has to be given, and the repair is derived from it every time
        instead of being carried alongside and able to disagree.
        """
        return caption_text.repair(
            self.text,
            vocabulary=settings.cyclic_messages_text_vocabulary,
            number_after=settings.cyclic_messages_text_number_after,
        )

    @property
    def reliable(self) -> bool:
        return self.confidence >= settings.CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE


@dataclass(frozen=True)
class _Reader:
    """One engine, ready to be pointed at captions.

    ``workers`` is part of the engine rather than a setting applied to both:
    the two are not run the same way, and reading Paddle six at a time is not
    a tuning choice but a wrong one. See :func:`_read_all`.
    """

    name: str
    read: Callable[[_Caption], _Read]
    workers: int


@dataclass(frozen=True)
class _Engine:
    """The engine a request resolved, settled before a frame is decoded.

    It carries a factory rather than each engine's own fields because the two
    need different things and only one of them needs the game config: a
    Tesseract read takes the options a game may override per region, and a
    Paddle read takes a model that :func:`ocr_service.paddle_engine_for_reading`
    has already built.
    """

    name: str
    reader_for: Callable[[GameConfig, str], _Reader]


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
    1080x1920 frame is 6MB and a caption is a few kilobytes, so holding a 90s
    clip's worth of the first is over half a gigabyte and holding the same
    number of the second is nothing.

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


def _blank(caption: _Caption, exc: Exception) -> _Read:
    """A frame the engine refused, as a blank reading.

    Reported rather than raised because one bad frame is not a reason to lose
    the clip -- which is only safe because the engine was built and checked
    before the loop started. Without that pre-flight this is how a broken
    install would come back: every frame blank, indistinguishable from a strip
    that showed nothing.
    """
    logger.warning("Frame %d of the clip could not be read: %s", caption.index, exc)
    return _Read(caption.index, caption.at_seconds, "", 0.0)


def _worst(confidences: tuple[float, ...]) -> float:
    """The *lowest* per-word confidence, not the mean: one mangled word is what
    makes a caption wrong, and an average hides it behind the words that read
    well."""
    return min(confidences, default=0.0)


def _read_tesseract(
    caption: _Caption, *, executable: Path, options: ocr.OcrOptions
) -> _Read:
    """Read one caption with Tesseract."""
    try:
        result = ocr.read_image(caption.image, executable=executable, options=options)
    except ocr.OcrError as exc:
        return _blank(caption, exc)

    confidence = _worst(tuple(word.confidence for word in result.words))
    return _Read(caption.index, caption.at_seconds, result.text.strip(), confidence)


def _read_paddle(caption: _Caption, *, options: paddle_ocr.PaddleLineOptions) -> _Read:
    """Read one caption with PaddleOCR, recognise-only.

    ``read_line`` rather than ``read_image``: the crop already *is* the line,
    so running the detector over it is asking Paddle to rediscover the
    rectangle it was handed. That is also the expensive half -- see
    ``PaddleLineOptions.model_name`` for what the two cost.

    Taking the worst "word" is the same rule as the Tesseract path and means
    the same thing, though a recognise-only read returns just the one.
    """
    try:
        result = paddle_ocr.read_line(caption.image, options=options)
    except ocr.OcrError as exc:
        return _blank(caption, exc)

    # Scaled onto Tesseract's 0-100 at the point of reading, so the floor, the
    # `reliable` flag and the payload all mean one thing whichever engine
    # answered -- the same conversion `ocr._read_tile_paddle` makes for an orb.
    confidence = _worst(tuple(word.confidence * 100.0 for word in result.words))
    return _Read(caption.index, caption.at_seconds, result.text.strip(), confidence)


def _tesseract_reader(executable: Path, config: GameConfig, region: str) -> _Reader:
    """Tesseract, with the options this game reads this region at.

    The only branch that resolves any: the ``ocr`` block a game overrides is
    Tesseract's command line and preprocessing, and a ``PaddleOptions`` shares
    none of its fields.
    """
    options = ocr_service.options_for(config, region)
    return _Reader(
        name=TESSERACT,
        read=lambda caption: _read_tesseract(
            caption, executable=executable, options=options
        ),
        workers=settings.cyclic_messages_text_workers,
    )


def _paddle_reader(options: paddle_ocr.PaddleLineOptions) -> _Reader:
    """PaddleOCR, over the model already built for this request."""
    return _Reader(
        name=PADDLE,
        read=lambda caption: _read_paddle(caption, options=options),
        # One at a time, and not a tuning choice. `utils/paddle_ocr` keeps one
        # model for the whole process and Paddle's are not documented as
        # thread-safe, so several threads through them is a correctness
        # question rather than a throughput one. There would be little to win
        # anyway: a Tesseract read is a *subprocess* and `subprocess.run`
        # releases the GIL while it waits, which is what makes threads pay
        # there, while a Paddle read is in-process and already spreads itself
        # across cores.
        workers=1,
    )


def _read_all(captions: list[_Caption], reader: _Reader) -> list[_Read]:
    """Read every caption, as many at a time as the engine tolerates.

    The single-worker case takes no pool at all rather than a pool of one:
    Paddle is always that case, so this is the ordinary path and not a corner.
    """
    if not captions:
        return []
    workers = min(len(captions), max(1, reader.workers))
    if workers == 1:
        return [reader.read(caption) for caption in captions]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(reader.read, captions))


def _key(text: str) -> str:
    """What two readings have to share to be one message.

    Case, spacing and punctuation only -- ``"Line B Pays 250 +"`` and
    ``"line b pays250"`` are one caption read twice, and holding out for an
    exact match splits it into two rows a second apart.

    **Deliberately not tolerant of a wrong character**, which is the noise that
    remains and the tempting next step. It cannot be taken: two genuinely
    different captions here differ by exactly one character -- "Line 1 Pays
    250" against "Line 4 Pays 250" -- so any edit-distance slack wide enough to
    merge a misread is wide enough to merge two different lines, and a merge is
    a *lost message* rather than a duplicated one. Character noise is a reading
    problem, fixed by the model (see CYCLIC_MESSAGES_TEXT_PADDLE_MODEL), not a
    grouping one.
    """
    return "".join(character for character in text if character.isalnum()).casefold()


def _group(reads: list[_Read]) -> list[CyclicTextMessage]:
    """Collapse *consecutive* readings of one caption into one message each.

    A blank frame ends the message before it without starting one. That is the
    part worth getting right: the strip goes empty between presentations and
    the pass repeats, so the same line pays again on the next cycle round. If a
    blank were merely skipped, those two showings would fold into one message
    spanning the gap -- one entry claiming to have been on screen for a minute,
    and one showing of the line lost.

    A message keeps the *best-scoring* of the readings that formed it. Once
    grouping is by :func:`_key` the frames no longer agree character for
    character, so one of the variants has to be the one shown, and the frame
    the engine was surest of is the better bet than whichever came first.
    Every variant survives on ``frames`` regardless.
    """
    messages: list[CyclicTextMessage] = []
    keys: list[str] = []
    open_at: int | None = None
    """Where in ``messages`` the message still on screen is, or None if the
    strip is currently blank."""

    for read in reads:
        if not read.text:
            open_at = None
            continue
        repaired = read.repaired
        key = _key(repaired)
        if open_at is not None and keys[open_at] == key:
            running = messages[open_at]
            better = read.confidence > running.confidence
            messages[open_at] = running.model_copy(
                update={
                    "text": repaired if better else running.text,
                    "last_seen": read.at_seconds,
                    "frames": running.frames + 1,
                    "confidence": max(running.confidence, read.confidence),
                    "reliable": running.reliable or read.reliable,
                }
            )
            continue
        messages.append(
            CyclicTextMessage(
                text=repaired,
                first_seen=read.at_seconds,
                last_seen=read.at_seconds,
                frames=1,
                confidence=read.confidence,
                reliable=read.reliable,
            )
        )
        keys.append(key)
        open_at = len(messages) - 1
    return messages


def _distinct(messages: list[CyclicTextMessage]) -> list[CyclicTextCaption]:
    """Count the repeats instead of listing them.

    The strip loops, so one pass of a 40-line win says "Line 1 Pays 250" as
    many times as the clip runs round. Grouping by :func:`_key` again -- not by
    the displayed text -- so a caption that read two ways still counts once,
    and the variant shown is the one the engine was surest of anywhere.

    Ordered by first appearance rather than by count: the strip has an order
    and it is the order a reader is checking against.
    """
    captions: dict[str, CyclicTextCaption] = {}
    for message in messages:
        key = _key(message.text)
        seen = captions.get(key)
        if seen is None:
            captions[key] = CyclicTextCaption(
                text=message.text,
                showings=1,
                frames=message.frames,
                first_seen=message.first_seen,
                last_seen=message.last_seen,
                confidence=message.confidence,
                reliable=message.reliable,
            )
            continue
        better = message.confidence > seen.confidence
        captions[key] = seen.model_copy(
            update={
                "text": message.text if better else seen.text,
                "showings": seen.showings + 1,
                "frames": seen.frames + message.frames,
                "last_seen": max(seen.last_seen, message.last_seen),
                "confidence": max(seen.confidence, message.confidence),
                "reliable": seen.reliable or message.reliable,
            }
        )
    return list(captions.values())


def _read(
    run_id: str, cycle: int | None, interval: float, engine: _Engine
) -> CyclicTextReading:
    """Blocking half of :func:`read_run`: decode, crop, OCR, group."""
    detail = cyclic_service.get_run(run_id)
    clip, file_name = _clip_of(detail, cycle)
    path = cyclic_service.file_path(run_id, file_name)

    config = _config_for(detail.game)
    region_name = settings.CYCLIC_MESSAGES_TEXT_REGION
    region = _region_of(config, region_name)
    reader = engine.reader_for(config, region_name)

    info = video_frames.probe(path)
    captions = _captions(path, region, interval)
    reads = _read_all(captions, reader)

    messages = _group(reads)
    distinct = _distinct(messages)
    unreadable = sum(1 for read in reads if read.text and not read.reliable)
    logger.info(
        "Read %d frames of %s at %.2fs with %s: %d captions over %d showings, "
        "%d frames below the confidence floor",
        len(reads),
        file_name,
        interval,
        reader.name,
        len(distinct),
        len(messages),
        unreadable,
    )
    return CyclicTextReading(
        run_id=run_id,
        game=detail.game,
        file_name=file_name,
        cycle=clip.cycle,
        region=region_name,
        engine=reader.name,
        interval_seconds=interval,
        duration_seconds=info.duration_seconds,
        frames_sampled=len(reads),
        frames_unreadable=unreadable,
        captions=distinct,
        messages=messages,
        frames=[
            CyclicTextFrame(
                at_seconds=read.at_seconds,
                frame_index=read.index,
                text=read.text,
                repaired=read.repaired,
                confidence=read.confidence,
                reliable=read.reliable,
            )
            for read in reads
        ],
    )


def _paddle_options() -> paddle_ocr.PaddleLineOptions:
    """The options a caption is read with, straight out of the environment.

    ``PaddleLineOptions``, not the orb reader's ``PaddleOptions``: the two
    paths load different models and share no setting, which is also why the
    caption reader does not inherit the orb reader's cached engine.
    """
    return paddle_ocr.PaddleLineOptions(
        model_name=settings.CYCLIC_MESSAGES_TEXT_PADDLE_MODEL,
        upscale=settings.CYCLIC_MESSAGES_TEXT_PADDLE_UPSCALE,
    )


async def _resolve_engine() -> _Engine:
    """Settle which engine will read, and prove it will, before anything is
    decoded.

    Both branches build the engine rather than merely look for it, and both
    turn a missing one into a 409. That is the whole point of doing it here:
    once the per-frame loop starts, an engine failure is caught per frame and
    recorded as a blank, so a clip read by nothing is indistinguishable from a
    strip that showed nothing.
    """
    if settings.CYCLIC_MESSAGES_TEXT_ENGINE == TESSERACT:
        executable = await ocr_service.engine_for_reading()
        return _Engine(
            name=TESSERACT,
            reader_for=lambda config, region: _tesseract_reader(
                executable, config, region
            ),
        )

    options = _paddle_options()
    await ocr_service.paddle_line_engine_for_reading(options)
    return _Engine(
        name=PADDLE,
        reader_for=lambda _config, _region: _paddle_reader(options),
    )


async def read_run(
    run_id: str, *, cycle: int | None = None, interval_seconds: float | None = None
) -> CyclicTextReading:
    """Read every message out of one run's clip.

    The engine is resolved first and allowed to fail the request: a reading
    with no OCR behind it is a list of blank frames, which is worse than a 409
    saying the engine is not installed.
    """
    engine = await _resolve_engine()
    interval = interval_seconds or settings.CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS
    # Decoding a 90s clip and running ~90 OCR passes over it is squarely
    # blocking work, so it goes to a thread rather than stalling the loop --
    # the i-deck and the game clicks share this process.
    return await asyncio.to_thread(_read, run_id, cycle, interval, engine)
