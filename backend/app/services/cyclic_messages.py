"""Cyclic Messages: follow the game's log for the rotating message strip,
screenshot every message it shows, and record a video of the whole run.

Sibling of :mod:`app.services.event_capture`, and structured the same way --
module-level singleton, a watcher task over a :class:`LogFollower`, one
directory per run holding a ``run.json`` beside its images. Four things differ,
all of them because a cyclic message is not a gameplay event:

* **The rule set is only** :data:`game_log.CYCLIC_MESSAGE_RULES`. Event capture
  resolves the shipped rules plus a game's own; this feature deliberately does
  not, because "only cyclic messages" is the whole point of it.
* **The sequence boundaries are recorded without a frame.** ``capture=False``
  means "no screenshot" here, not "ignore" as it does in event capture -- a run
  reads as sequences, and a marker with no picture is what makes it do so.
* **A run records video per win, not per run.** OBS is asked to record when
  ``cyclic-game-pays`` is read and to stop when the line messages report
  finishing, and each file is moved beside the screenshots so a run is still
  one directory. See below; the boundaries are the point.
* **Part of a run is sampled, not logged.** See below; this is the one thing
  about the feature that cannot be inferred from its siblings.

The strip's *text* is never logged by the game -- not by ``AttractStateMachine``,
not by the results cycle, not by ``LocalizationManager`` -- so these events say
only that the strip changed. What it changed to is in the screenshot, which is
why a run that takes no screenshots is worth failing loudly about and a run that
takes no video is not.

**The win strip is bracketed, not followed, and that is the load-bearing
compromise.** The idle strip logs a line per message, so it gets an event per
message. The win strip does not: ``WinBangDone`` says the line messages have
begun and ``FirstCycleResultsIterationFinishedMsg`` says one full pass through
them is done, and between those two the client log is *completely silent* --
measured at 6.5-7.3s across four one-line FortuneOx wins and at 78.8s across
three 40-line ones, not one line in between either way. **How long that window
is depends on how many lines paid**, at roughly 2s a line, which is what every
deadline here has to be sized against rather than against a typical win. So
individual "LINE 3 PAYS 20" frames are taken on a timer inside that window and
marked :attr:`CyclicEventSource.SAMPLED`. Two rules protect the distinction:

* A sampled event's ``log_line`` quotes the boundary that *opened* the window,
  never a line about the message in its own picture -- there is no such line.
* Only ``cyclic-game-pays`` carries an amount, because
  ``SpinBufferManager.OnGameStateResults`` is the single line in either log that
  states one. Its cents over the denomination is what the strip displays: 16800
  at ``denom[100.000]`` is the "GAME PAYS 168" a player sees.

Do not "improve" a sampled event into a logged one by inventing a rule for it.
A frame taken on a clock that claims a log line behind it is worse than one
that admits it was a guess, because the second can be checked and the first
cannot.

**A clip is one win presentation, and its boundaries are the same two lines the
sampler brackets one pass with -- widened by one at the front.** Recording
opens on ``cyclic-game-pays`` (before that event's own screenshot, so the GAME
PAYS banner the frame catches is inside the video) and closes on
``cyclic-line-pays-cycle-finished`` (after that event's screenshot, so the last
line message is). What that buys is a video of the thing the frames are
evidence of: the amount, then every line message that paid it. What it costs is
the idle strip, which is never recorded -- it is per-message logged, so every
one of its messages already has a frame of its own and a video of the gaps
between them shows nothing a screenshot missed.

Four other things end a clip, and :attr:`CyclicRunVideo.closed_by` says which:
the next spin cutting the pass short, a *second* win opening before the first
reported finishing (two presentations are two clips, never one long one),
tracking being stopped mid-presentation, and
``CYCLIC_MESSAGES_VIDEO_MAX_SECONDS`` for the closing line that never comes.
That last one is not paranoia -- an unbounded clip is a recording of the rest
of the session, which is exactly what recording per win exists to avoid. It is
also the one that has already been wrong once, at 60s against an 83s
presentation: a clip cut off by a deadline looks exactly like a complete one to
whoever watches it, so it is reported on the run's ``errors`` as well as on the
clip, and the value belongs far above the longest pass a game can legitimately
make rather than just above a typical one.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.config.runtime import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    AppException,
    CyclicMessagesAlreadyRunningError,
    CyclicMessagesLogUnavailableError,
    CyclicMessagesNotRunningError,
    CyclicMessagesRunNotFoundError,
    GameConfigInvalidError,
)
from app.schemas.cyclic_messages import (
    CyclicEvent,
    CyclicEventSource,
    CyclicRunDetail,
    CyclicRunState,
    CyclicRunSummary,
    CyclicRunVideo,
    CyclicStatus,
)
from app.schemas.obs import ScreenshotRequest
from app.services import obs as obs_service
from app.utils import game_log, log_search
from app.utils.log_tail import LogFollower
from app.utils.paths import UnsafeNameError, resolve_subdirectory, resolve_within

logger = get_logger("cyclic_messages")

MANIFEST_NAME = "run.json"

# Directory name per run, and the stamp inside each screenshot's filename.
_RUN_ID_FORMAT = "%Y-%m-%d_%H-%M-%S"
_FILE_TIME_FORMAT = "%H-%M-%S"

# The idle strip's rules, by the part each plays in a loop.
_MESSAGE_EVENT = "cyclic-message-shown"
_CYCLE_STARTED = "cyclic-cycle-started"
_CYCLE_COMPLETED = "cyclic-cycle-completed"

# The win strip's, in the order one presentation writes them. Only the first
# carries an amount; the pass between the second and third is the silent window
# that :func:`_sampler` fills.
_GAME_PAYS = "cyclic-game-pays"
_WIN_PRESENTED = "cyclic-win-presented"
_LINE_PAYS_CYCLE_DONE = "cyclic-line-pays-cycle-finished"
_RESULTS_CYCLE_STOPPED = "cyclic-results-cycle-stopped"

# Not a rule: the sampled frame, which by definition no log line produces.
_LINE_PAYS_SHOWN = "cyclic-line-pays-shown"

# Not rules either: the two ways a clip ends without a log line saying so.
_RUN_STOPPED = "run-stopped"
_TIME_LIMIT = "time-limit"

# OBS reports the recording's path the moment it stops, which on Windows is
# before the muxer has let go of the handle. Retried rather than failed: the
# file is right there and a moment later it moves.
_MOVE_ATTEMPTS = 5
_MOVE_RETRY_SECONDS = 0.3


@dataclass
class _Clip:
    """The recording in progress, and the win presentation it belongs to."""

    cycle: int
    started_at: datetime


@dataclass
class _ActiveRun:
    """Everything the watcher needs, and nothing anyone else does."""

    run_id: str
    game: str
    directory: Path
    output_dir: str
    """Run directory relative to the screenshot root, as OBS wants it."""

    log: LogFollower
    """Cursor over the game's log, positioned at its end when the run started."""

    started_at: datetime
    events: list[CyclicEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    last_seen: dict[str, datetime] = field(default_factory=dict)

    next_sequence: int = 0
    """Sequences handed out so far.

    Not ``len(events) + 1`` any more: the watcher and the sampler both record,
    both await a screenshot before appending, and two producers reading the
    list length would hand the same number to both.
    """

    denomination: float | None = None
    """Cents per credit, from the game's own ``UpdatePayTable`` line.

    Read backwards out of the log at start and refreshed whenever the game
    logs a new one, because it is what turns the only amount either log states
    (16800 cents) into the one the strip displays (168).
    """

    # --- loop bookkeeping -------------------------------------------------
    cycle: int = 0
    """Loops begun so far; also the number every event of the current one carries."""

    position: int = 0
    """Messages shown in the current loop."""

    cycle_count: int = 0
    """Loops that ran to completion."""

    pending_start: game_log.DetectedEvent | None = None
    """A ``cyclic-cycle-started`` held back until a message proves the loop real.

    The game arms and ends attract far more often than it displays anything --
    measured on FortuneOx's log, 115 sequence-ends against 30 starts that showed
    a message. Emitting the marker only once a message follows it is what keeps
    a run's loop count equal to the loops a person actually watched.
    """

    clip: _Clip | None = None
    """The win presentation being recorded, if one is; ``None`` while idle."""

    clip_guard: asyncio.Task[None] | None = None
    """Closes :attr:`clip` if its closing line never arrives.

    Its own task for the same reason the sampler is one: the watcher awaits each
    handler in turn, so a deadline waited out inside one would stop the run
    reading the very line it is waiting for.
    """

    closing: asyncio.Task[None] | None = None
    """The clip being stopped and filed right now, if one is.

    Its own task because the watcher is cancelled *where it stands* when a run
    stops: a close that unwound halfway would leave OBS still recording and the
    file where OBS put it, with nothing on the record saying so. So the filing
    is shielded from its caller's cancellation and :func:`_finish` waits for it.
    """

    videos: list[CyclicRunVideo] = field(default_factory=list)
    """One entry per win presentation, failures included -- a clip OBS refused
    is a fact about that win, not an absence."""

    task: asyncio.Task[None] | None = None

    sampler: asyncio.Task[None] | None = None
    """Fills the silent line-message window with frames; see the module docstring.

    Its own task rather than a loop inside :func:`_handle`, because the watcher
    awaits each handler in turn -- sleeping through a pass there would stop the
    run reading the very line that ends it.
    """

    sampled_count: int = 0

    finishing: bool = False
    """Set once the run is being sealed, to refuse a *new* sampling pass or clip.

    The final catch-up read replays lines that are already history, and a
    ``WinBangDone`` among them would otherwise open a pass that outlives the
    run -- which under ``filterwarnings = error`` is a test failure, not just
    an oddity.
    """


_run: _ActiveRun | None = None
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Serialise start and stop. Built lazily, like the OBS service's, since a
    lock made at import time would bind to the wrong loop in tests."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


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


def _log_for(name: str, config: GameConfig) -> Path:
    """The game's log, checked to actually be there -- refusing up front, since a
    run against a log that never appears looks identical to a quiet game."""
    if config.log_path is None:
        raise CyclicMessagesLogUnavailableError(
            f"The game config for {name!r} does not declare a 'log' path to follow"
        )
    if not config.log_path.is_file():
        raise CyclicMessagesLogUnavailableError(
            f"The log for {name!r} does not exist yet: {config.log_path}. "
            "Start the game and try again."
        )
    return config.log_path


# --- the manifest ---------------------------------------------------------


def _capture_root() -> Path:
    """Directory holding one subdirectory per run."""
    return resolve_subdirectory(
        settings.obs_screenshot_dir, settings.CYCLIC_MESSAGES_DIR_NAME
    )


def _message_count(run: _ActiveRun) -> int:
    """Messages seen, which is not the event count -- the sequence markers are
    events too. Counted by ``captured`` rather than by rule name, so a message
    added to either family counts without editing this."""
    return sum(1 for event in run.events if event.captured)


def _detail(
    run: _ActiveRun, *, status: CyclicRunState, stopped: datetime | None
) -> CyclicRunDetail:
    """Build the record that both the manifest and the API return."""
    return CyclicRunDetail(
        run_id=run.run_id,
        game=run.game,
        status=status,
        started_at=run.started_at,
        stopped_at=stopped,
        message_count=_message_count(run),
        sampled_count=run.sampled_count,
        cycle_count=run.cycle_count,
        log_path=str(run.log.path),
        videos=list(run.videos),
        # Sorted, not appended: the watcher and the sampler each await a
        # screenshot before appending, so the order they finish in is not the
        # order they were triggered in. The sequence is what a reader trusts.
        events=sorted(run.events, key=lambda event: event.sequence),
        errors=list(run.errors),
    )


def _write_manifest(run: _ActiveRun, detail: CyclicRunDetail) -> None:
    """Persist the run record atomically. Never raises -- losing one write is
    better than taking down a live run over it; the next event retries."""
    target = run.directory / MANIFEST_NAME
    temporary = target.with_name(f".{MANIFEST_NAME}.tmp")
    try:
        temporary.write_text(
            detail.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8"
        )
        temporary.replace(target)
    except OSError:
        logger.exception("Could not write the cyclic message manifest at %s", target)
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _read_manifest(path: Path) -> CyclicRunDetail | None:
    """Read one ``run.json``, or ``None`` if it is unusable -- drops out of the
    listing rather than breaking it."""
    try:
        document: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Skipping unreadable cyclic message manifest at %s", path)
        return None
    try:
        return CyclicRunDetail.model_validate(document)
    except ValueError:
        logger.warning("Skipping malformed cyclic message manifest at %s", path)
        return None


def _note(run: _ActiveRun, message: str) -> None:
    """Record a problem that did not stop the run, once."""
    if message not in run.errors:
        run.errors.append(message)


# --- video: one clip per win presentation ---------------------------------


def _recording_dir(run_id: str) -> str:
    """Where OBS should write the run's clips, relative to the recording root."""
    return f"{settings.CYCLIC_MESSAGES_DIR_NAME}/{run_id}"


def _clip_name(clip: _Clip, suffix: str) -> str:
    """Filename that says which sequence the clip covers, e.g.
    ``cycle-003_win-video_16-36-21.mp4`` -- so it sits beside that sequence's
    own frames in a directory listing rather than under an OBS timestamp."""
    stamp = clip.started_at.strftime(_FILE_TIME_FORMAT)
    return f"cycle-{clip.cycle:03d}_win-video_{stamp}{suffix}"


async def _open_clip(run: _ActiveRun) -> None:
    """Begin recording the win presentation that just opened.

    Never raises: a win whose recording failed still screenshots every message,
    and the reason travels on the run's own list of clips.
    """
    if not settings.CYCLIC_MESSAGES_RECORD_VIDEO or run.finishing:
        return

    # A win opening before the previous one reported finishing gets its own
    # clip; two presentations in one file could not be told apart afterwards.
    await _close_clip(run, closed_by=_GAME_PAYS)

    clip = _Clip(cycle=max(1, run.cycle), started_at=datetime.now())
    try:
        await obs_service.start_recording(_recording_dir(run.run_id))
    except AppException as exc:
        run.videos.append(
            CyclicRunVideo(
                cycle=clip.cycle, started_at=clip.started_at, error=exc.message
            )
        )
        _note(run, f"video: {exc.message}")
        logger.warning(
            "Could not record the win presentation for %s: %s", run.run_id, exc.message
        )
        return

    run.clip = clip
    run.clip_guard = asyncio.create_task(
        _guard_clip(run), name=f"cyclic-messages-clip-{run.run_id}"
    )
    logger.info(
        "Cyclic message run %s is recording sequence %d", run.run_id, clip.cycle
    )


async def _file_clip(
    run: _ActiveRun, clip: _Clip, source: str | None
) -> tuple[str | None, str | None]:
    """Move a finished recording beside the run's screenshots, under a name that
    says which sequence it is. Returns ``(file name, error)``.

    OBS writes under its own recording root, which is the same directory as the
    screenshot root by default but need not be -- so the file is moved rather
    than assumed to have landed in the right place, and a move that keeps
    failing leaves the video where OBS put it rather than losing it.
    """
    if not source:
        return None, "OBS did not report where it wrote the recording"

    origin = Path(source)
    if not origin.is_file():
        return None, f"OBS reported a recording at {source}, but there is no file there"

    target = run.directory / _clip_name(clip, origin.suffix)
    if origin == target:
        return origin.name, None

    attempt = 0
    while True:
        attempt += 1
        try:
            run.directory.mkdir(parents=True, exist_ok=True)
            shutil.move(str(origin), str(target))
        except OSError as exc:
            if attempt >= _MOVE_ATTEMPTS:
                logger.warning(
                    "Could not move %s beside its screenshots: %s", origin, exc
                )
                return None, (
                    f"The video was recorded to {source} but could not be moved "
                    f"beside the screenshots: {exc}"
                )
            await asyncio.sleep(_MOVE_RETRY_SECONDS)
        else:
            return target.name, None


def _is_recording(run: _ActiveRun) -> bool:
    """Whether a clip is open *or* still being filed.

    Both count as recording on purpose: it makes "not recording" mean the clip
    is on the record, which is what a reader waiting for one needs.
    """
    if run.clip is not None:
        return True
    return run.closing is not None and not run.closing.done()


async def _close_clip(run: _ActiveRun, *, closed_by: str) -> None:
    """End the clip in progress and file it with the screenshots. Never raises,
    and does nothing when no clip is open -- every caller may be the one that
    finds the presentation already over."""
    clip = run.clip
    if clip is None:
        return
    # Both of these happen before the first await, and that is load-bearing.
    # Claiming the clip is what stops the watcher and the guard both closing it.
    # Handing the work to a task in the same breath is what stops a caller
    # cancelled *anywhere* below here -- Stop tracking cancels the watcher where
    # it stands -- from leaving OBS recording with nothing on the record. An
    # await between the two leaves exactly that window open.
    run.clip = None
    run.closing = asyncio.create_task(
        _file_and_record(run, clip, closed_by),
        name=f"cyclic-messages-closing-{run.run_id}",
    )
    # Shielded rather than awaited plainly: see _ActiveRun.closing. The caller
    # still sees the cancellation; what it does not do is take the filing with it.
    await asyncio.shield(run.closing)
    run.closing = None


async def _await_closing(run: _ActiveRun) -> None:
    """Wait for a filing that outlived the task which started it."""
    task, run.closing = run.closing, None
    if task is not None and not task.done():
        await asyncio.gather(task, return_exceptions=True)


async def _file_and_record(run: _ActiveRun, clip: _Clip, closed_by: str) -> None:
    """Stop OBS, move the file beside the screenshots, put it on the record.
    Never raises: a clip is a record, never a reading.

    Everything that awaits lives here rather than in :func:`_close_clip`,
    including retiring the deadline -- this task is the one nobody cancels.
    """
    await _stop_guard(run)

    stopped_at = datetime.now()
    try:
        result = await obs_service.stop_recording()
    except AppException as exc:
        video = CyclicRunVideo(
            cycle=clip.cycle,
            started_at=clip.started_at,
            stopped_at=stopped_at,
            closed_by=closed_by,
            error=exc.message,
        )
    else:
        file_name, error = await _file_clip(run, clip, result.output_path)
        video = CyclicRunVideo(
            file_name=file_name,
            source_path=result.output_path,
            cycle=clip.cycle,
            started_at=clip.started_at,
            stopped_at=stopped_at,
            closed_by=closed_by,
            error=error,
        )

    run.videos.append(video)
    if video.error:
        _note(run, f"video: {video.error}")
        logger.warning(
            "Cyclic message run %s could not save sequence %d: %s",
            run.run_id,
            clip.cycle,
            video.error,
        )
    else:
        logger.info(
            "Cyclic message run %s saved sequence %d as %s (%s)",
            run.run_id,
            clip.cycle,
            video.file_name,
            closed_by,
        )
    _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))


