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
  :func:`caption_key`, and it is deliberately narrower than it could be.
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
from app.utils import caption_text, image_roi, ocr, paddle_ocr, video_frames

logger = get_logger("cyclic_text")

PADDLE = "paddle"
TESSERACT = "tesseract"


def repair(text: str) -> str:
    """One reading put right against the strip's known wording, or nothing at
    all if it was too short to be a caption.

    Module-level rather than a method because both readers apply it -- the
    clip reader per decoded frame and the live reader per captured frame --
    and two copies of "what the strip is allowed to say" would eventually
    disagree.
    """
    if not believable(text):
        return ""
    return caption_text.repair(
        text,
        vocabulary=settings.cyclic_messages_text_vocabulary,
        number_after=settings.cyclic_messages_text_number_after,
    )


def believable(text: str) -> bool:
    """Whether a reading is long enough to be one of the strip's captions.

    The strip leaves bands unused -- the top one for a whole pass between
    spins -- and a recogniser handed an empty crop answers with a character it
    thinks it found in the artwork rather than with nothing. This is the cut
    that throws those away, and it counts characters rather than trusting the
    confidence because the confidence cannot tell them apart: see
    ``CYCLIC_MESSAGES_TEXT_MIN_CHARACTERS`` for the measurement, where a stray
    "50" outscored the reliability floor.
    """
    characters = sum(1 for character in text if character.isalnum())
    return characters >= settings.CYCLIC_MESSAGES_TEXT_MIN_CHARACTERS


def reliable(confidence: float) -> bool:
    """Whether a reading cleared the confidence floor. One floor for both
    readers, on the 0-100 scale Paddle's own 0-1 is put onto at the point of
    reading."""
    return confidence >= settings.CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE


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
        return repair(self.text)

    @property
    def reliable(self) -> bool:
        return reliable(self.confidence)


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
    """One frame cut down to just its caption, before anything reads it.

    ``images`` is one crop per line the strip draws, top first -- the strip is
    stacked and each line has to be recognised on its own. Which lines those
    are depends on which strip the clip is of: see :func:`clip_regions`.
    """

    index: int
    at_seconds: float
    images: tuple[Image.Image, ...]


def _captions(
    path: Path, regions: tuple[image_roi.Roi, ...], interval: float
) -> list[_Caption]:
    """Decode the clip and keep only the caption bands out of each sampled frame.

    Cropping here rather than after is what makes the reading affordable: a
    1080x1920 frame is 6MB and a caption is a few kilobytes, so holding a 90s
    clip's worth of the first is over half a gigabyte and holding the same
    number of the second is nothing.

    The content box is found per frame rather than once for the clip, and once
    for all of that frame's bands. It costs little beside the decode, and a
    clip whose window changed shape part-way through would otherwise crop the
    wrong place for the rest of its length.
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

    Reported rather than raised because one bad frame is not a reason to lose
    the clip -- which is only safe because the engine was built and checked
    before the loop started. Without that pre-flight this is how a broken
    install would come back: every frame blank, indistinguishable from a strip
    that showed nothing.
    """
    logger.warning("Frame %d of the clip could not be read: %s", caption.index, exc)
    return _Read(caption.index, caption.at_seconds, "", 0.0)


def clip_regions(kind: str) -> tuple[str, ...]:
    """Which of the strip's lines a clip of ``kind`` draws.

    A run holds three kinds of clip and they are recordings of different
    things: a win presentation uses one line, and the between-spins strip two.
    Reading an idle clip against the win presentation's single region is how
    "Read the messages" came back with nothing at all from a losing spin --
    the band it was cropping is the one that strip leaves empty.

    ``win-then-idle`` is one file holding both, which happens when the win is
    taken before its line messages have been round once: the strip runs
    straight on and so does the recording. It is read for *both* bands, since
    the half that draws two is in there -- the win half simply leaves the
    second one empty, which reads as nothing and is dropped, exactly as an
    empty band is anywhere else.

    Public, and keyed on the kind rather than on the clip, because two callers
    ask it now: this module's own "Read the messages" and
    ``cyclic_messages._recover``, which reads a clip back frame by frame while
    the run is still going. Both have to crop the same bands out of the same
    file, or a caption recovered live and the same caption read afterwards
    would disagree about which band it came from.
    """
    if kind in (cyclic_service.IDLE_VIDEO, cyclic_service.WIN_THEN_IDLE):
        return settings.cyclic_messages_idle_regions
    return settings.cyclic_messages_text_regions


