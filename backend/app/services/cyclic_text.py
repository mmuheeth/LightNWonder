"""Read the messages back out of a finished cyclic-messages clip.

The strip's text is never logged, and a screenshot costs an OBS round trip of
1.5-7s, so only the clip catches every message; this cuts it into frames,
crops the caption, and OCRs it. Same joinery as ``services/roi.py`` and
``services/ocr.py``, one step on: the region is a named entry in the game
config, and readiness to read it is still ``services/ocr``'s question, asked
through ``paddle_line_engine_for_reading``.

Four things it exists to get right:

* **Sampling must clear the shortest dwell, not the typical one.** A sample
  lands inside every window at least as long as the interval, so sampling
  slower than the strip moves drops messages *silently*. A message dwells
  1.3-1.8s on FortuneOx and frames are taken every 1.0s, so all are caught;
  consecutive readings of one caption then collapse into one message via
  :func:`caption_key` -- that collapse, not the interval, decides the message
  count.
* **PaddleOCR is the only engine, and the reading still names it.**
  ``CyclicTextReading.engine`` is always ``"paddle"``, carried on every reading
  anyway so two readings of one clip are comparable without assuming that.
  Which Paddle *model* is named (``CYCLIC_MESSAGES_TEXT_PADDLE_MODEL``) decides
  both accuracy and whether the clip finishes inside one request.
* **A frame the recording ruined is reported, not dropped.** A caption the
  encoder smeared reads as plausible nonsense -- "Line 32 Pays 25" as "Liss 32
  Pups 29" -- at 45-61 confidence against 85-97 for a legible one on
  Tesseract, so every frame keeps its text *and* figure, and ``reliable`` says
  which side of the floor it fell (Paddle's 0-1 score scaled onto the same
  0-100).
* **It holds no state**, like ``roi``/``grid``/``paylines`` and unlike its own
  feature's run engine -- a clip on disk is the same clip every time it is
  read, so there is nothing to cache and no ``reset()``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy
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
from app.utils import caption_text, image_roi, paddle_ocr, video_frames

logger = get_logger("cyclic_text")

PADDLE = "paddle"


def repair(text: str) -> str:
    """One reading corrected against the strip's known wording, or "" if too
    short to be a caption.

    Module-level because both the clip reader and the live reader apply it,
    and two copies of "what the strip is allowed to say" would drift apart.
    """
    if not believable(text):
        return ""
    return caption_text.repair(
        text,
        vocabulary=settings.cyclic_messages_text_vocabulary,
        number_after=settings.cyclic_messages_text_number_after,
    )


def believable(text: str) -> bool:
    """Whether a reading is long enough to be a caption, not how confident it
    was -- an empty crop's recogniser answers with a character it thinks it
    found in the artwork rather than with nothing. Counts alnum characters
    against ``CYCLIC_MESSAGES_TEXT_MIN_CHARACTERS``: a stray "50" once
    outscored the confidence floor, which is why this doesn't use confidence.
    """
    characters = sum(1 for character in text if character.isalnum())
    return characters >= settings.CYCLIC_MESSAGES_TEXT_MIN_CHARACTERS


def reliable(confidence: float) -> bool:
    """Whether a reading cleared the confidence floor, on the 0-100 scale
    Paddle's own 0-1 is put onto at read time."""
    return confidence >= settings.CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE


@dataclass(frozen=True)
class _Read:
    """One frame's reading, before it is grouped with its neighbours."""

    index: int
    at_seconds: float
    text: str
    """Verbatim, as the engine returned it."""

    confidence: float
    """0-100 -- Paddle's own 0-1, scaled at the point of reading so the floor
    and the payload mean one thing regardless of where the scan came from."""

    @property
    def repaired(self) -> str:
        """The reading corrected against the strip's wording. A property, not
        a field, so the raw text stays authoritative and repair is derived
        fresh each time rather than carried alongside and able to disagree."""
        return repair(self.text)

    @property
    def reliable(self) -> bool:
        return reliable(self.confidence)


@dataclass(frozen=True)
class _Reader:
    """A model already built by :func:`ocr_service.paddle_line_engine_for_reading`,
    ready to be pointed at captions. ``workers`` lives here rather than as a
    shared setting: Paddle is CPU-bound against one shared model, so reading
    several captions at once is a wrong choice, not just an untuned one -- see
    :func:`_read_all`, which is why this is always ``1``.
    """

    name: str
    read: Callable[[_Caption], _Read]
    workers: int