async def _guard_clip(run: _ActiveRun) -> None:
    """Close a clip whose closing line never arrives.

    The ordinary end of a presentation is a log line; this is only for the one
    that never comes. Without it the clip runs until the next win or until
    tracking stops, which is the whole-run recording this feature exists not to
    make.
    """
    await asyncio.sleep(max(0.1, settings.CYCLIC_MESSAGES_VIDEO_MAX_SECONDS))
    # Cleared before closing: _close_clip cancels the guard, and a task
    # cancelling itself would never come back from the cancellation.
    run.clip_guard = None
    # Said out loud on the run, not just on the clip: a truncated video looks
    # exactly like a complete one to whoever watches it, so the one place this
    # can be noticed is here.
    _note(
        run,
        f"video: the clip for sequence {run.clip.cycle if run.clip else 0} was "
        f"cut off after {settings.CYCLIC_MESSAGES_VIDEO_MAX_SECONDS:g}s because "
        "the line messages never reported finishing; raise "
        "CYCLIC_MESSAGES_VIDEO_MAX_SECONDS if the presentation is longer",
    )
    logger.warning(
        "Cyclic message run %s stopped recording after %.1fs without the line "
        "messages reporting that they finished",
        run.run_id,
        settings.CYCLIC_MESSAGES_VIDEO_MAX_SECONDS,
    )
    await _close_clip(run, closed_by=_TIME_LIMIT)