def _joined(caption: _Caption, lines: list[tuple[str, float]]) -> _Read:
    """One frame's bands as a single reading.

    The bands are joined rather than kept apart because everything downstream
    of here -- :func:`_group`, :func:`_distinct`, the run view's list -- asks
    "what did the strip say at this moment", and on a stacked strip the answer
    is all of its lines together. Two captions differing only on the second
    line are two different things the strip showed, and a reading that kept
    only the first would merge them.

    **Bands that read nothing are dropped, not joined as blanks**, and their
    confidence with them: the strip does not use every line at every moment --
    between spins FortuneOx leaves the top one empty for a whole pass -- so an
    unused band is not a bad reading, and letting its 0 through
    :func:`_worst` would mark every frame of that pass unreliable.
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
    """The *lowest* per-word confidence, not the mean: one mangled word is what
    makes a caption wrong, and an average hides it behind the words that read
    well."""
    return min(confidences, default=0.0)


def _read_tesseract(
    caption: _Caption, *, executable: Path, options: ocr.OcrOptions
) -> _Read:
    """Read every band of one caption with Tesseract."""
    lines: list[tuple[str, float]] = []
    for image in caption.images:
        try:
            result = ocr.read_image(image, executable=executable, options=options)
        except ocr.OcrError as exc:
            return _blank(caption, exc)
        lines.append(
            (
                result.text.strip(),
                _worst(tuple(word.confidence for word in result.words)),
            )
        )
    return _joined(caption, lines)


def _read_paddle(caption: _Caption, *, options: paddle_ocr.PaddleLineOptions) -> _Read:
    """Read one caption with PaddleOCR, recognise-only.

    ``read_line`` rather than ``read_image``: the crop already *is* the line,
    so running the detector over it is asking Paddle to rediscover the
    rectangle it was handed. That is also the expensive half -- see
    ``PaddleLineOptions.model_name`` for what the two cost.

    Taking the worst "word" is the same rule as the Tesseract path and means
    the same thing, though a recognise-only read returns just the one.
    """
    lines: list[tuple[str, float]] = []
    for image in caption.images:
        try:
            result = paddle_ocr.read_line(image, options=options)
        except ocr.OcrError as exc:
            return _blank(caption, exc)
        # Scaled onto Tesseract's 0-100 at the point of reading, so the floor,
        # the `reliable` flag and the payload all mean one thing whichever
        # engine answered -- the same conversion `ocr._read_tile_paddle` makes
        # for an orb.
        lines.append(
            (
                result.text.strip(),
                _worst(tuple(word.confidence * 100.0 for word in result.words)),
            )
        )
    return _joined(caption, lines)


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


def _plan(captions: list[_Caption]) -> list[int | None]:
    """For each sampled frame, whose reading it gets: its own, an earlier
    frame's, or none at all.

    **This is what makes reading a clip finish.** A clip is sampled every
    second and a caption stays up for several, so most of the frames of one are
    the frame before it again -- and at about a second and a half per
    recognition, reading all of them is where "Read the messages" went from
    slow to not returning: a 90s between-spins clip is ~180 recognitions (two
    bands a frame) against the client's 290s, and it lost that race. Here the
    same clip is one recognition per caption the strip actually showed.

    Three answers, one per frame, in order:

    * ``None`` -- **no band is showing.** The strip goes blank between
      messages, and a recogniser handed an empty crop does not say "nothing",
      it answers with a character it thinks it found in the artwork. That
      reading was always thrown away by the character floor
      (:func:`believable`), so the recognition was pure cost. Taken as blank
      without asking, which is also what keeps :func:`_group` splitting two
      showings of one caption either side of a gap.
    * its own index -- **the caption changed**, so this frame is read.
    * an earlier index -- **the picture repeated.** Judged by
      ``_SIGNATURE_REPEATED``, the strict cut, which is 0.000 for a frame the
      encoder repeated outright against the 0.01-0.03 between two consecutive
      line messages differing by one glyph -- see :meth:`CaptionChanges.changed`
      for why anything looser would merge two messages into one. So sharing a
      reading here is not an approximation: the two frames are the same
      picture, and reading the second could only have produced the same answer
      or a worse one.

    Every frame still appears on ``frames`` with its own offset either way --
    this decides what is *recognised*, never what is reported. What was
    recognised is ``frames_read`` beside ``frames_sampled``.
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

    The engine still sees a plain list, so :func:`_read_all` keeps its thread
    pool and Tesseract keeps its parallelism -- the saving is in the length of
    that list, not in how it is run.
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