def _clip_of(detail: CyclicRunDetail, cycle: int | None) -> tuple[CyclicRunVideo, str]:
    """The clip to read and its file name: the one covering ``cycle``, or the
    run's only one.

    Refused rather than guessed when a run holds several, since reading the
    wrong win produces a complete-looking answer about the wrong spin.
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
    """One frame cut down to just its caption, before anything reads it.

    ``images`` is one crop per line the strip draws, top first -- which lines
    those are depends on which strip the clip is of: see :func:`clip_regions`.
    """

    index: int
    at_seconds: float
    images: tuple[Image.Image, ...]


def _captions(
    path: Path, regions: tuple[image_roi.Roi, ...], interval: float
) -> list[_Caption]:
    """Decode the clip and keep only the caption bands out of each sampled frame.

    Cropping here is what makes the reading affordable: a 1080x1920 frame is
    ~6MB and a caption crop is kilobytes, so holding a 90s clip's frames whole
    would be over half a gigabyte. The content box is found per frame, not
    once for the clip, so a window that changes shape mid-clip doesn't crop
    the wrong place for the rest of its length.
    """
    captions: list[_Caption] = []
    for frame in video_frames.sample(path, interval_seconds=interval):
        content = roi_service.content_box(frame.image)
        crops = tuple(
            frame.image.crop(region.to_box_within(content.box)) for region in regions
        )
        captions.append(_Caption(frame.index, frame.at_seconds, crops))
    return captions


def _blank(caption: _Caption, exc: Exception) -> _Read:
    """A frame the engine refused, as a blank reading.

    Reported rather than raised -- one bad frame is not a reason to lose the
    clip, which is safe only because the engine was built and checked before
    the loop started (otherwise a broken install would come back as every
    frame blank, indistinguishable from a strip that showed nothing).
    """
    logger.warning("Frame %d of the clip could not be read: %s", caption.index, exc)
    return _Read(caption.index, caption.at_seconds, "", 0.0)


def clip_regions(kind: str) -> tuple[str, ...]:
    """Which of the strip's lines a clip of ``kind`` draws: one line for a win
    presentation, two for the between-spins strip, and both for
    ``win-then-idle`` (a win taken before the idle strip completes a round, so
    recording runs straight into it -- the unused band on either half simply
    reads empty and drops).

    Public because two callers need identical bands from the same file: this
    module's clip reader and ``cyclic_messages._recover``, which reads a clip
    back live -- otherwise a caption recovered live and the same caption read
    afterwards could disagree about which band it came from.
    """
    if kind in (cyclic_service.IDLE_VIDEO, cyclic_service.WIN_THEN_IDLE):
        return settings.cyclic_messages_idle_regions
    return settings.cyclic_messages_text_regions


def _joined(caption: _Caption, lines: list[tuple[str, float]]) -> _Read:
    """One frame's bands as a single reading -- two captions differing only on
    line 2 are two different moments, so a reading that kept just the first
    line would merge them.

    **Bands that read nothing are dropped, not joined as blanks**: the strip
    does not use every line at every moment (FortuneOx leaves the top one
    empty for a whole idle pass), so an unused band is not a bad reading, and
    letting its 0 through :func:`_worst` would mark the whole pass unreliable.
    """
    said = [(t, c) for t, c in lines if believable(t)]
    if not said:
        return _Read(caption.index, caption.at_seconds, "", 0.0)
    return _Read(
        caption.index,
        caption.at_seconds,
        " ".join(text for text, _ in said),
        _worst(tuple(confidence for _, confidence in said)),
    )


def _worst(confidences: tuple[float, ...]) -> float:
    """The *lowest* per-word confidence, not the mean: one mangled word is
    what makes a caption wrong, and an average would hide it."""
    return min(confidences, default=0.0)


def _read_paddle(caption: _Caption, *, options: paddle_ocr.PaddleLineOptions) -> _Read:
    """Read one caption with PaddleOCR, recognise-only -- the crop already
    *is* the line, so running the detector over it would ask Paddle to
    rediscover a rectangle it was already handed (see
    ``PaddleLineOptions.model_name`` for what that costs). Takes the worst
    "word", though recognise-only returns just the one.
    """
    lines: list[tuple[str, float]] = []
    for image in caption.images:
        try:
            result = paddle_ocr.read_line(image, options=options)
        except paddle_ocr.OcrError as exc:
            return _blank(caption, exc)
        # Scaled onto the 0-100 the floor/`reliable`/payload all use, at the
        # point of reading -- the same conversion `ocr._read_tile_paddle`
        # makes for an orb.
        lines.append(
            (
                result.text.strip(),
                _worst(tuple(word.confidence * 100.0 for word in result.words)),
            )
        )
    return _joined(caption, lines)


def _paddle_reader(options: paddle_ocr.PaddleLineOptions) -> _Reader:
    """PaddleOCR, over the model already built for this request."""
    return _Reader(
        name=PADDLE,
        read=lambda caption: _read_paddle(caption, options=options),
        # One at a time, not a tuning choice: `utils/paddle_ocr` keeps one
        # model per process and Paddle's are not documented thread-safe, so
        # more threads is a correctness risk, not a throughput one. Little to
        # win anyway -- Paddle runs in-process and already spreads itself
        # across cores.
        workers=1,
    )


def _plan(captions: list[_Caption]) -> list[int | None]:
    """For each sampled frame, whose reading it gets: its own, an earlier
    frame's, or none at all.

    **This is what makes reading a clip finish.** A caption stays up for
    several seconds while sampling is every one, so at ~1.5s per recognition,
    reading every frame of a 90s clip (~180 recognitions across two bands)
    lost the race against the client's 290s timeout. Here each caption the
    strip actually showed is read once.

    Three answers, one per frame: ``None`` -- no band is showing (an empty
    crop's recogniser invents a character rather than answering blank, so it's
    skipped without asking, which is also what keeps :func:`_group` splitting
    two showings either side of a gap); the frame's own index -- the caption
    changed; or an earlier index -- the picture repeated, judged by
    :data:`_SIGNATURE_REPEATED` (0.000 for an outright repeated frame against
    0.01-0.03 for two line messages differing by one glyph -- see
    :meth:`CaptionChanges.changed`).

    Every frame still appears on ``frames`` either way -- this decides only
    what gets *recognised*; ``frames_read`` vs ``frames_sampled`` reports it.
    """
    plan: list[int | None] = []
    current: Signature | None = None
    owner: int | None = None
    for index, caption in enumerate(captions):
        try:
            signature, showing = crop_signature(caption.images)
        except Exception as exc:  # noqa: BLE001 - a frame is not the clip
            # Read rather than skipped: this is only an optimisation, and the
            # safe way to be wrong about a frame is to spend a recognition on it.
            logger.warning("Could not compare frame %d: %s", caption.index, exc)
            plan.append(index)
            current, owner = None, None
            continue
        if not showing:
            plan.append(None)
            # Deliberately not cleared: the strip going blank between two
            # showings of one caption does not make the second a new picture,
            # and `_group` is what keeps them two showings rather than one.
            continue
        if owner is not None and current is not None and not signature.changed(current):
            plan.append(owner)
            continue
        current, owner = signature, index
        plan.append(index)
    return plan


def _read_planned(
    captions: list[_Caption], plan: list[int | None], reader: _Reader
) -> list[_Read]:
    """Read the frames :func:`_plan` chose, then hand their answers to the
    frames that share them.

    The engine still sees a plain list, so :func:`_read_all`'s own pooling
    logic is unaffected -- only the list length shrinks.
    """
    leaders = [index for index, owner in enumerate(plan) if owner == index]
    answers = dict(
        zip(
            leaders,
            _read_all([captions[index] for index in leaders], reader),
            strict=True,
        )
    )
    reads: list[_Read] = []
    for caption, owner in zip(captions, plan, strict=True):
        if owner is None:
            reads.append(_Read(caption.index, caption.at_seconds, "", 0.0))
            continue
        shared = answers[owner]
        reads.append(
            _Read(
                caption.index,
                caption.at_seconds,
                shared.text,
                shared.confidence,
            )
        )
    return reads


def _read_all(captions: list[_Caption], reader: _Reader) -> list[_Read]:
    """Read every caption, as many at a time as the engine tolerates.

    No pool at all for the single-worker case rather than a pool of one --
    Paddle is always that case, so this is the ordinary path, not a corner.
    """
    if not captions:
        return []
    workers = min(len(captions), max(1, reader.workers))
    if workers == 1:
        return [reader.read(caption) for caption in captions]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(reader.read, captions))


def caption_key(text: str) -> str:
    """What two readings have to share to be one message.

    Public because live capture groups by it too: a frame every 1.0s against
    a caption that dwells 1.3-1.8s catches most captions twice, and the same
    rule has to collapse them both ways or a clip read back and the stills of
    the same pass would disagree about how many messages there were.

    Folds case, spacing and punctuation only. **Deliberately not tolerant of a
    wrong character**: two genuinely different captions can differ by exactly
    one character ("Line 1 Pays 250" vs "Line 4 Pays 250"), so any
    edit-distance slack wide enough to merge a misread is wide enough to merge
    two different lines -- a *lost* message, not a duplicated one. Character
    noise is a reading problem, fixed by the model
    (``CYCLIC_MESSAGES_TEXT_PADDLE_MODEL``), not a grouping one.
    """
    return "".join(character for character in text if character.isalnum()).casefold()


def _group(reads: list[_Read]) -> list[CyclicTextMessage]:
    """Collapse *consecutive* readings of one caption into one message each.

    A blank frame ends the open message without starting one: the strip goes
    empty between presentations and the pass repeats, so if a blank were
    merely skipped, two showings of the same line a cycle apart would fold
    into one message spanning the gap.

    A message keeps the *best-scoring* of the readings that formed it, since
    grouping is by :func:`caption_key`, not exact match, so the frames aren't
    character-identical -- the surest reading is the better bet than whichever
    came first. Every variant still survives on ``frames``.
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
        key = caption_key(repaired)
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
    """Count the repeats instead of listing them -- a looping strip says the
    same caption once per pass.

    Grouped by :func:`caption_key` again, not by displayed text, so a caption
    that read two ways still counts once, keeping the variant the engine was
    surest of. Ordered by first appearance, since the strip has an order and
    that is the order a reader is checking against.
    """
    captions: dict[str, CyclicTextCaption] = {}
    for message in messages:
        key = caption_key(message.text)
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
    run_id: str, cycle: int | None, interval: float, reader: _Reader
) -> CyclicTextReading:
    """Blocking half of :func:`read_run`: decode, crop, OCR, group."""
    detail = cyclic_service.get_run(run_id)
    clip, file_name = _clip_of(detail, cycle)
    path = cyclic_service.file_path(run_id, file_name)

    config = _config_for(detail.game)
    # The lines *this* clip's strip draws, not one fixed band -- see
    # :func:`clip_regions`.
    names = clip_regions(clip.kind)
    if not names:
        raise GameConfigInvalidError(
            "No caption region is configured for this clip, so there is "
            "nothing to read it out of"
        )
    regions = tuple(_region_of(config, name) for name in names)
    region_name = ", ".join(names)

    info = video_frames.probe(path)
    captions = _captions(path, regions, interval)
    # Which frames are worth a recognition at all -- see :func:`_plan`: most
    # of a clip's frames just repeat the one before them.
    plan = _plan(captions)
    reads = _read_planned(captions, plan, reader)

    messages = _group(reads)
    distinct = _distinct(messages)
    unreadable = sum(1 for read in reads if read.text and not read.reliable)
    recognised = sum(1 for index, owner in enumerate(plan) if owner == index)
    logger.info(
        "Read %d of %d frames of %s at %.2fs with %s: %d captions over %d "
        "showings, %d frames below the confidence floor",
        recognised,
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
        frames_read=recognised,
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

    ``PaddleLineOptions``, not the orb reader's ``PaddleOptions`` -- the two
    load different models and share no setting, which is also why the caption
    reader does not inherit the orb reader's cached engine.
    """
    return paddle_ocr.PaddleLineOptions(
        model_name=settings.CYCLIC_MESSAGES_TEXT_PADDLE_MODEL,
        upscale=settings.CYCLIC_MESSAGES_TEXT_PADDLE_UPSCALE,
    )


async def _resolve_reader() -> _Reader:
    """Build the reader and prove it will run, before anything is decoded --
    once the per-frame loop starts, a read failure is caught per frame and
    recorded as a blank, so a clip read by nothing would otherwise be
    indistinguishable from a strip that showed nothing."""
    options = _paddle_options()
    await ocr_service.paddle_line_engine_for_reading(options)
    return _paddle_reader(options)


async def read_run(
    run_id: str, *, cycle: int | None = None, interval_seconds: float | None = None
) -> CyclicTextReading:
    """Read every message out of one run's clip.

    The reader is resolved first and allowed to fail the request: a reading
    with no OCR behind it is a list of blank frames, which is worse than a 409
    saying the engine is not installed.
    """
    reader = await _resolve_reader()
    interval = interval_seconds or settings.CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS
    # Decoding a 90s clip and running ~90 OCR passes over it is squarely
    # blocking work, so it goes to a thread rather than stalling the loop --
    # the i-deck and the game clicks share this process.
    return await asyncio.to_thread(_read, run_id, cycle, interval, reader)


# --- reading a frame while the run is still going -------------------------
#
# Answers "what did the strip say", frame by frame, while the run is still
# going, off the PNGs the capture loop is writing -- so a tester watching a
# pass sees each caption named beside its own screenshot instead of waiting
# for the clip.
#
# Same region, same :func:`repair`, same :func:`reliable` as the clip reader,
# so a live reading should differ from a clip reading only because the
# picture differed (a still against an encoded frame), never because two
# readers disagreed about what the strip is allowed to say.
#
# Paddle only, deliberately: an in-process model is what lets this reader keep
# pace with the capture loop, which a subprocess started fresh per frame could
# not do.


CAPTION_CROP_SUFFIX = "_caption.png"
"""Appended to a frame's stem for the crop written beside it."""


@dataclass(frozen=True)
class LiveRead:
    """One *line* of a captured frame's strip, read from the file OBS wrote.

    One of these per configured region, not per frame -- the strip is
    stacked, and each line is cropped and recognised on its own.
    """

    region: str
    """Which named region this line was cropped from."""

    text: str
    """Verbatim, as Paddle returned it."""

    repaired: str
    confidence: float
    """0-100, Paddle's own 0-1 scaled onto the range the floor is written in."""

    reliable: bool
    crop_name: str | None
    """The caption crop written beside the frame, or None if it could not be."""

    read_ms: int


@dataclass(frozen=True)
class LiveReader:
    """A caption reader prepared once and then pointed at frame after frame.

    Built by :func:`live_reader`, which resolves the region and *builds the
    model* first -- the same pre-flight :func:`_resolve_engine` does, so a
    reader that was never going to work fails loudly once instead of coming
    back as a pass of blank captions. Frozen and stateless, so the one
    instance a run makes is reused for every frame in it.
    """

    game: str
    regions: dict[str, image_roi.Roi]
    """Every line either window may ask for, by name.

    Resolved once for the run because the two windows show different strips
    (a win presentation draws one line, the between-spins strip two); which of
    these a frame wants is decided by the caller and passed to :meth:`read`.
    """

    model: str
    """The Paddle recognition model that will read, as it reported itself."""

    options: paddle_ocr.PaddleLineOptions

    def read(self, path: Path, names: tuple[str, ...]) -> list[LiveRead]:
        """Crop every line of the strip out of one written frame and read each.

        Blocking. Raises rather than returning blanks on failure -- unlike the
        clip reader's :func:`_blank`, because a live frame's error belongs on
        that frame where it can be seen, not averaged into a run of empty
        captions.

        ``names`` is which lines to read, in draw order -- reading a band the
        strip isn't using costs a recogniser pass for nothing, which is why
        the win presentation asks for one line and the between-spins strip
        for two. The frame is opened and its content box found *once* for all
        the lines, since letterbox detection scans the whole picture anyway.
        """
        return self.read_image(roi_service.open_frame(path), names, beside=path)

    def regions_in(
        self, names: tuple[str, ...]
    ) -> tuple[tuple[str, image_roi.Roi], ...]:
        """The named regions, paired with their rectangles, for a caller that
        wants to *look* at the bands rather than read them."""
        return tuple(
            (name, self.regions[name]) for name in names if name in self.regions
        )

    def read_image(
        self, image: Image.Image, names: tuple[str, ...], *, beside: Path
    ) -> list[LiveRead]:
        """The same read, over a frame already in hand -- the door for a frame
        that never was a file of its own, such as one decoded out of a clip.
        ``beside`` is only where that frame's caption crops are written.
        """
        started = time.perf_counter()
        # Per frame rather than once for the clip, exactly as :func:`_captions`
        # does it: cheap next to the decode, and guards against the window
        # changing shape part-way through.
        content = roi_service.content_box(image)

        reads: list[LiveRead] = []
        for name in names:
            roi = self.regions.get(name)
            if roi is None:
                continue
            crop = image.crop(roi.to_box_within(content.box))
            crop_name = _write_crop(crop, beside, name)
            if not crop_signature((crop,))[1]:
                # Nothing drawn on this band. Reading it anyway cost ~3s a
                # band under load for an answer the character floor then
                # discards -- measured over 256 real band crops, every
                # caption scored 25.3+ against 10.4 at most for an empty band
                # (`_SIGNATURE_INK`).
                reads.append(
                    LiveRead(
                        region=name,
                        text="",
                        repaired="",
                        confidence=0.0,
                        reliable=False,
                        crop_name=crop_name,
                        read_ms=round((time.perf_counter() - started) * 1000),
                    )
                )
                continue
            result = paddle_ocr.read_line(crop, options=self.options)
            confidence = _worst(tuple(word.confidence * 100.0 for word in result.words))
            text = result.text.strip()
            reads.append(
                LiveRead(
                    region=name,
                    text=text,
                    repaired=repair(text),
                    confidence=confidence,
                    reliable=reliable(confidence),
                    crop_name=crop_name,
                    read_ms=round((time.perf_counter() - started) * 1000),
                )
            )
        return reads


def _write_crop(crop: Image.Image, frame: Path, region: str) -> str | None:
    """Leave one line's crop beside the frame it came out of -- evidence, not
    output: a misread and a badly aimed rectangle look identical until
    somebody sees the pixels the recogniser was given. Named off the frame's
    own stem *and* the region so a strip's lines sort together. Never raises:
    losing the crop costs the diagnosis, not the reading.
    """
    target = frame.with_name(f"{frame.stem}_{region}{CAPTION_CROP_SUFFIX}")
    try:
        crop.save(target, format="PNG")
    except (OSError, ValueError) as exc:
        logger.warning("Could not write the caption crop at %s: %s", target, exc)
        return None
    return target.name


# --- noticing when the strip has come round ------------------------------
#
# The between-spins strip loops the same few messages until somebody spins,
# so capturing to a deadline means capturing the same captions repeatedly.
# Stopping after one lap needs knowing when it repeats, but the capture loop
# can't ask what a caption *says* to know that -- reading is 1.5s a frame and
# deliberately does not run until the window has closed.
#
# So the loop is spotted by the picture instead: two crops of one caption
# reduce to the same normalised grid (zero mean, unit deviation, so a fade
# between messages doesn't read as a new one) and two different captions
# don't, by a wide margin -- measured on real FortuneOx frames:
#
#   the same caption, 0.4s and 2.0s apart      0.000, 0.000
#   different captions of the same strip       0.135 - 0.593
#
# That gap looks comfortable, but the strip's tightest real pair sits far
# closer than it suggests -- see `_SIGNATURE_SAME`, measured on consecutive
# line messages, two orders of magnitude below what this population implies.
_SIGNATURE_GRID = (64, 16)
_SIGNATURE_REPEATED = 0.005
"""Below which two frames are the same picture and reading both is wasted.

Measured over a real 29-frame win clip: a frame the encoder repeated outright
scores **0.000**, and the smallest genuine change (one glyph, "Line 1 Pays 15"
becoming "Line 2 Pays 15") scores **0.01**. The cut sits nearer the bottom
because the two errors aren't equal: a repeat costs a second and a half, a
skip costs a message nobody will know was missed.
"""

_SIGNATURE_INK = 20.0
"""Least contrast a band needs before it counts as showing a caption, rather
than the gap between two. Measured on real frames: 32-38 with a message on
it, 10.0 caught mid-change, 6.4 for a band this strip never uses -- the cut
sits between, with a wide margin either side.
"""
_SIGNATURE_SAME = 0.003
"""Mean absolute difference below which two bands show the same caption.

**Per band, never across bands** -- see :func:`crop_signature`.

Re-measured over every band-1 pair of a real 68-frame run (2278 of them):
same caption 1-40s apart scored 0.0000-0.0010 (n=33), different captions
0.0046-0.2684 (n=2245). The first cut (0.05) called 1350 of those 2245
different pairs the same caption -- "Line 36 Pays 25" against "Line 38 Pays
25" scores 0.0046 -- which is what let :class:`LoopWatch` declare a lap closed
early. This sits in the measured gap: three times the largest "same" reading,
two thirds of the smallest "different" one.

Erring low is deliberate: too strict just runs a window to its deadline; too
slack closes a lap early and loses a message nobody will know was missed.
"""


def strip_rois(
    game: str, names: tuple[str, ...]
) -> tuple[tuple[str, image_roi.Roi], ...]:
    """The named regions of one game, resolved without starting an OCR
    engine -- for a caller like :class:`LoopWatch` that only wants to *look*
    at the caption bands, from inside the capture loop where an engine has no
    business being.
    """
    config = _config_for(game)
    return tuple((name, _region_of(config, name)) for name in names)


@dataclass
class LoopWatch:
    """Notices when the strip has shown everything it has and come round again.

    Fed one written frame at a time; :meth:`saw` answers "has the strip now
    been round once" by counting *distinct consecutive* captions, so joining
    the strip part-way through its cycle still measures a whole lap.
    """

    regions: tuple[tuple[str, image_roi.Roi], ...]
    """The bands whose cycle decides the lap -- *not* every band the strip
    has. See ``CYCLIC_MESSAGES_IDLE_LOOP_REGIONS``: the bands cycle
    independently and at different lengths, so watching both answers the
    question of neither.
    """

    seen: list[Signature] = field(default_factory=list)
    """One signature per distinct caption, in the order the strip showed them."""

    current: Signature | None = None
    """The caption on screen, so the repeats of it in a row are not counted."""

    blank: int = 0
    """Frames passed over as the gap between two messages; reported only so a
    pass that saw nothing but gaps is recognisable as such."""

    def saw(self, path: Path) -> bool:
        """Take one frame in; return whether the strip has completed a lap.

        Cheap on purpose: reducing two thin bands to a 64x16 grid is
        milliseconds against the 1.5s an OCR pass costs, which is the whole
        reason the loop can be spotted here and the reading left for later.
        Never raises: an unreadable frame is not evidence the strip has come
        round, so it's passed over and the window runs on to its deadline.
        """
        try:
            signature, showing = self._signature(path)
        except Exception as exc:  # noqa: BLE001 - never worth ending a pass over
            logger.warning("Could not read %s to watch for the loop: %s", path, exc)
            return False

        if not showing:
            # The gap between two messages, not a message. Counting it would
            # invent a caption that was never shown and could close the lap
            # before the strip has been round, so it's passed over entirely --
            # it never becomes `current`.
            self.blank += 1
            return False

        if self.current is not None and signature.alike(self.current):
            return False
        self.current = signature

        if self.seen and signature.alike(self.seen[0]):
            # Back to the *first* caption seen = a whole lap, however far
            # into the cycle the window opened. Requires >=2 seen (a strip
            # sitting on one message hasn't looped, it just hasn't changed)
            # and checks the first specifically, not any seen -- a message
            # repeated part-way round, or a frame OBS was too slow to catch,
            # would otherwise look like a return and close the lap early.
            return len(self.seen) >= 2
        if not any(signature.alike(earlier) for earlier in self.seen):
            self.seen.append(signature)
        return False

    def _signature(self, path: Path) -> tuple[Signature, bool]:
        return band_signature(roi_service.open_frame(path), self.regions)


def band_signature(
    image: Image.Image, regions: tuple[tuple[str, image_roi.Roi], ...]
) -> tuple[Signature, bool]:
    """One frame's caption bands as a signature, and whether any is showing --
    the cheap stand-in for reading a frame, used both to notice the strip
    coming round (:class:`LoopWatch`) and to pick out the frames worth
    reading at all (:class:`CaptionChanges`).
    """
    content = roi_service.content_box(image)
    return crop_signature(
        tuple(image.crop(roi.to_box_within(content.box)) for _, roi in regions)
    )


def crop_signature(crops: tuple[Image.Image, ...]) -> tuple[Signature, bool]:
    """The same signature, off caption crops that have already been made --
    split out because the clip reader cuts its crops once before anything
    looks at them (:func:`_captions`), and re-cropping a 1080x1920 frame per
    comparison would be most of what a comparison costs.

    The bands are kept **apart** rather than concatenated into one grid --
    see :class:`Signature` for why.
    """
    grids = []
    showing = False
    for crop in crops:
        grid = numpy.asarray(
            crop.convert("L").resize(_SIGNATURE_GRID), dtype=numpy.float32
        )
        # Read before normalising: normalising divides by the grid's own
        # deviation, which would turn a flat blank band into noise
        # indistinguishable from text.
        showing = showing or bool(grid.std() >= _SIGNATURE_INK)
        grids.append((grid - grid.mean()) / (grid.std() + 1e-6))
    return Signature(bands=tuple(grids)), showing


@dataclass(frozen=True)
class Signature:
    """One frame's caption bands, each reduced to a grid and kept separate.

    **Separate is the point, and joining them is a measured bug.** The
    strip's bands cycle independently, so concatenating them and averaging
    dilutes a real change with a zero from the band that didn't move: on a
    real run "Line 35 Pays 25" becoming "Line 37 Pays 25" scored 0.0298 on its
    own band but **0.0149** once the unchanged band was averaged in, which
    fell under :data:`_SIGNATURE_SAME` where the undiluted score didn't --
    how a between-spins window once closed while "GAME PAYS 1000" was still
    to come. Band 2 alone separates captions at 0.326-0.914, diluted with
    band 1 only 0.168.

    So a comparison is per band: :meth:`alike` is *every* band (a lap has
    closed only if nothing changed) and :meth:`changed` is *any* band (a
    frame is worth reading if anything did).
    """

    bands: tuple[Any, ...]

    def _scores(self, other: Signature) -> tuple[float, ...]:
        """Each band's mean absolute difference from the same band of
        ``other``. Raises on a mismatched band count -- two frames of one run
        always have the same bands, so a mismatch is a bug, not a frame to
        skip."""
        if len(self.bands) != len(other.bands):
            raise ValueError(
                f"cannot compare {len(self.bands)} bands against {len(other.bands)}"
            )
        return tuple(
            float(numpy.abs(mine - theirs).mean())
            for mine, theirs in zip(self.bands, other.bands, strict=True)
        )

    def alike(self, other: Signature) -> bool:
        """Whether this is the same caption as ``other`` -- *every* band must
        agree; one band changed means the strip is showing something new."""
        return all(score < _SIGNATURE_SAME for score in self._scores(other))

    def changed(self, other: Signature) -> bool:
        """Whether this frame shows something ``other`` did not -- *any*
        band. The strict cousin of :meth:`alike`: see
        :data:`_SIGNATURE_REPEATED`."""
        return any(score >= _SIGNATURE_REPEATED for score in self._scores(other))


@dataclass
class CaptionChanges:
    """Picks the frames of a clip where the caption actually changed.

    Reading every sampled frame is mostly re-reading the same caption -- a
    29-frame win presentation holding five messages is 24 repetitions -- and
    at ~1.5s an OCR pass that's the difference between reading a clip in ten
    seconds and in eighty, long enough to still be going when the next spin
    needs the run's attention. Frames caught in the gap between two messages
    are skipped for the same reason as in :class:`LoopWatch`.
    """

    regions: tuple[tuple[str, image_roi.Roi], ...]
    current: Signature | None = None

    def changed(self, image: Image.Image) -> bool:
        """Whether this frame shows a caption the one before it did not.

        Judged far more strictly than :class:`LoopWatch` judges two captions
        alike: that asks "is this the caption I saw earlier" across seconds
        and can afford slack, but here slack costs a *message* -- the strip's
        consecutive captions differ by a single glyph (0.01-0.03) against the
        0.000 of a frame the encoder repeated outright, and anything loose
        enough to be comfortable would merge two line messages into one.
        """
        try:
            signature, showing = band_signature(image, self.regions)
        except Exception as exc:  # noqa: BLE001 - a frame is not the clip
            logger.warning("Could not compare a clip frame: %s", exc)
            return True
        if not showing:
            return False
        if self.current is not None and not signature.changed(self.current):
            return False
        self.current = signature
        return True


@dataclass(frozen=True)
class ClipFrame:
    """One frame decoded out of a clip, with the whole picture kept -- unlike
    :class:`_Caption`, which throws the frame away, so a message recovered
    from the clip can be shown beside its own screenshot like a live one.
    """

    index: int
    at_seconds: float
    image: Image.Image


def clip_frames(path: Path, interval: float) -> Generator[ClipFrame, None, None]:
    """Every frame of a clip at ``interval``, whole rather than cropped.

    A generator, like the sampler it wraps: a clip's worth of full frames is
    hundreds of megabytes held at once and nothing here needs two of them.
    """
    for frame in video_frames.sample(path, interval_seconds=interval):
        yield ClipFrame(frame.index, frame.at_seconds, frame.image)


async def live_reader(game: str) -> LiveReader:
    """Prepare a caption reader for one game, proving the engine starts first.

    Raises the same 409 the clip reader does when PaddleOCR is missing or OCR
    is off, and the same 500 when the game declares no such region --
    ``services/cyclic_messages`` catches both and notes them rather than
    failing the run.
    """
    config = _config_for(game)
    # Both windows' lines, resolved together: a region only the between-spins
    # strip uses should still fail loudly here, when the run starts, rather
    # than on the first frame after somebody's first losing spin.
    names = tuple(
        dict.fromkeys(
            settings.cyclic_messages_text_regions
            + settings.cyclic_messages_idle_regions
        )
    )
    if not names:
        raise GameConfigInvalidError(
            "Neither CYCLIC_MESSAGES_TEXT_REGIONS nor "
            "CYCLIC_MESSAGES_IDLE_REGIONS names a region, so there is nothing "
            "to read the strip out of"
        )
    regions = {name: _region_of(config, name) for name in names}
    options = paddle_ocr.PaddleLineOptions(
        model_name=settings.CYCLIC_MESSAGES_LIVE_PADDLE_MODEL,
        # Shared with the clip reader's setting rather than given its own: 2x
        # is a property of how thin the caption band is, the same band
        # whichever picture it was cut out of.
        upscale=settings.CYCLIC_MESSAGES_TEXT_PADDLE_UPSCALE,
    )
    model = await ocr_service.paddle_line_engine_for_reading(options)
    logger.info(
        "Live caption reader ready for %s: %d line(s) (%s), PaddleOCR %s",
        game,
        len(regions),
        ", ".join(f"roi.{name}" for name in regions),
        model,
    )
    return LiveReader(game=game, regions=regions, model=model, options=options)