async def _stop_guard(run: _ActiveRun) -> None:
    """End the clip's deadline task. Awaiting the cancellation is not optional
    -- an unawaited one is the pending-task warning that fails the test suite."""
    task, run.clip_guard = run.clip_guard, None
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# --- capturing ------------------------------------------------------------


def _screenshot_name(sequence: int, event: str, at: datetime | None) -> str:
    """Filename that sorts by sequence and still says what it is, e.g.
    ``007_cyclic-message-shown_14-32-14``."""
    stamp = (at or datetime.now()).strftime(_FILE_TIME_FORMAT)
    return f"{sequence:03d}_{event}_{stamp}"


async def _record(
    run: _ActiveRun,
    detected: game_log.DetectedEvent,
    *,
    position: int | None,
    source: CyclicEventSource = CyclicEventSource.LOG,
    at: datetime | None = None,
) -> None:
    """Append one event to the run, screenshotting it unless it is a marker.

    ``at`` overrides the log line's own stamp, which only a sampled event needs:
    every frame of one pass is opened by the same boundary line, so taking the
    time from it would stamp them all identically.
    """
    # Reserved before the first await: see _ActiveRun.next_sequence.
    run.next_sequence += 1
    sequence = run.next_sequence
    at = at or detected.line.timestamp

    screenshot: str | None = None
    capture_error: str | None = None

    if not detected.capture:
        # A loop boundary: structure, not a frame. Deliberately still recorded.
        pass
    elif sequence > settings.CYCLIC_MESSAGES_MAX_EVENTS:
        capture_error = (
            f"Event cap of {settings.CYCLIC_MESSAGES_MAX_EVENTS} reached; "
            "the event was recorded without a screenshot"
        )
    else:
        if detected.delay_ms:
            # The transition lands; the strip draws the new text a beat later.
            await asyncio.sleep(detected.delay_ms / 1000)
        name = _screenshot_name(sequence, detected.event, at)
        try:
            result = await obs_service.take_screenshot(
                ScreenshotRequest(
                    image_format="png",
                    width=settings.CYCLIC_MESSAGES_SCREENSHOT_WIDTH,
                    file_name=name,
                    output_dir=run.output_dir,
                )
            )
        except AppException as exc:
            # One failed screenshot isn't a reason to end the run -- OBS
            # re-identifies on the next request even if its socket dropped.
            capture_error = exc.message
            _note(run, f"{detected.event}: {exc.message}")
            logger.warning("Screenshot failed for %s: %s", detected.event, exc.message)
        else:
            screenshot = Path(result.file_path).name if result.file_path else None

    run.events.append(
        CyclicEvent(
            sequence=sequence,
            event=detected.event,
            at=at,
            summary=detected.summary,
            cycle=max(1, run.cycle),
            position=position,
            fields=dict(detected.fields),
            captured=detected.capture,
            screenshot=screenshot,
            capture_error=capture_error,
            source=source,
            log_line=detected.line.raw,
        )
    )
    if source is CyclicEventSource.SAMPLED:
        run.sampled_count += 1
    _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))