def caption_key(text: str) -> str:
    """What two readings have to share to be one message.

    Public because the live capture groups by it too: a frame every 1.0s
    against a message that dwells 1.3-1.8s catches most captions twice, and
    those repeats are collapsed on the way to the screen. The rule for "the
    same caption" has to be one rule -- a clip read back and the stills taken
    of the same pass disagreeing about how many messages there were would be
    worse than either being wrong alone.

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
    grouping is by :func:`caption_key` the frames no longer agree character for
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
    """Count the repeats instead of listing them.

    The strip loops, so one pass of a 40-line win says "Line 1 Pays 250" as
    many times as the clip runs round. Grouping by :func:`caption_key` again -- not by
    the displayed text -- so a caption that read two ways still counts once,
    and the variant shown is the one the engine was surest of anywhere.

    Ordered by first appearance rather than by count: the strip has an order
    and it is the order a reader is checking against.
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
    run_id: str, cycle: int | None, interval: float, engine: _Engine
) -> CyclicTextReading:
    """Blocking half of :func:`read_run`: decode, crop, OCR, group."""
    detail = cyclic_service.get_run(run_id)
    clip, file_name = _clip_of(detail, cycle)
    path = cyclic_service.file_path(run_id, file_name)

    config = _config_for(detail.game)
    # The lines *this* clip's strip draws, not one fixed band: see
    # :func:`clip_regions`.
    names = clip_regions(clip.kind)
    if not names:
        raise GameConfigInvalidError(
            "No caption region is configured for this clip, so there is "
            "nothing to read it out of"
        )
    regions = tuple(_region_of(config, name) for name in names)
    region_name = ", ".join(names)
    # Tesseract's per-region options are resolved against the first band; the
    # bands of one strip are the same kind of crop drawn in the same face, so
    # there is nothing per-band to resolve between them.
    reader = engine.reader_for(config, names[0])

    info = video_frames.probe(path)
    captions = _captions(path, regions, interval)
    # Which frames are worth a recognition at all. See :func:`_plan`: most of a
    # clip's frames are the frame before them again, and reading every one is
    # what stopped this request finishing on a long clip.
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


# --- reading a frame while the run is still going -------------------------
#
# The clip reader above answers "what did the strip say" once the run is over,
# off a video. This answers the same question frame by frame while the run is
# still going, off the PNGs the capture loop is writing -- so a tester watching
# a pass sees each caption named beside its own screenshot rather than waiting
# for the clip and asking for it.
#
# Three things it does *not* do differently, deliberately. The region is the
# same ``CYCLIC_MESSAGES_TEXT_REGION``; the repair is the same
# :func:`repair`; and the confidence floor is the same :func:`reliable`. A
# caption read live and the same caption read off the clip should differ
# because the picture differed -- a still against an encoded frame -- and never
# because two readers disagreed about what the strip is allowed to say.
#
# Paddle only, and that is the one real difference. The clip reader keeps
# Tesseract selectable for a host that cannot install Paddle; a reader that has
# to keep pace with the capture loop is not the place to start a subprocess per
# frame, and a live reading that silently came from the other engine at a floor
# measured for neither is worse than no live reading at all.


CAPTION_CROP_SUFFIX = "_caption.png"
"""Appended to a frame's stem for the crop written beside it."""


@dataclass(frozen=True)
class LiveRead:
    """One *line* of a captured frame's strip, read from the file OBS wrote.

    One of these per configured region, not per frame: the strip is stacked,
    and each line is cropped and recognised on its own. See
    ``CYCLIC_MESSAGES_TEXT_REGIONS``.
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
    model* before handing one back -- the same pre-flight :func:`_resolve_engine`
    does and for the same reason. Inside the worker loop a failure is caught per
    frame and recorded on that frame, so a reader that was never going to work
    would otherwise come back as a pass of blank captions rather than as one
    error saying PaddleOCR is not installed.

    Frozen and holding no per-frame state, so the one instance a run makes is
    reused for every frame of every pass in it.
    """

    game: str
    regions: dict[str, image_roi.Roi]
    """Every line either window may ask for, by name.

    Resolved once for the run and then *selected from* per frame, because the
    two windows show different strips: a win presentation draws one line, and
    the between-spins strip draws two. Which of these a frame wants is decided
    by the window it was captured in and passed to :meth:`read` -- see
    ``CYCLIC_MESSAGES_TEXT_REGIONS`` and ``CYCLIC_MESSAGES_IDLE_REGIONS``.
    """

    model: str
    """The Paddle recognition model that will read, as it reported itself."""

    options: paddle_ocr.PaddleLineOptions

    def read(self, path: Path, names: tuple[str, ...]) -> list[LiveRead]:
        """Crop every line of the strip out of one written frame and read each.

        Blocking: opening the frame and running a recogniser over each line is
        squarely thread work, and the caller hands it to one. Raises rather
        than returning blanks on failure -- unlike the clip reader's
        :func:`_blank`, because a live frame's error belongs on that frame
        where it can be seen, not averaged into a run of empty captions.

        ``names`` is which lines to read, in draw order -- the caller knows
        which window the frame came from and this does not. Reading a band the
        strip was not using costs a recogniser pass to be told there is nothing
        there, which is why the win presentation asks for one line and the
        between-spins strip for two.

        The frame is opened and its content box found *once* for all the lines
        rather than per line: they are rectangles on one picture, and letterbox
        detection scans the whole of it.
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
        """The same read, over a frame already in hand.

        The door for a frame that never was a file of its own -- one decoded
        out of a clip, recovering the messages the live stills were too slow to
        catch. ``beside`` is the picture this frame stands for on disk, which
        is only where its caption crops are written.
        """
        started = time.perf_counter()
        # Per frame rather than once for the clip, exactly as :func:`_captions`
        # does it: the cost is nothing beside the decode, and a window that
        # changed shape part-way through would otherwise crop the wrong place
        # for the rest of it.
        content = roi_service.content_box(image)

        reads: list[LiveRead] = []
        for name in names:
            roi = self.regions.get(name)
            if roi is None:
                continue
            crop = image.crop(roi.to_box_within(content.box))
            crop_name = _write_crop(crop, beside, name)
            if not crop_signature((crop,))[1]:
                # Nothing drawn on this band, so nothing to recognise. The
                # between-spins strip leaves band 1 empty for its whole window,
                # and reading it anyway was half of every such window's reading
                # time -- ~3s a band under load, for an answer the character
                # floor then threw away. The same contrast test the clip reader
                # skips blank frames by (`_plan`); measured over 256 real band
                # crops, every caption scored 25.3 or more against 10.4 at most
                # for an empty band, either side of `_SIGNATURE_INK`.
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
    """Leave one line's crop beside the frame it came out of.

    Evidence, not output: a reading that says "Lne 1 Paye 25" is either a
    misread or a badly aimed rectangle, and the two look identical until
    somebody sees the pixels the recogniser was given. Named off the frame's
    own stem *and* the region, so the strip's lines sort together beneath their
    frame instead of overwriting one another.

    Never raises -- losing the crop costs the diagnosis and not the reading.
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
# The between-spins strip loops the same few messages until somebody spins, so
# capturing it to a deadline means capturing the same three captions over and
# over. Stopping once it has been round once is what keeps a run to one of
# each -- but the capture loop cannot ask what a caption *says* to know that,
# because reading is 1.5s a frame and deliberately does not run until the
# window has closed.
#
# So the loop is spotted by the picture instead. Two crops of one caption are
# the same pixels; two different captions are not, and by a wide margin. The
# signature below is the caption bands reduced to a small normalised grid, and
# measured on real frames of a FortuneOx run the two populations do not come
# close to meeting:
#
#   the same caption, 0.4s and 2.0s apart      0.000, 0.000
#   different captions of the same strip       0.135 - 0.593
#
# Those "different" figures are two *kinds* of message apart, though, and the
# strip's tightest pair is far closer than they suggest -- see
# ``_SIGNATURE_SAME``, which is measured on consecutive line messages and is
# two orders of magnitude below what this population implies.
#
# Normalising each grid to zero mean and unit deviation is what makes that gap
# hold: it takes brightness and contrast out, so the strip fading between
# messages does not read as a new one.
_SIGNATURE_GRID = (64, 16)
_SIGNATURE_REPEATED = 0.005
"""Below which two frames are the same picture and reading both is wasted.

Measured over a real 29-frame win clip: frames the encoder repeated outright
score **0.000**, and the smallest genuine change -- one glyph, "Line 1 Pays 15"
becoming "Line 2 Pays 15" -- scores **0.01**. The cut sits between, nearer the
bottom, because the two errors are not equal: reading a frame twice costs a
second and a half, and skipping one costs a message nobody will know was
missed.
"""