# --- the amount, and the denomination that scales it ----------------------


def _read_denomination(path: Path) -> float | None:
    """The denomination the game last logged, read *backwards* out of the log.

    The game writes ``UpdatePayTable`` only when a denomination changes, so a
    run that starts on an already-running game would otherwise see none until
    the player changed one -- and every "GAME PAYS" until then would report
    cents. Reading backwards costs one seek and answers immediately.
    """
    found = log_search.last_match(path, game_log.PAYTABLE_LOADED)
    if found is None:
        return None
    return _as_denomination(found.match.groupdict().get("denom"))


def _as_denomination(raw: str | None) -> float | None:
    """Parse a logged denomination, refusing the values that cannot scale."""
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _pays_summary(run: _ActiveRun, detected: game_log.DetectedEvent) -> str:
    """What the strip is about to display, in the units it displays it in.

    The log states cents; the strip states credits. Without a denomination the
    cents are reported *as cents and said to be* -- guessing a rate here would
    put a number on screen that is wrong by a factor of the denomination while
    looking exactly as confident as a right one.
    """
    cents = _as_denomination(detected.fields.get("win_cents"))
    if cents is None:
        return detected.summary
    if run.denomination is None:
        return f"Game pays {cents:g} cents (denomination not logged yet)"
    return f"Game pays {cents / run.denomination:g}"


def _pays_event(
    run: _ActiveRun, detected: game_log.DetectedEvent
) -> game_log.DetectedEvent:
    """``cyclic-game-pays`` with the credits worked out and both units kept."""
    fields = dict(detected.fields)
    cents = _as_denomination(detected.fields.get("win_cents"))
    if run.denomination is not None:
        fields["denom"] = f"{run.denomination:g}"
        if cents is not None:
            fields["win_credits"] = f"{cents / run.denomination:g}"
    return dataclasses.replace(
        detected, summary=_pays_summary(run, detected), fields=fields
    )


# --- sampling the silent window -------------------------------------------


async def _sampler(run: _ActiveRun, opener: game_log.DetectedEvent) -> None:
    """Screenshot the line-message pass on a timer, until the pass ends.

    Cancelled by :func:`_stop_sampler` the moment the log says the pass is
    over, so the interval decides how *often* a frame is taken and never how
    many -- the deadline only exists for the pass whose closing line never
    comes.
    """
    interval = max(0.1, settings.CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS)
    deadline = time.monotonic() + max(
        interval, settings.CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS
    )
    index = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        index += 1
        elapsed = index * interval
        run.position += 1
        await _record(
            run,
            dataclasses.replace(
                opener,
                event=_LINE_PAYS_SHOWN,
                summary=f"Line message on screen {elapsed:.1f}s into the pass",
                fields={
                    "sample_index": str(index),
                    "elapsed_seconds": f"{elapsed:.1f}",
                },
                # The pass is already running; a frame is wanted now, not later.
                delay_ms=0,
            ),
            position=run.position,
            source=CyclicEventSource.SAMPLED,
            at=datetime.now(),
        )
    _note(
        run,
        f"sampling stopped after {settings.CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS:g}s "
        "without the line-message pass reporting that it finished; raise "
        "CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS if the pass is longer",
    )
    logger.warning(
        "Cyclic message run %s stopped sampling after %.1fs without the "
        "line-message pass reporting that it finished",
        run.run_id,
        settings.CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS,
    )