_SIGNATURE_INK = 20.0
"""Least contrast a band needs before it counts as showing a caption.

The strip goes briefly blank changing from one message to the next, and a
frame caught in that gap is not a caption -- it is the space between two. Told
apart by how much the band varies, measured on real frames: 32-38 with a
message on it, 10.0 caught mid-change, and 6.4 for a band this strip never
uses at all. The cut sits between, with a wide margin either side.
"""
_SIGNATURE_SAME = 0.003
"""Mean absolute difference below which two bands show the same caption.

**Per band, never across bands** -- see :func:`crop_signature`.

Re-measured over every band-1 pair of a real 68-frame run (2278 of them,
captions taken from what the recogniser made of each frame), because the
figures this was first set from came from a strip whose consecutive messages
differ wholesale and the line messages do not:

    the same caption, 1-40s apart      0.0000 - 0.0010   (n=33)
    different captions                 0.0046 - 0.2684   (n=2245)

The populations separate, but far lower down than the first cut assumed: 0.05
called **1350 of those 2245 different pairs the same caption** -- "Line 36 Pays
25" against "Line 38 Pays 25" scores 0.0046 -- which is what let
:class:`LoopWatch` declare a lap closed on a strip that had two messages left
to show. This sits in the measured gap, three times the largest "same" reading
and two thirds of the smallest "different" one.

Erring low is the safe direction and deliberate: too strict and a lap never
closes, so the window runs to its deadline and captures more than it needed.
Too slack and it closes early, which loses a message nobody will know was
missed.
"""


def strip_rois(
    game: str, names: tuple[str, ...]
) -> tuple[tuple[str, image_roi.Roi], ...]:
    """The named regions of one game, resolved without starting an OCR engine.

    :func:`live_reader` needs PaddleOCR before it can hand anything back; this
    is the same resolution for a caller that only wants to *look* at the
    caption bands -- :class:`LoopWatch` does, and it runs inside the capture
    loop where an engine has no business being.
    """
    config = _config_for(game)
    return tuple((name, _region_of(config, name)) for name in names)