async def _start_sampler(run: _ActiveRun, opener: game_log.DetectedEvent) -> None:
    """Begin sampling a line-message pass, replacing any pass still running --
    two spins close together should not leave two samplers on one run."""
    if not settings.CYCLIC_MESSAGES_SAMPLE_LINE_PAYS or run.finishing:
        return
    await _stop_sampler(run)
    run.sampler = asyncio.create_task(
        _sampler(run, opener), name=f"cyclic-messages-sampler-{run.run_id}"
    )


async def _stop_sampler(run: _ActiveRun) -> None:
    """End the sampler. Awaiting the cancellation is not optional -- an
    unawaited one is the pending-task warning that fails the test suite."""
    task, run.sampler = run.sampler, None
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _is_repeat(run: _ActiveRun, detected: game_log.DetectedEvent) -> bool:
    """Whether this event is the same one already recorded moments ago, by the
    game's own timestamps -- the publish line and the transition it caused are
    one event logged twice. A line with no usable timestamp is always let through."""
    at = detected.line.timestamp
    if at is None:
        return False
    previous = run.last_seen.get(detected.event)
    run.last_seen[detected.event] = at
    if previous is None:
        return False
    gap = (at - previous).total_seconds()
    return 0 <= gap < settings.CYCLIC_MESSAGES_DEBOUNCE_SECONDS