@dataclass
class LoopWatch:
    """Notices when the strip has shown everything it has and come round again.

    Fed one written frame at a time by the capture loop, in order.
    :meth:`saw` answers "has the strip now been round once", which is when
    there is nothing further to capture: everything after it is a repeat.

    It counts *distinct consecutive* captions, so the several frames that catch
    one message are one caption rather than several, and joining the strip
    part-way through its cycle still measures a whole lap -- the lap closes
    when it returns to whichever caption it happened to start on, wherever in
    the loop that was.
    """

    regions: tuple[tuple[str, image_roi.Roi], ...]
    """The bands whose cycle decides the lap -- *not* every band the strip has.

    See ``CYCLIC_MESSAGES_IDLE_LOOP_REGIONS``: the bands cycle independently
    and at very different lengths, so "the strip has come round" is a question
    about one of them and watching both asks it of neither.
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

        Blocking, and cheap on purpose: opening the frame and reducing two thin
        bands to a 64x16 grid is milliseconds against the 1.5s an OCR pass
        costs, which is the whole reason the loop can be spotted here and the
        reading still left until afterwards.

        Never raises. A frame that cannot be read is not evidence the strip has
        come round, so it is passed over and the window runs on to its deadline
        -- capturing a few frames too many is a far smaller failure than
        stopping a pass before it has shown anything.
        """
        try:
            signature, showing = self._signature(path)
        except Exception as exc:  # noqa: BLE001 - never worth ending a pass over
            logger.warning("Could not read %s to watch for the loop: %s", path, exc)
            return False

        if not showing:
            # The gap between two messages, not a message. Counting it as one
            # both invents a caption that was never shown and -- because the
            # next real frame then looks like a *return* -- can close the lap
            # before the strip has been round. Passed over entirely: it does
            # not become `current`, so the caption either side of it is still
            # one showing.
            self.blank += 1
            return False

        if self.current is not None and signature.alike(self.current):
            return False
        self.current = signature

        if self.seen and signature.alike(self.seen[0]):
            # Back to the caption it started on, which is a whole lap however
            # far into the strip's own cycle the window happened to open. Only
            # once a second caption has been seen, since a strip sitting on one
            # message has not looped, it has simply not changed.
            #
            # Deliberately the *first* caption and not any already seen. The
            # strip can repeat a message part-way round, and a frame missed
            # while OBS was slow leaves the sequence looking like a return to
            # the wrong one -- both of which closed a pass early enough to lose
            # a message that had not been shown yet.
            return len(self.seen) >= 2
        if not any(signature.alike(earlier) for earlier in self.seen):
            self.seen.append(signature)
        return False

    def _signature(self, path: Path) -> tuple[Signature, bool]:
        return band_signature(roi_service.open_frame(path), self.regions)


def band_signature(
    image: Image.Image, regions: tuple[tuple[str, image_roi.Roi], ...]
) -> tuple[Signature, bool]:
    """One frame's caption bands as a signature, and whether any is showing.

    The cheap stand-in for reading a frame: two crops of one caption reduce to
    the same grid and two different captions do not, at a fraction of what an
    OCR pass costs. Used both to notice the strip coming round
    (:class:`LoopWatch`) and to pick out the frames worth reading at all
    (:class:`CaptionChanges`).
    """
    content = roi_service.content_box(image)
    return crop_signature(
        tuple(image.crop(roi.to_box_within(content.box)) for _, roi in regions)
    )


def crop_signature(crops: tuple[Image.Image, ...]) -> tuple[Signature, bool]:
    """The same signature, off caption crops that have already been made.

    Split out rather than duplicated because the clip reader cuts its crops
    once, before anything looks at them (:func:`_captions`), and re-cropping a
    1080x1920 frame per comparison is most of what a comparison would cost.
    Whichever door a caller comes in by, two crops of one caption have to
    reduce to the same grid or the strip's own repeats stop being recognisable
    as repeats.

    The bands are kept **apart** rather than concatenated into one grid, and
    that is the whole of :class:`Signature`'s reason for existing -- see its
    own note.
    """
    grids = []
    showing = False
    for crop in crops:
        grid = numpy.asarray(
            crop.convert("L").resize(_SIGNATURE_GRID), dtype=numpy.float32
        )
        # Read before normalising, which is what removes the very contrast this
        # depends on: a blank band is flat, and dividing it by its own
        # deviation turns it into noise indistinguishable from text.
        showing = showing or bool(grid.std() >= _SIGNATURE_INK)
        grids.append((grid - grid.mean()) / (grid.std() + 1e-6))
    return Signature(bands=tuple(grids)), showing