async def _handle(run: _ActiveRun, raw: str) -> None:
    """Capture one appended line, if a cyclic rule claims it.

    This is where a sequence is assembled, and the two families are assembled
    differently. For the idle strip a start is held until a message proves the
    loop real, messages carry their position, and a completion counts only if
    the loop showed something. For the win strip the amount opens a new
    sequence, the rack-up ending starts the sampler, and the pass finishing
    stops it -- so the sampler's lifetime is exactly the window the log brackets.
    """
    try:
        line = game_log.parse_line(raw)
        if line is None:
            return

        # Not an event: the game states its denomination on its own line, and
        # this is the only thing that turns logged cents into shown credits.
        paytable = game_log.PAYTABLE_LOADED.search(line.message)
        if paytable is not None:
            run.denomination = (
                _as_denomination(paytable.groupdict().get("denom")) or run.denomination
            )

        detected = game_log.match(line, game_log.CYCLIC_MESSAGE_RULES)
        if detected is None or _is_repeat(run, detected):
            return

        if detected.event == _CYCLE_STARTED:
            # Held, not recorded: most armed sequences never display anything.
            run.pending_start = detected
            run.position = 0
            return

        if detected.event == _MESSAGE_EVENT:
            pending, run.pending_start = run.pending_start, None
            if pending is not None:
                run.cycle += 1
                await _record(run, pending, position=None)
            elif run.cycle == 0:
                # Tracking began mid-loop; the run's first loop is still a loop.
                run.cycle = 1
            run.position += 1
            await _record(run, detected, position=run.position)
            return

        if detected.event == _CYCLE_COMPLETED:
            # A sequence that ended without showing anything is not a loop.
            if run.position > 0:
                run.cycle_count += 1
                await _record(run, detected, position=None)
            run.pending_start = None
            run.position = 0
            return

        if detected.event == _GAME_PAYS:
            # A win presentation is its own cyclic sequence: it opens here, on
            # the one line that says what the strip is about to display.
            run.pending_start = None
            run.cycle += 1
            run.position = 1
            # Recording starts before the frame is taken, so the GAME PAYS
            # banner this event screenshots is inside the video as well.
            await _open_clip(run)
            await _record(run, _pays_event(run, detected), position=run.position)
            return

        if detected.event == _WIN_PRESENTED:
            # The meter has finished counting, so the line messages start now --
            # and nothing further is logged until the pass ends.
            if run.cycle == 0:
                run.cycle = 1
            run.position += 1
            await _record(run, detected, position=run.position)
            await _start_sampler(run, detected)
            return

        if detected.event == _LINE_PAYS_CYCLE_DONE:
            # Stop first: the pass is over, and a frame taken after this line
            # would be of the next pass rather than of the one being recorded.
            await _stop_sampler(run)
            run.cycle_count += 1
            run.position += 1
            await _record(run, detected, position=run.position)
            # Closed after that frame rather than before it: this line is the
            # end of the pass, so the last line message belongs in the video.
            await _close_clip(run, closed_by=_LINE_PAYS_CYCLE_DONE)
            return

        if detected.event == _RESULTS_CYCLE_STOPPED:
            # The strip has stopped cycling results, so any pass still being
            # sampled or recorded has been cut short by whatever stopped it.
            await _stop_sampler(run)
            await _close_clip(run, closed_by=_RESULTS_CYCLE_STOPPED)
            await _record(run, detected, position=None)
            run.position = 0
            return

        # Anything else the rule set recognises -- 'cyclic-game-over' and the
        # rack-up marker -- is a message in the sequence that is current.
        if run.cycle == 0:
            run.cycle = 1
        position: int | None = None
        if detected.capture:
            run.position += 1
            position = run.position
        await _record(run, detected, position=position)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if message not in run.errors:
            run.errors.append(message)
        logger.exception("Cyclic message run %s hit an error", run.run_id)


async def _watch(run: _ActiveRun) -> None:
    """Follow the log until cancelled."""
    logger.info("Cyclic message run %s following %s", run.run_id, run.log.path)
    await run.log.follow(lambda raw: _handle(run, raw))


async def _stop_watching(run: _ActiveRun) -> None:
    """End the watcher task. Awaiting the cancellation is not optional -- an
    unawaited one is the pending-task warning that fails the test suite."""
    task = run.task
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _finish(run: _ActiveRun, *, status: CyclicRunState) -> CyclicRunDetail:
    """Stop the watcher, take one last look, end any clip, seal the manifest."""
    await _stop_watching(run)

    # The watcher may have been cancelled part-way through filing a clip, which
    # goes on without it. Waited for here so the record this returns has it.
    await _await_closing(run)

    # Both before and after the catch-up read: before, because a pass still
    # being sampled ends with the run; after, because the flag is what stops
    # the replayed lines opening a new one.
    run.finishing = True
    await _stop_sampler(run)

    # Events logged between the last poll and the stop request still count.
    for raw in run.log.new_lines():
        await _handle(run, raw)

    await _stop_sampler(run)
    # A presentation still being recorded ends with the run; the clip says so
    # rather than being dropped, because a short video is still the win.
    await _close_clip(run, closed_by=_RUN_STOPPED)

    detail = _detail(run, status=status, stopped=datetime.now())
    _write_manifest(run, detail)
    logger.info(
        "Cyclic message run %s %s with %d messages over %d loops and %d clips",
        run.run_id,
        status.value,
        detail.message_count,
        detail.cycle_count,
        len(detail.videos),
    )
    return detail


# --- public API -----------------------------------------------------------