@dataclass(frozen=True)
class Signature:
    """One frame's caption bands, each reduced to a grid and kept separate.

    **Separate is the point, and joining them is a measured bug.** The strip's
    bands cycle independently, so most frames change one band and leave the
    other exactly as it was. Concatenating the two grids and taking one mean
    across the pair averages the band that changed against a zero from the band
    that did not, which halves every real difference: on a real run "Line 35
    Pays 25" becoming "Line 37 Pays 25" scores 0.0298 on its own band and
    **0.0149** once the unchanged band is averaged in, and the second figure
    fell under :data:`_SIGNATURE_SAME` where the first did not. That is how a
    between-spins window came to close while the strip still had "GAME PAYS
    1000" to show.

    Diluting also throws away the good band: band 2's captions separate at
    0.326-0.914 alone and at 0.168 once band 1's near-zeros are mixed in.

    So a comparison is per band, and the two questions callers ask of it are
    different quantifiers over the same pairs -- :meth:`alike` is *every* band
    (the strip has come round only if nothing on it has changed) and
    :meth:`changed` is *any* band (a frame is worth reading if anything on it
    has).
    """

    bands: tuple[Any, ...]

    def _scores(self, other: Signature) -> tuple[float, ...]:
        """Each band's mean absolute difference from the same band of
        ``other``. Raises on a mismatched band count rather than comparing what
        happens to line up: two frames of one run always have the same bands,
        so a mismatch is a bug and not a frame to skip."""
        if len(self.bands) != len(other.bands):
            raise ValueError(
                f"cannot compare {len(self.bands)} bands against {len(other.bands)}"
            )
        return tuple(
            float(numpy.abs(mine - theirs).mean())
            for mine, theirs in zip(self.bands, other.bands, strict=True)
        )

    def alike(self, other: Signature) -> bool:
        """Whether this is the same caption as ``other`` -- *every* band of it.

        One band having changed means the strip is showing something it was
        not, however still the rest of it is.
        """
        return all(score < _SIGNATURE_SAME for score in self._scores(other))

    def changed(self, other: Signature) -> bool:
        """Whether this frame shows something ``other`` did not -- *any* band.

        The strict cousin, asking "is this the same picture" per band rather
        than "the same caption": see :data:`_SIGNATURE_REPEATED`.
        """
        return any(score >= _SIGNATURE_REPEATED for score in self._scores(other))


@dataclass
class CaptionChanges:
    """Picks the frames of a clip where the caption actually changed.

    Reading every frame of a clip is mostly reading the same caption again: a
    message stays up for several seconds and the clip is sampled every one, so
    a 29-frame win presentation holding five messages is five answers and
    twenty-four repetitions of them. At around a second and a half an OCR pass
    that is the difference between reading a clip in ten seconds and in eighty
    -- and eighty is long enough to still be going when the next spin needs the
    run's attention.

    So each frame is compared against the last by picture first, and only a
    frame whose caption is *new* is read. Frames caught in the gap between two
    messages are skipped for the same reason they are in :class:`LoopWatch`:
    they are not captions, and reading them yields the noise the character cut
    would throw away anyway.
    """

    regions: tuple[tuple[str, image_roi.Roi], ...]
    current: Signature | None = None

    def changed(self, image: Image.Image) -> bool:
        """Whether this frame shows a caption the one before it did not.

        Judged far more strictly than :class:`LoopWatch` judges two captions
        alike, and the difference is the whole safety of this. That one asks
        "is this the caption I saw earlier", across seconds and a re-render, so
        it can afford slack. This asks "is this frame the one before it", where
        slack costs a *message*: the strip's consecutive captions differ by a
        single glyph -- "Line 1 Pays 15" against "Line 2 Pays 15" -- which
        measures only 0.01-0.03 against the 0.000 of a frame the encoder
        repeated outright. Anything loose enough to be comfortable would merge
        two line messages into one, which is a lost message and the failure
        this feature exists to prevent.
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
    """One frame decoded out of a clip, with the whole picture kept.

    Unlike :class:`_Caption`, which throws the frame away and keeps only its
    caption bands, this holds the picture too -- the caller writes it out as
    the frame's own screenshot, so a message recovered from the clip is shown
    the same way a message caught live is.
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
    is off, and the same 500 when the game declares no such region. Both are
    for the *caller* to decide about: a cyclic run whose captions cannot be
    read is still a run worth having, so ``services/cyclic_messages`` catches
    these and notes them rather than failing the run.
    """
    config = _config_for(game)
    # Both windows' lines, resolved together: the reader outlives any one
    # window and a region that only the between-spins strip uses should still
    # fail loudly here, when the run starts, rather than on the first frame
    # after somebody's first losing spin.
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
        # Shared with the clip reader rather than given its own setting: 2x is
        # a property of how thin the caption band is, which is the same band
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