async def start() -> CyclicStatus:
    """Begin a run against the active game's log. OBS is connected first and
    allowed to fail the request -- a run that can't screenshot is just text."""
    global _run
    async with _get_lock():
        if _run is not None:
            raise CyclicMessagesAlreadyRunningError(
                f"Cyclic message run {_run.run_id} is already in progress; "
                "stop it first"
            )

        name, config = _active_config()
        log_path = _log_for(name, config)

        await obs_service.connect()
        # Worth trying, not worth failing for -- the scene may already be correct.
        with contextlib.suppress(AppException):
            await obs_service.select_current_game_window()

        started = datetime.now()
        run_id = started.strftime(_RUN_ID_FORMAT)
        output_dir = f"{settings.CYCLIC_MESSAGES_DIR_NAME}/{run_id}"
        directory = resolve_subdirectory(settings.obs_screenshot_dir, output_dir)
        directory.mkdir(parents=True, exist_ok=True)

        run = _ActiveRun(
            run_id=run_id,
            game=name,
            directory=directory,
            output_dir=output_dir,
            # Opened here so the run starts from the log's end, not its history.
            log=LogFollower(
                log_path, poll_seconds=settings.CYCLIC_MESSAGES_POLL_SECONDS
            ),
            started_at=started,
            # Read backwards now rather than waited for: the game states its
            # denomination only when one changes, so a run against an
            # already-running game would report cents until the player did.
            denomination=_read_denomination(log_path),
        )

        # Deliberately not recording yet: a clip is a win presentation, and
        # this run has not seen one. See the module docstring.
        _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))
        run.task = asyncio.create_task(_watch(run), name=f"cyclic-messages-{run_id}")
        _run = run

    return status()


async def stop() -> CyclicRunDetail:
    """End the run and return its finished record."""
    global _run
    async with _get_lock():
        run = _run
        if run is None:
            raise CyclicMessagesNotRunningError(
                "No cyclic message run is in progress, so there is nothing to stop"
            )
        _run = None

    return await _finish(run, status=CyclicRunState.COMPLETED)


def status() -> CyclicStatus:
    """Report the run in progress, if any. Never fails."""
    run = _run
    if run is None:
        return CyclicStatus(active=False)

    elapsed = datetime.now() - run.started_at
    recent = settings.CYCLIC_MESSAGES_RECENT_EVENTS
    return CyclicStatus(
        active=True,
        run_id=run.run_id,
        game=run.game,
        started_at=run.started_at,
        duration_ms=max(0, int(elapsed.total_seconds() * 1000)),
        message_count=_message_count(run),
        sampled_count=run.sampled_count,
        cycle_count=run.cycle_count,
        recording=_is_recording(run),
        video_count=len(run.videos),
        sampling=run.sampler is not None and not run.sampler.done(),
        recent_events=sorted(run.events, key=lambda event: event.sequence)[-recent:],
        errors=list(run.errors),
    )


def list_runs() -> list[CyclicRunSummary]:
    """Every run on disk, newest first -- run ids are timestamps, so sorting by
    name is sorting by time."""
    root = _capture_root()
    if not root.is_dir():
        return []

    summaries: list[CyclicRunSummary] = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name, reverse=True):
        if not directory.is_dir():
            continue
        detail = _read_manifest(directory / MANIFEST_NAME)
        if detail is not None:
            summaries.append(CyclicRunSummary.model_validate(detail.model_dump()))
    return summaries


def _run_directory(run_id: str) -> Path:
    """Resolve one run's directory from an id that came off the wire."""
    try:
        directory = resolve_within(_capture_root(), run_id)
    except UnsafeNameError as exc:
        raise CyclicMessagesRunNotFoundError(
            f"Invalid cyclic message run id: {exc.reason}"
        ) from exc
    if not directory.is_dir():
        raise CyclicMessagesRunNotFoundError(f"No cyclic message run named {run_id!r}")
    return directory


def get_run(run_id: str) -> CyclicRunDetail:
    """One run, with all of its events."""
    detail = _read_manifest(_run_directory(run_id) / MANIFEST_NAME)
    if detail is None:
        raise CyclicMessagesRunNotFoundError(
            f"The record for cyclic message run {run_id!r} is missing or unreadable"
        )
    return detail


def file_path(run_id: str, file_name: str) -> Path:
    """Resolve one screenshot or video for serving. Both halves come from the
    URL, so both go through the path guards."""
    directory = _run_directory(run_id)
    try:
        target = resolve_within(directory, file_name)
    except UnsafeNameError as exc:
        raise CyclicMessagesRunNotFoundError(
            f"Invalid file name: {exc.reason}"
        ) from exc
    if not target.is_file():
        raise CyclicMessagesRunNotFoundError(
            f"No file named {file_name!r} in cyclic message run {run_id!r}"
        )
    return target


async def abort() -> None:
    """End any run because the process is shutting down. Marks the record
    ``interrupted``, not ``completed`` -- nobody pressed stop. Runs before OBS
    disconnects in the lifespan, so the recording is stopped rather than left on."""
    global _run
    run = _run
    if run is None:
        return
    _run = None
    await _finish(run, status=CyclicRunState.INTERRUPTED)


async def reset() -> None:
    """Drop all state, including the lock bound to this loop. Tests only."""
    global _run, _lock
    run, _run = _run, None
    if run is not None:
        run.finishing = True
        await _stop_watching(run)
        await _stop_sampler(run)
        await _stop_guard(run)
        await _await_closing(run)
    _lock = None
