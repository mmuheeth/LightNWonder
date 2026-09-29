"""Cyclic Messages: follows the game's log for the rotating message strip,
screenshotting every message and recording video of the run.

Sibling of :mod:`app.services.event_capture` (same shape: singleton, a
watcher over a :class:`LogFollower`), but differs because a cyclic message is
not a gameplay event: the rule set is **only**
:data:`game_log.CYCLIC_MESSAGE_RULES`, never merged with a game's own;
``capture=False`` means **no screenshot** here, not "ignore" as in event
capture; video is recorded **per win, not per run**
(``cyclic-game-pays`` -> ``cyclic-line-pays-cycle-finished``); and the **win
strip is bracketed, not followed** -- the game logs almost nothing during the
count-up (measured 6.5-7.3s for one-line wins, 78.8s for 40-line ones, ~2s a
line), so those frames are taken on a timer and marked
:attr:`CyclicEventSource.SAMPLED` rather than tied to a log line.

The between-spins strip (``cyclic-idle-strip-started``/``-ended``) is a
second, separately bracketed/sampled window -- not the win's, and not always
separate from it either: taking a win before its line messages loop once
makes the strip run straight on with no break on screen, so the window is
carried on (:func:`_carry_on`) rather than closed and reopened. Both strips
are now sampled at 1.0s (not the 2.0s once assumed, which silently dropped
captions -- see ``CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS``).

Every clip is read back afterwards (:func:`_recover`) because the live stills
cannot keep up with either strip, and recovery runs on a background queue
(:func:`_drain_recovery`) so it never competes with capture -- reading
inline once cost a strip its first fifteen seconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import errno
import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    ObsRequestError,
)
from app.schemas.cyclic_messages import (
    CyclicEvent,
    CyclicEventSource,
    CyclicFrameReading,
    CyclicLiveView,
    CyclicRunDetail,
    CyclicRunState,
    CyclicRunSummary,
    CyclicRunVideo,
    CyclicStatus,
)
from app.schemas.obs import ObsRecordStatus, ScreenshotRequest
from app.services import analyze_spin as analyze_spin_service
from app.services import obs as obs_service
from app.utils import game_log, log_search
from app.utils.log_tail import LogFollower
from app.utils.paths import UnsafeNameError, resolve_subdirectory, resolve_within

if TYPE_CHECKING:  # pragma: no cover - the cycle below is a runtime one only
    from collections.abc import Generator

    # `cyclic_text` imports this module (to find a run's clip), so importing
    # it back at runtime would cycle. Annotation-only thanks to `__future__
    # annotations`, so mypy gets the name and the interpreter never needs it.
    from app.services import cyclic_text

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
_NO_PAY = "cyclic-no-pay"
_WIN_PRESENTED = "cyclic-win-presented"
_LINE_PAYS_CYCLE_DONE = "cyclic-line-pays-cycle-finished"
_IDLE_STRIP_STARTED = "cyclic-idle-strip-started"
_IDLE_STRIP_ENDED = "cyclic-idle-strip-ended"
_RESULTS_CYCLE_STOPPED = "cyclic-results-cycle-stopped"

# Not rules: the sampled frames, which by definition no log line produces.
# One name per strip, because they are different strips showing different
# things and a reader sorting a run by event should not have to guess which.
_LINE_PAYS_SHOWN = "cyclic-line-pays-shown"
_IDLE_MESSAGE_SHOWN = "cyclic-idle-message-shown"
# Not a sampled frame either: one recovered from the clip afterwards, because
# the live stills could not keep up with the window. See :func:`_recover`.
_RECOVERED = "cyclic-message-recovered"

# Not rules either: the two ways a clip ends without a log line saying so.
_RUN_STOPPED = "run-stopped"
_TIME_LIMIT = "time-limit"
_STRIP_LOOPED = "strip-looped"

# OBS reports the recording path before Windows lets go of the file handle,
# so a move is retried rather than failed. Not just tidiness: an encoder
# behind on its backlog kept writing for 66s after StopRecord answered on one
# FortuneOx win, so this is a short courtesy, never a wait for the file -- see
# `_file_clip` for what happens once retries run out.
_MOVE_ATTEMPTS = 5
_MOVE_RETRY_SECONDS = 0.3

# How often recovery polls for OBS releasing a clip, and how long it waits
# before reading one anyway. Reading early loses the tail silently (the muxer
# hasn't caught up); the cap only bounds a handle that never closes (OBS died
# mid-write) -- a normal drain is seconds to about a minute.
_RELEASE_POLL_SECONDS = 0.5
_RELEASE_WAIT_SECONDS = 300.0

# How long closing a clip waits for OBS to have *started* it first. StartRecord
# answers immediately but the output only comes up once the encoder is ready --
# measured at 13s under load, past the 3s `obs.start_recording` settles for. A
# StopRecord sent in that gap is refused as "not recording" (501) and OBS then
# starts anyway with nothing left to stop it -- measured once: 4.5 minutes and
# 500MB into a run's directory, failing every later clip with "code 500"
# because OBS won't change its record directory mid-recording.
_START_WAIT_SECONDS = 30.0
_START_POLL_SECONDS = 0.25

# How long a stop waits for the capture loop to finish its current frame
# before cancelling outright. Sized above the 1.5-7s an OBS screenshot costs
# while recording, so only a wedged OBS ever reaches the cancel.
_SAMPLER_STOP_SECONDS = 10.0


# What a clip is a recording of. On the clip so its file name, and the run
# view beside it, say which strip it covers -- a run holds both kinds now.
#
# Public because which *bands* a clip's strip draws follows from which strip it
# is of, and that answer lives in `cyclic_text.clip_regions` -- one place, so a
# caption read back off a clip cannot come from a different band than the same
# caption read off the stills beside it.
WIN_VIDEO = "win-video"
IDLE_VIDEO = "idle-video"

# Both strips in one window: take a win before its line messages loop once and
# the strip runs straight from them into "GAME OVER / GAME PAYS n / PLAY 880
# CREDITS" with no break, so the window carries on rather than being torn down
# and rebuilt (see the ``cyclic-idle-strip-started`` branch of :func:`_handle`).
# Its own kind because it decides which bands get drawn (both), which must
# match for the stills and the clip read back beside them.
WIN_THEN_IDLE = "win-then-idle"

# Which strip each is, for a summary a reader sees rather than a rule name.
_STRIP_NAMES = {
    WIN_VIDEO: "the win presentation",
    IDLE_VIDEO: "the between-spins strip",
    WIN_THEN_IDLE: "the win presentation and the strip after it",
}


@dataclass(frozen=True)
class _Pending:
    """One written frame waiting to be read once its pass closes.

    A path and a sequence, not the picture -- reading is deferred, not
    duplicated. A plain list, not a queue: nothing drains it mid-pass.
    """

    sequence: int
    path: Path
    regions: tuple[str, ...]
    """Which strip lines to read this frame for, stamped at capture time
    rather than looked up later -- by read time the run has moved on and the
    window a frame came from is the only thing that still knows."""


@dataclass
class _Clip:
    """The recording in progress, and the win presentation it belongs to."""

    cycle: int
    started_at: datetime
    kind: str = WIN_VIDEO
    """Which strip this is a recording of; see :data:`WIN_VIDEO`."""


@dataclass
class _Recovery:
    """A filed clip whose messages have not been read back yet.

    Queued rather than recovered on the spot: the next window is often already
    on screen by the time a clip is filed, and reading inline there measured
    fifteen seconds lost off an eight-second strip. :func:`_drain_recovery`
    works the queue in the background, giving way the instant a window opens.
    """

    video: CyclicRunVideo
    queued_at: float = field(default_factory=time.monotonic)
    """When the clip was filed; how long recovery has waited on OBS to
    release it -- see `_still_writing`."""

    written: int = 0
    """Frames of this clip already written out and read.

    Lets an interrupted recovery resume instead of restarting. Counted in
    *changed* frames (what :func:`_recover` walks), not decoded ones, so
    re-walking from the top reproduces the same sequence.
    """


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
    """Sequences handed out so far. Not ``len(events) + 1``: the watcher and
    sampler both append after awaiting a screenshot, so list length would race."""

    denomination: float | None = None
    """Cents per credit, from the game's own ``UpdatePayTable`` line, read
    backwards at start -- turns the log's only amount (16800 cents) into what
    the strip displays (168)."""

    # --- loop bookkeeping -------------------------------------------------
    cycle: int = 0
    """Loops begun so far; also the number every event of the current one carries."""

    position: int = 0
    """Messages shown in the current loop."""

    cycle_count: int = 0
    """Loops that ran to completion."""

    pending_start: game_log.DetectedEvent | None = None
    """A ``cyclic-cycle-started`` held back until a message proves the loop
    real -- measured 115 sequence-ends against only 30 starts that showed one,
    so the loop count matches what a person actually watched."""

    clip: _Clip | None = None
    """The win presentation being recorded, if one is; ``None`` while idle."""

    clip_guard: asyncio.Task[None] | None = None
    """Closes :attr:`clip` if its closing line never arrives. Its own task
    because the watcher awaits handlers in turn -- waiting inline would stop
    the run reading the very line it is waiting for."""

    closing: asyncio.Task[None] | None = None
    """The clip being stopped and filed right now, if one is. Shielded from
    the watcher's own cancellation, so a run stopped mid-close doesn't leave
    OBS recording with nothing on record; :func:`_finish` awaits it."""

    videos: list[CyclicRunVideo] = field(default_factory=list)
    """One entry per win presentation, failures included -- a clip OBS refused
    is a fact about that win, not an absence."""

    task: asyncio.Task[None] | None = None

    sampler: asyncio.Task[None] | None = None
    """Fills the silent line-message window with frames; see the module
    docstring. Its own task because the watcher awaits handlers in turn."""

    sampled_count: int = 0

    stop_sampling: bool = False
    """Asks the capture loop to finish cooperatively (see :func:`_stop_sampler`)
    rather than being cancelled mid-screenshot, after its sequence number is
    already spent."""

    sample_rate: float = 0.0
    """Frames/second the loop actually achieved this pass -- measured, not
    assumed, since an OBS round trip decides the real rate. Freezes at the
    achieved rate once the sampler is cancelled."""

    sample_frames: int = 0
    """Frames the current pass has taken; the numerator of :attr:`sample_rate`."""

    pass_open: bool = False
    """Whether a presentation window is open right now. Set on
    ``cyclic-game-pays``, cleared when the pass ends; decides whether a
    written frame is collected for :func:`_read_pending`."""

    live_kind: str = ""
    """Which strip :attr:`live_cycles` is of -- :data:`WIN_VIDEO`,
    :data:`IDLE_VIDEO` or :data:`WIN_THEN_IDLE` when it spans both halves of
    one spin. Distinct from :attr:`pass_kind` (cleared when a window closes):
    the live view keeps showing a finished window's frames, so this is what
    still names them correctly. :func:`_live_on` is the only writer."""

    live_cycles: list[int] = field(default_factory=list)
    """Cyclic sequences the live view shows, oldest first. Not ``cycle``,
    which walks on through the attract loops after a win. A list rather than
    one sequence because a spin can be two windows -- a win taken after its
    line messages finish opens a second sequence for the between-spins strip,
    and it is *appended* here rather than replacing the view, so the card
    keeps the win's frames instead of emptying at the moment it was taken.
    Reset to one entry whenever a genuinely new spin's window opens.
    """

    win_cycle: int | None = None
    """The win whose between-spins strip has not run yet. Set when the win
    opens its window, consumed by the ``cyclic-idle-strip-started`` that
    follows -- tells whether that strip continues this spin (append to the
    live view) or is a spin of its own (start it again)."""

    pass_kind: str = ""
    """Which strip the open window is of -- :data:`WIN_VIDEO`,
    :data:`IDLE_VIDEO` or :data:`WIN_THEN_IDLE`, empty when none is open.
    Keeps one strip's closing line from tearing down the other's window when
    the two overlap in the log (a win taken early makes
    ``cyclic-line-pays-cycle-finished`` arrive after the game has gone idle).
    """

    pass_interval: float = 0.0
    """The interval the open window asked for, reported beside the achieved
    rate. Read from the window rather than one setting, since the two strips
    can sample at different speeds and the wrong one misreports coverage."""

    pass_regions: tuple[str, ...] = ()
    """Lines the strip is drawing in the window currently open. Set when one
    opens, and stamped onto every frame taken inside it."""

    # --- the knobs the capture loop reads, and why they are here ----------
    # Each was once a parameter of :func:`_sampler`, until a carried window
    # (win taken early, running on into the between-spins strip) needed a
    # different deadline/event/watch mid-flight with no gap in the frames. So
    # they live on the run and the loop re-reads them each frame; :func:`_retune`
    # is the whole of "the window changed".
    pass_opener: game_log.DetectedEvent | None = None
    """The log line each sampled frame quotes as the boundary that opened it."""

    pass_event: str = ""
    """Name stamped on a sampled frame -- one per strip, so a reader sorting a
    run by event never has to guess which strip a frame is of."""

    pass_deadline: float = 0.0
    """When the capture loop gives up, on the monotonic clock -- absolute, not
    a duration, since a retune moves it mid-window."""

    pass_closing_expected: bool = False
    """Whether reaching that deadline is worth complaining about. See
    :func:`_sampler`: the game writes the end of a win pass and writes nothing
    at all for the end of a between-spins one."""

    pass_watch: cyclic_text.LoopWatch | None = None
    """Ends the window once the strip has come back round, or ``None`` to run
    to the deadline. Decided from the picture -- see :func:`_loop_watch`."""

    lines_running: bool = False
    """A carried window (:data:`WIN_THEN_IDLE`) whose line messages have not
    finished yet. No loop watch while true: band 2 laps in about five seconds
    but band 1 may still be walking forty lines over a minute -- measured on
    one real case, watching early closed the window at "Line 9 Pays 15" and
    lines 10-40 played to nothing, stills and clip both."""

    unfiled: list[int] = field(default_factory=list)
    """Frames captured with no window open, awaiting a cycle. A log-driven
    frame can land ~400ms before its window's own marker line (e.g.
    ``cyclic-game-over`` before ``cyclic-idle-strip-started``), landing in the
    cycle that just ended and vanishing from :func:`live`. Re-filed by
    :func:`_adopt` once the next window opens."""

    pending_reads: list[_Pending] = field(default_factory=list)
    """Frames captured, not yet read. Worked through by :func:`_read_pending`
    only once a pass closes, so reading never competes with capture --
    includes frames taken before any window was open."""

    reading_now: bool = False
    """Whether :func:`_read_pending` is running right now -- the only way
    ``status()``/``live()`` can tell a read from an idle run, since reading is
    a plain awaited call rather than its own task."""

    read_count: int = 0
    """Frames read so far this run, across every pass -- never reset between
    them, unlike :attr:`pending_reads`."""

    pending_recovery: list[_Recovery] = field(default_factory=list)
    """Filed clips not yet read back, drained by :func:`_drain_recovery`
    whenever no window is open. Queued rather than read inline; see
    :class:`_Recovery`."""

    recovering: asyncio.Task[None] | None = None
    """The drain task, if running -- the only work that deliberately runs
    alongside the watcher, since blocking a log-line handler on it would delay
    the next window opening."""

    stop_recovery: bool = False
    """Asks the drain to give way. Set when a window opens and when the run is
    sealed; the most a window can overlap with a recovery is one frame."""

    recovered_count: int = 0
    """Frames recovered from a clip so far this run, reported beside the
    sampled count -- different evidence: what the stills went past."""

    no_pay_count: int = 0
    """Spins that paid nothing, so showed no win strip. Counted so a quiet run
    (nobody's winning) reads differently from a broken one -- measured 29
    losing vs 26 winning spins on FortuneOx's own log."""

    no_pay_since_win: int = 0
    """Of those, how many since the last win -- reset by ``cyclic-game-pays``.
    The live view's number: 'the last four spins paid nothing' is what makes a
    waiting card obviously correct rather than possibly stuck."""

    index: dict[int, int] = field(default_factory=dict)
    """Sequence to position in :attr:`events`. Needed because the watcher and
    sampler both append concurrently, so ``events[sequence - 1]`` cannot be
    trusted."""

    last_write: float = 0.0
    """When the manifest was last flushed, on the monotonic clock. See
    :func:`_touch`: the manifest is the whole run every time it is written."""

    finishing: bool = False
    """Set once the run is being sealed, to refuse a *new* sampling pass or
    clip -- the final catch-up read replays history, and a ``WinBangDone``
    among it would otherwise open a pass outliving the run (a test failure
    under ``filterwarnings = error``)."""


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


def _touch(run: _ActiveRun) -> None:
    """Flush the manifest, throttled to once every
    ``CYCLIC_MESSAGES_MANIFEST_INTERVAL_SECONDS`` -- writing the whole run per
    change would be quadratic at ten frames a second. A stop calls
    :func:`_write_manifest` directly, never throttled."""
    now = time.monotonic()
    if now - run.last_write < settings.CYCLIC_MESSAGES_MANIFEST_INTERVAL_SECONDS:
        return
    run.last_write = now
    _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))


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
    """Filename that says which sequence the clip covers and which strip it is
    of, e.g. ``cycle-003_win-video_16-36-21.mp4`` or
    ``cycle-004_idle-video_16-36-40.mp4`` -- so it sits beside that sequence's
    own frames in a directory listing rather than under an OBS timestamp."""
    stamp = clip.started_at.strftime(_FILE_TIME_FORMAT)
    return f"cycle-{clip.cycle:03d}_{clip.kind}_{stamp}{suffix}"


async def _open_clip(
    run: _ActiveRun, *, kind: str = WIN_VIDEO, replaces: str = _GAME_PAYS
) -> None:
    """Begin recording the window that just opened. Each strip gets its own
    clip, switched on separately (``CYCLIC_MESSAGES_RECORD_VIDEO`` /
    ``..._RECORD_IDLE_VIDEO``) since recording costs screenshot rate. Never
    raises: a failed recording still screenshots every message, and the
    reason is recorded on the run's list of clips.
    """
    wanted = (
        settings.CYCLIC_MESSAGES_RECORD_IDLE_VIDEO
        if kind == IDLE_VIDEO
        else settings.CYCLIC_MESSAGES_RECORD_VIDEO
    )
    if not wanted or run.finishing:
        return

    # A window opening before the previous one reported finishing gets its own
    # clip; two of them in one file could not be told apart afterwards.
    # ``replaces`` is what that predecessor's record says ended it, which is
    # this event and not a guess -- a win clip cut off by the game going idle
    # was reading as "the next win began".
    await _close_clip(run, closed_by=replaces)

    clip = _Clip(cycle=max(1, run.cycle), started_at=datetime.now(), kind=kind)
    try:
        await _refuse_if_recording()
        await obs_service.start_recording(_recording_dir(run.run_id))
    except AppException as exc:
        run.videos.append(
            CyclicRunVideo(
                kind=clip.kind,
                cycle=clip.cycle,
                started_at=clip.started_at,
                error=exc.message,
            )
        )
        _note(run, f"video: {exc.message}")
        logger.warning(
            "Could not record %s for %s: %s", clip.kind, run.run_id, exc.message
        )
        return

    run.clip = clip
    run.clip_guard = asyncio.create_task(
        _guard_clip(run), name=f"cyclic-messages-clip-{run.run_id}"
    )
    logger.info(
        "Cyclic message run %s is recording %s for sequence %d",
        run.run_id,
        clip.kind,
        clip.cycle,
    )


async def _refuse_if_recording() -> None:
    """Refuse to start a clip while OBS is already recording -- by now our own
    previous clip is stopped, so this is either it still draining (an encoder
    behind keeps writing after StopRecord, up to a minute on a big win) or
    somebody else's recording. Not waited out, since a wait as long as a drain
    would cost the new window its first frames; how long it has been running
    is reported so a reader can tell the two cases apart.
    """
    try:
        status = await obs_service.record_status()
    except AppException:
        return
    if status.active:
        raise ObsRequestError(
            f"OBS was already recording, {_recording_for(status)} in, so this "
            "window has no clip -- OBS records one output at a time. Under a "
            "few seconds that is the previous clip still being written out, "
            "which OBS goes on doing after it is told to stop when its encoder "
            "is behind. Longer than that it is a recording started somewhere "
            "else (the dashboard's OBS card, or OBS itself) that is still "
            "running, and no clip will be saved until it is stopped. Its "
            "screenshots are unaffected."
        )


def _recording_for(status: ObsRecordStatus) -> str:
    """How long OBS says it has been recording, in words. Falls back to the raw
    timecode, and then to nothing at all, rather than reporting a confident 0s
    for a status that did not carry one."""
    seconds = status.duration_ms / 1000
    if seconds <= 0:
        return status.timecode or "an unreported time"
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.0f}m"


async def _clear_stray_recording() -> str | None:
    """Stop a recording nobody is left to stop, before the run begins --
    e.g. a backend killed mid-run, which never reaches :func:`abort`, leaving
    OBS recording and refusing every later clip until stopped by hand. This is
    the one safe moment (no window open yet). A spin-analysis recording is
    left alone rather than interrupted. Returns what to say about it on the
    run, or ``None``; never raises.
    """
    if not (
        settings.CYCLIC_MESSAGES_RECORD_VIDEO
        or settings.CYCLIC_MESSAGES_RECORD_IDLE_VIDEO
    ):
        return None
    try:
        status = await obs_service.record_status()
    except AppException:
        # A status OBS will not give is not evidence of anything -- same
        # bargain as _refuse_if_recording.
        return None
    if not status.active:
        return None

    running_for = _recording_for(status)
    if analyze_spin_service.owns_recording():
        return (
            f"OBS is recording for a spin analysis, {running_for} in, so this "
            "run has nowhere to record until that spin finishes -- OBS records "
            "one output at a time. It has been left alone. Its screenshots are "
            "unaffected."
        )

    try:
        await _stop_recording()
    except AppException as exc:
        logger.warning("Could not stop the recording found running: %s", exc.message)
        return (
            f"OBS was already recording, {running_for} in, and stopping it "
            f"failed ({exc.message}), so this run may have no clips -- OBS "
            "records one output at a time. Its screenshots are unaffected."
        )
    logger.warning(
        "Stopped an OBS recording that was already running (%s in) before "
        "starting a cyclic message run",
        running_for,
    )
    return (
        f"OBS was already recording when this run started, {running_for} in, "
        "and no run of this backend owned it -- a backend killed mid-run "
        "leaves one behind. It has been stopped so this run can record its own "
        "clips, and its video is wherever OBS was writing it."
    )


async def _stop_recording() -> ObsRecordStatus:
    """StopRecord, once OBS has actually started the recording -- otherwise a
    short clip gets stopped before it begins, refused as not running, and then
    starts with nothing left to stop it (see ``_START_WAIT_SECONDS``). Past the
    wait the stop is sent anyway and its refusal becomes the clip's error.
    """
    deadline = time.monotonic() + _START_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            if (await obs_service.record_status()).active:
                break
        except AppException:
            break
        await asyncio.sleep(_START_POLL_SECONDS)
    return await obs_service.stop_recording()


async def _file_clip(
    run: _ActiveRun, clip: _Clip, source: str | None
) -> tuple[str | None, str | None]:
    """Move a finished recording beside the run's screenshots, under a name
    that says which sequence it is. Returns ``(file name, error)``; a move
    that keeps failing leaves the video where OBS put it rather than losing it.
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
            _move(origin, target)
        except OSError as exc:
            if attempt < _MOVE_ATTEMPTS:
                await asyncio.sleep(_MOVE_RETRY_SECONDS)
                continue
            if origin.parent == run.directory:
                # Already beside the screenshots, just under OBS's own
                # timestamp -- filed as-is (recovery waits for OBS to release
                # it anyway) rather than delaying the next window, which is
                # often already on screen.
                logger.info("OBS still holds %s; filing it under its own name", origin)
                return origin.name, None
            logger.warning("Could not move %s beside its screenshots: %s", origin, exc)
            return None, (
                f"The video was recorded to {source} but could not be moved "
                f"beside the screenshots: {exc}"
            )
        return target.name, None


def _move(origin: Path, target: Path) -> None:
    """A rename, deliberately not ``shutil.move``: that falls back to copying
    when rename fails, which on Windows happens exactly while OBS still holds
    the file -- copying a still-writing recording left a truncated video and
    an undeletable original. Cross-volume copies still happen, but only once
    OBS has released the file.
    """
    try:
        origin.replace(target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        if not _released(origin):
            raise
        shutil.move(str(origin), str(target))


def _released(path: Path) -> bool:
    """Whether nothing still has ``path`` open for writing. Probed by renaming
    the file onto itself: Windows refuses that (WinError 32) while OBS's muxer
    holds the handle, and allows it once released.
    """
    try:
        path.replace(path)
    except PermissionError:
        return False
    except OSError:
        # Gone or unreadable: not "still being written", and the reader that
        # comes next reports it properly.
        return True
    return True


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
    # Both lines run before the first await -- load-bearing. Clearing `clip`
    # stops the watcher and the guard both closing it; handing off to a task in
    # the same breath stops a cancellation anywhere below from leaving OBS
    # recording with nothing on record. An await between the two would reopen
    # that gap.
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
        result = await _stop_recording()
    except AppException as exc:
        video = CyclicRunVideo(
            kind=clip.kind,
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
            kind=clip.kind,
            cycle=clip.cycle,
            started_at=clip.started_at,
            stopped_at=stopped_at,
            closed_by=closed_by,
            error=error,
        )

    run.videos.append(video)
    if video.file_name and settings.CYCLIC_MESSAGES_RECOVER_FROM_CLIP:
        # Queued here, read back later -- never inline, since the caller is
        # often about to open the very next window. See :class:`_Recovery`.
        run.pending_recovery.append(_Recovery(video=video))
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
    """Close a clip whose closing line never arrives -- without this it would
    run until the next win or tracking stops, the whole-run recording this
    feature exists to avoid.
    """
    await asyncio.sleep(max(0.1, settings.CYCLIC_MESSAGES_VIDEO_MAX_SECONDS))
    # Cleared before closing: a task cancelling itself never returns.
    run.clip_guard = None
    # On the run, not just the clip: a truncated video looks complete to
    # whoever watches it, so this is the one place it gets flagged.
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
) -> Path | None:
    """Append one event to the run, screenshotting it unless it is a marker.
    Returns where the frame landed, or ``None`` if none was taken. ``at``
    overrides the log line's own timestamp for a sampled frame, since every
    frame of a pass shares one boundary line's timestamp otherwise.
    """
    # Reserved before the first await: see _ActiveRun.next_sequence.
    run.next_sequence += 1
    sequence = run.next_sequence
    at = at or detected.line.timestamp

    def _append(screenshot: str | None, capture_error: str | None) -> None:
        """Put this event on the record under the sequence already reserved.
        A closure so the cancellation path below builds the same event as the
        ordinary path, just without a picture.
        """
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
        run.index[sequence] = len(run.events) - 1
        if source is CyclicEventSource.SAMPLED:
            run.sampled_count += 1

    screenshot: str | None = None
    capture_error: str | None = None
    saved: Path | None = None

    if not detected.capture:
        # A loop boundary: structure, not a frame. Deliberately still recorded.
        pass
    elif sequence > settings.CYCLIC_MESSAGES_MAX_EVENTS:
        capture_error = (
            f"Event cap of {settings.CYCLIC_MESSAGES_MAX_EVENTS} reached; "
            "the event was recorded without a screenshot"
        )
    else:
        name = _screenshot_name(sequence, detected.event, at)
        try:
            if detected.delay_ms:
                # Lets the strip draw its new text before the shot. Inside the
                # guard so a stop landing here (as it often does) still
                # unwinds past a sequence already reserved.
                await asyncio.sleep(detected.delay_ms / 1000)
            result = await obs_service.take_screenshot(
                ScreenshotRequest(
                    # JPEG by default (CYCLIC_MESSAGES_SCREENSHOT_FORMAT): a
                    # PNG encode of a full canvas costs round-trip time the
                    # loop needs to stay inside a message's dwell.
                    image_format=settings.CYCLIC_MESSAGES_SCREENSHOT_FORMAT,
                    quality=settings.CYCLIC_MESSAGES_SCREENSHOT_QUALITY,
                    width=settings.CYCLIC_MESSAGES_SCREENSHOT_WIDTH,
                    file_name=name,
                    output_dir=run.output_dir,
                    # Never read back inline -- the frame is reopened from disk
                    # when its caption is read, so skipping the base64
                    # GetSourceScreenshot re-encode is most of the loop's
                    # budget against the 2s interval.
                    include_image_data=False,
                )
            )
        except AppException as exc:
            # One failed screenshot isn't a reason to end the run -- OBS
            # re-identifies on the next request even if its socket dropped.
            capture_error = exc.message
            _note(run, f"{detected.event}: {exc.message}")
            logger.warning("Screenshot failed for %s: %s", detected.event, exc.message)
        except asyncio.CancelledError:
            # A stop landed mid-frame. The sequence was reserved before either
            # await, so unwinding without appending would leave a numbered gap
            # with no event -- recorded instead, without its picture, saying why.
            _append(None, "The run stopped before this frame was taken")
            raise
        else:
            saved = Path(result.file_path) if result.file_path else None
            screenshot = saved.name if saved else None

    _append(screenshot, capture_error)

    if saved is not None:
        _queue_read(run, sequence=sequence, saved=saved)

    _touch(run)
    return saved


def _queue_read(run: _ActiveRun, *, sequence: int, saved: Path) -> None:
    """Keep one written frame for :func:`_read_pending`, including frames
    captured with no window open -- ``cyclic-game-over`` lands 400ms before
    ``cyclic-idle-strip-started`` opens one, and gating on ``pass_open`` there
    dropped that caption ("Game Over" at 97, measured) on the floor. Such a
    frame is read as the between-spins strip's regions, since
    :attr:`_ActiveRun.pass_regions` would otherwise still name whichever
    window ran last.
    """
    if not run.pass_open:
        # Captured between two windows, so it has no cycle of its own yet --
        # see :attr:`_ActiveRun.unfiled` and :func:`_adopt`.
        run.unfiled.append(sequence)
    run.pending_reads.append(
        _Pending(
            sequence=sequence,
            path=saved,
            regions=(
                run.pass_regions
                if run.pass_open
                else settings.cyclic_messages_idle_regions
            ),
        )
    )


def _adopt(run: _ActiveRun) -> None:
    """Move frames captured before this window opened into its cycle -- a
    frame belongs to the strip it pictures, not the cycle current when it was
    taken (the ~400ms-early ``cyclic-game-over`` frame shows the between-spins
    strip, but was filed under the outgoing cycle and dropped by
    :func:`live`'s per-cycle scoping). Called on every window open; in
    practice only the between-spins one ever has anything to adopt.
    """
    if not run.unfiled:
        return
    adopted = set(run.unfiled)
    run.unfiled = []
    for sequence in adopted:
        at = run.index.get(sequence)
        if at is None:
            continue
        run.events[at] = run.events[at].model_copy(update={"cycle": run.cycle})
    logger.info(
        "Cyclic message run %s: %d frame(s) taken before this window opened "
        "belong to cycle %d",
        run.run_id,
        len(adopted),
        run.cycle,
    )


# --- the amount, and the denomination that scales it ----------------------


def _read_denomination(path: Path) -> float | None:
    """The denomination the game last logged, read *backwards*: it only logs
    ``UpdatePayTable`` on a change, so a run against an already-running game
    would otherwise report cents until the player changed one.
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
    """What the strip is about to display, in the units it displays it in
    (the log states cents, the strip credits). Without a denomination the
    cents are reported as cents rather than guessing a rate.
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


# --- reading a pass's frames, once it has closed ---------------------------


def _attach(run: _ActiveRun, sequence: int, readings: list[CyclicFrameReading]) -> None:
    """Hang a frame's readings -- one per line of the strip -- on its event.

    By :attr:`_ActiveRun.index` rather than by position: the watcher and the
    sampler are both still appending while a pass is open, so nothing about
    ``events`` is indexable by sequence.
    """
    at = run.index.get(sequence)
    if at is None:
        logger.warning("No event %d to hang a caption reading on", sequence)
        return
    run.events[at] = run.events[at].model_copy(update={"readings": readings})


def _loop_watch(run: _ActiveRun) -> cyclic_text.LoopWatch | None:
    """A watcher for the between-spins strip coming round, or None if the
    window should simply run to its deadline.

    Never fails the window: a game whose caption regions cannot be resolved
    still gets its frames, it just gets them until the deadline instead of
    until the lap closes.
    """
    if not settings.CYCLIC_MESSAGES_IDLE_STOP_AFTER_LOOP:
        return None
    # Locally, for the reason the TYPE_CHECKING block above gives.
    from app.services import cyclic_text

    try:
        # The lap bands only (CYCLIC_MESSAGES_IDLE_LOOP_REGIONS) -- band 1 is
        # still captured, it just doesn't decide the lap: its cycle can be
        # forty messages long against band 2's three.
        regions = cyclic_text.strip_rois(
            run.game, settings.cyclic_messages_idle_loop_regions
        )
    except AppException as exc:
        _note(run, f"Not watching for the strip's loop: {exc.message}")
        return None
    return cyclic_text.LoopWatch(regions=regions)


async def _start_recovery(run: _ActiveRun) -> None:
    """Ask for the queued clips to be read back, in the background -- fire
    and forget, deliberately not awaited (see :class:`_Recovery`), and a
    no-op if a drain is already running: one queue, one worker.
    """
    if not settings.CYCLIC_MESSAGES_RECOVER_FROM_CLIP:
        run.pending_recovery.clear()
        return
    if not run.pending_recovery or run.finishing:
        return
    task = run.recovering
    if task is not None and not task.done():
        return
    run.stop_recovery = False
    run.recovering = asyncio.create_task(
        _drain_recovery(run), name=f"cyclic-messages-recovery-{run.run_id}"
    )


async def _drain_recovery(run: _ActiveRun) -> None:
    """Work through the queued clips while nothing is being captured. Safe
    alongside the watcher because a window opening sets
    :attr:`_ActiveRun.stop_recovery` and this returns before its next frame --
    at most one frame overlaps a capture, against the fifteen seconds recovering
    inline once cost the window after a taken win. Never raises: a clip that
    won't decode costs only the messages it held.
    """
    try:
        while run.pending_recovery:
            if _recovery_stopped(run) or _finishing(run) or _capturing(run):
                return
            item = run.pending_recovery[0]
            if _still_writing(run, item):
                # Checked again next time round, so a window opening while this
                # waits still stops the drain at once.
                await asyncio.sleep(_RELEASE_POLL_SECONDS)
                continue
            if not await _recover(run, item):
                # A window opened part-way through. Left on the queue with its
                # progress, so the rest of it is picked up rather than re-read.
                return
            run.pending_recovery.pop(0)
    except Exception:
        logger.exception(
            "Cyclic message run %s could not recover a clip's messages", run.run_id
        )


def _still_writing(run: _ActiveRun, item: _Recovery) -> bool:
    """Whether OBS is still writing the clip -- filed at StopRecord, not at
    finish, so reading too early silently loses the tail. Read anyway past
    `_RELEASE_WAIT_SECONDS`, since a handle that never closes is not going to.
    """
    if item.video.file_name is None:
        return False
    if time.monotonic() - item.queued_at > _RELEASE_WAIT_SECONDS:
        return False
    return not _released(run.directory / item.video.file_name)


async def _recover(run: _ActiveRun, item: _Recovery) -> bool:
    """Fill one filed clip's messages in from the video itself, since live
    stills miss captions on both strips (~6s per screenshot vs captions that
    change every ~1-2s -- measured two of three caught on a real between-spins
    pass). Decodes the clip and reads each frame where the caption *changed*.

    Returns whether the clip is finished with; ``False`` means a window opened
    part-way through and :attr:`_Recovery.written` marks where it got to.
    Never raises.
    """
    clip = item.video
    if clip.file_name is None or clip.started_at is None:
        return True

    # Locally, for the reason the TYPE_CHECKING block at the top gives.
    from app.services import cyclic_text

    # Bands depend on which strip this clip is of -- a win uses one line, the
    # between-spins strip two -- so reading the wrong kind's regions crops a
    # band that strip leaves empty.
    regions = cyclic_text.clip_regions(clip.kind)
    if not regions:
        return True
    try:
        reader = await cyclic_text.live_reader(run.game)
    except AppException as exc:
        _note(run, f"Could not recover this clip's messages: {exc.message}")
        return True

    path = run.directory / clip.file_name
    interval = settings.CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS
    # Only frames where the caption changed are OCR'd -- at ~1.5s a pass, that
    # is the difference between reading an 80s win presentation in ten seconds
    # or still running it when the next spin needs the watcher.
    changes = cyclic_text.CaptionChanges(regions=reader.regions_in(regions))
    frames = cyclic_text.clip_frames(path, interval)
    # Changed frames walked so far -- the counterpart of `_Recovery.written`.
    seen = 0
    recovered = 0
    finished = False
    try:
        while True:
            if _recovery_stopped(run) or _finishing(run) or _capturing(run):
                break
            try:
                # One frame at a time off the decoder -- a 90s clip's frames
                # held all at once would be hundreds of megabytes.
                frame = await asyncio.to_thread(_next_clip_frame, frames)
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                _note(run, f"Could not read this clip back: {exc}")
                logger.warning(
                    "Could not decode %s to recover its messages: %s", path, exc
                )
                finished = True
                break
            if frame is None:
                finished = True
                break
            if not await asyncio.to_thread(changes.changed, frame.image):
                continue
            seen += 1
            if seen <= item.written:
                # Already recorded from an earlier attempt. Re-walked rather
                # than skipped in the decoder, since `CaptionChanges` compares
                # consecutive frames and a jump would compare across the gap.
                continue
            at = clip.started_at + timedelta(seconds=frame.at_seconds)
            run.next_sequence += 1
            sequence = run.next_sequence
            name = _screenshot_name(sequence, _RECOVERED, at)
            target = (
                run.directory / f"{name}.{settings.CYCLIC_MESSAGES_SCREENSHOT_FORMAT}"
            )
            try:
                readings = await asyncio.to_thread(
                    _recover_frame, reader, frame, regions, target
                )
            except Exception as exc:  # noqa: BLE001 - one frame is not the clip
                logger.warning("Could not recover frame %d: %s", frame.index, exc)
                item.written = seen
                continue
            run.events.append(
                CyclicEvent(
                    sequence=sequence,
                    event=_RECOVERED,
                    at=at,
                    summary=(
                        f"Strip {frame.at_seconds:.1f}s into "
                        f"{_STRIP_NAMES.get(clip.kind, clip.kind)}, recovered "
                        "from the clip"
                    ),
                    cycle=clip.cycle or max(1, run.cycle),
                    fields={
                        "at_seconds": f"{frame.at_seconds:.1f}",
                        "strip": clip.kind,
                    },
                    captured=True,
                    screenshot=target.name,
                    source=CyclicEventSource.SAMPLED,
                    log_line=f"recovered from {clip.file_name}",
                    readings=readings,
                )
            )
            run.index[sequence] = len(run.events) - 1
            run.read_count += 1
            run.recovered_count += 1
            item.written = seen
            recovered += 1
            _touch(run)
    finally:
        # Releases the file handle now rather than waiting for GC -- abandoning
        # a clip part-way through is the ordinary case here, not the exception.
        frames.close()

    if recovered:
        logger.info(
            "Cyclic message run %s recovered %d frame(s) of sequence %s from %s%s",
            run.run_id,
            recovered,
            clip.cycle,
            clip.file_name,
            "" if finished else " so far",
        )
        _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))
    return finished


def _next_clip_frame(
    frames: Generator[cyclic_text.ClipFrame, None, None],
) -> cyclic_text.ClipFrame | None:
    """One frame off the decoder, or ``None`` at the end of the clip. Blocking."""
    return next(frames, None)


def _recover_frame(
    reader: cyclic_text.LiveReader,
    frame: cyclic_text.ClipFrame,
    regions: tuple[str, ...],
    target: Path,
) -> list[CyclicFrameReading]:
    """Write one decoded clip frame out and read its bands. Blocking."""
    # Local, and not optional: omitting it raised NameError per frame after
    # the picture was already written, which `_recover` logs and carries past
    # -- so frames reached disk but never the record.
    from app.services import cyclic_text

    frame.image.save(target)
    return [
        CyclicFrameReading(
            text=line.text,
            repaired=line.repaired,
            confidence=line.confidence,
            reliable=line.reliable,
            engine=cyclic_text.PADDLE,
            region=line.region,
            key=cyclic_text.caption_key(line.repaired),
            crop=line.crop_name,
            read_ms=line.read_ms,
        )
        for line in reader.read_image(frame.image, regions, beside=target)
    ]


# How long a stop waits for the drain to finish the frame it is on before
# cancelling it. Sized above one decode plus one OCR pass (~1.5s for the
# recogniser, more on a cold model), so the ordinary case always returns
# through the drain's own code and only a wedged decode reaches the cancel.
_RECOVERY_STOP_SECONDS = 30.0


def _recovery_stopped(run: _ActiveRun) -> bool:
    """Read :attr:`_ActiveRun.stop_recovery` through a call, for the reason
    :func:`_sampling_stopped` gives."""
    return run.stop_recovery


def _capturing(run: _ActiveRun) -> bool:
    """Read :attr:`_ActiveRun.pass_open` through a call rather than inline,
    since the watcher sets it from outside this coroutine mid-``await``."""
    return run.pass_open


def _ask_recovery_to_stop(run: _ActiveRun) -> None:
    """Ask the drain to give way without waiting -- called from a window
    opening, which must not block on the remainder of a decode/OCR pass.
    Nothing is lost: the drain still finishes that frame and its queue
    survives for the next quiet moment.
    """
    run.stop_recovery = True


async def _stop_recovery(run: _ActiveRun) -> None:
    """Ask the drain to give way and wait for it, with a cancel behind it --
    the waiting half, for sealing a run and for tests (an unawaited task
    outliving its run fails the suite under ``filterwarnings = error``).
    Asked rather than cancelled outright for the same reason as
    :func:`_stop_sampler`: cancelling mid-frame would spend a sequence number
    with no event to carry it.
    """
    task, run.recovering = run.recovering, None
    if task is None or task.done():
        return
    run.stop_recovery = True
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=_RECOVERY_STOP_SECONDS,
        )


async def _read_pending(run: _ActiveRun) -> None:
    """Read every frame from the pass that just closed, in order, one at a
    time, called plainly (never spawned) so nothing competes with capturing
    the next pass. One at a time because Paddle's recogniser isn't documented
    thread-safe; a real backlog wants a quicker model
    (``CYCLIC_MESSAGES_LIVE_PADDLE_MODEL``), not parallelising this. Failing
    to build the engine drops this pass's readings, not the run.
    """
    if not run.pending_reads:
        return
    if not settings.CYCLIC_MESSAGES_LIVE_READ:
        run.pending_reads.clear()
        return

    # Imported here, not at module scope: `cyclic_text` imports this module
    # (to find a run's clip), so the pair would cycle at import time otherwise.
    from app.services import cyclic_text

    try:
        reader = await cyclic_text.live_reader(run.game)
    except AppException as exc:
        _note(run, f"Captions are not being read: {exc.message}")
        logger.warning(
            "Cyclic message run %s will not read captions: %s", run.run_id, exc.message
        )
        return

    run.reading_now = True
    try:
        # Taken off the front one at a time rather than snapshotted, so a read
        # cut short by the run stopping leaves the rest for the next caller.
        while run.pending_reads:
            item = run.pending_reads.pop(0)
            try:
                read = await asyncio.to_thread(reader.read, item.path, item.regions)
            except Exception as exc:  # noqa: BLE001 - recorded on the frame below
                message = f"{type(exc).__name__}: {exc}"
                _attach(
                    run,
                    item.sequence,
                    # Recorded, not dropped: a caption that failed to read is a
                    # different fact from a strip that said nothing. One entry
                    # per line, so the shape matches a successful read.
                    [
                        CyclicFrameReading(
                            text="",
                            confidence=0.0,
                            reliable=False,
                            engine=cyclic_text.PADDLE,
                            region=name,
                            error=message,
                        )
                        for name in item.regions
                    ],
                )
                _note(run, f"caption reading: {message}")
                logger.warning("Could not read the caption on %s: %s", item.path, exc)
            else:
                _attach(
                    run,
                    item.sequence,
                    [
                        CyclicFrameReading(
                            text=line.text,
                            repaired=line.repaired,
                            confidence=line.confidence,
                            reliable=line.reliable,
                            engine=cyclic_text.PADDLE,
                            region=line.region,
                            # The clip reader's own rule, not a second one: the
                            # stills and the video are two readings of one pass
                            # and must not disagree about what the strip said.
                            key=cyclic_text.caption_key(line.repaired),
                            crop=line.crop_name,
                            read_ms=line.read_ms,
                        )
                        for line in read
                    ],
                )
            run.read_count += 1
            _touch(run)
    finally:
        run.reading_now = False
        # Unconditional rather than throttled: a caller polling right after a
        # pass closes should see every one of its readings, not wait out
        # whatever was left of _touch's own interval.
        _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))


# --- sampling the silent window -------------------------------------------


def _finishing(run: _ActiveRun) -> bool:
    """Read :attr:`_ActiveRun.finishing` through a call: checked again after
    an ``await``, mypy would otherwise narrow the second read away even though
    the run can be sealed from outside this coroutine in between."""
    return run.finishing


def _sampling_stopped(run: _ActiveRun) -> bool:
    """Read :attr:`_ActiveRun.stop_sampling` through a call, not inline --
    same mypy-narrowing reason as :func:`_finishing`: :func:`_stop_sampler`
    sets it from outside this coroutine while it is suspended at an ``await``.
    """
    return run.stop_sampling


async def _sampler(run: _ActiveRun) -> None:
    """Screenshot whatever the strip is showing, on a timer, until it stops.
    A win presentation runs ``cyclic-game-pays`` to
    ``cyclic-line-pays-cycle-finished``; a losing spin's idle strip runs
    ``cyclic-no-pay`` to ``cyclic-idle-strip-ended``; :func:`_stop_sampler`
    ends either the moment the log says so.

    Every knob (:attr:`_ActiveRun.pass_deadline`/``pass_watch``/
    ``pass_closing_expected``/etc.) lives on the run and is re-read each frame
    rather than passed in, because a win taken before its line messages loop
    once carries the same loop on into the idle strip with different knobs and
    no gap in the frames (:func:`_retune`). The two windows' knobs differ for
    good reason: their deadlines fail differently (an overrun win pass is
    still worth following; an overrun idle strip is just an unplayed machine);
    the idle strip's early-stop watch is decided from the picture, costing
    milliseconds against a 1.5s OCR pass, while a win pass has none (the log
    says when it's done); and only the win pass's closing line is guaranteed
    -- an idle strip ends only when the player spins again (measured 11s of
    total log silence on FortuneOx between a loss and the next spin), so its
    deadline expiring is normal, not an error.

    Frames are written here, never read (:func:`_read_pending` does that once
    the window closes), and their elapsed time is wall clock, not
    ``index * interval`` -- measured from when the window opened, unreset by a
    retune, since a merged window is one stretch of strip.
    """
    begun = time.monotonic()
    run.sample_frames = 0
    run.sample_rate = 0.0
    index = 0
    # The deadline is the *only* way out that falls through to the warning
    # below -- a cooperative stop returns from inside the loop instead, so
    # putting `stop_sampling` in the while condition would misreport every
    # ordinary stop as a pass that never finished.
    while time.monotonic() < run.pass_deadline:
        if _sampling_stopped(run):
            return
        await asyncio.sleep(max(0.0, run.pass_interval))
        if _sampling_stopped(run):
            # Checked again post-sleep, where a stop nearly always arrives.
            return
        opener = run.pass_opener
        if opener is None:  # pragma: no cover - every window sets one
            return
        index += 1
        elapsed = time.monotonic() - begun
        run.sample_frames = index
        run.sample_rate = index / elapsed if elapsed > 0 else 0.0
        run.position += 1
        saved = await _record(
            run,
            dataclasses.replace(
                opener,
                event=run.pass_event,
                summary=f"Strip on screen {elapsed:.1f}s into the presentation",
                fields={
                    "sample_index": str(index),
                    "elapsed_seconds": f"{elapsed:.1f}",
                },
                # The pass is already running; a frame is wanted now, not later.
                delay_ms=0,
                # Forced on, not inherited from `opener`: the line that opens a
                # window is often a no-picture marker (`cyclic-no-pay` etc), and
                # inheriting it once made every between-spins frame a marker
                # with no screenshot.
                capture=True,
            ),
            position=run.position,
            source=CyclicEventSource.SAMPLED,
            at=datetime.now(),
        )
        # Off the loop's own thread: opening a JPEG is blocking work best kept
        # off the event loop the OBS round trip also waits on.
        #
        # Read through the run: a retuned window gains a watch it did not open
        # with, which a local variable bound before the loop would never see.
        watch = run.pass_watch
        if (
            watch is not None
            and saved is not None
            and await asyncio.to_thread(watch.saw, saved)
        ):
            logger.info(
                "Cyclic message run %s captured %d caption(s) of the strip "
                "and stopped: it has come round",
                run.run_id,
                len(watch.seen),
            )
            return
    if not run.pass_closing_expected:
        # The ordinary way this window ends -- no closing line was ever coming.
        # Logged, not noted, or almost every losing spin would flag an error.
        logger.info(
            "Cyclic message run %s captured the between-spins strip until its "
            "deadline and stopped; it repeats until the next spin",
            run.run_id,
        )
        return

    limit = settings.CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS
    _note(
        run,
        f"capture stopped after {limit:g}s without the line-message pass "
        "reporting that it finished; raise CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS "
        "if a win presentation legitimately runs longer",
    )
    logger.warning(
        "Cyclic message run %s stopped capturing after %.1fs without the "
        "line-message pass reporting that it finished",
        run.run_id,
        limit,
    )


def _tune(
    run: _ActiveRun,
    opener: game_log.DetectedEvent,
    *,
    interval: float,
    max_seconds: float,
    event: str,
    closing_line_expected: bool,
    loop_watch: cyclic_text.LoopWatch | None,
) -> None:
    """Point the capture loop's knobs at one window. See :func:`_sampler` for
    why they are on the run rather than parameters of it."""
    run.pass_opener = opener
    run.pass_interval = max(0.0, interval)
    run.pass_event = event
    run.pass_deadline = time.monotonic() + max(run.pass_interval, max_seconds)
    run.pass_closing_expected = closing_line_expected
    run.pass_watch = loop_watch


def _retune(
    run: _ActiveRun,
    opener: game_log.DetectedEvent,
    *,
    interval: float,
    max_seconds: float,
    event: str,
    closing_line_expected: bool,
    loop_watch: cyclic_text.LoopWatch | None,
) -> None:
    """Change what the open window is capturing, without stopping it -- for
    the win taken early, where the strip runs straight on into the
    between-spins messages and wants one unbroken run of frames. Nothing here
    touches :attr:`_ActiveRun.sampler`; the loop picks up new values on its
    next frame. Everything a window decides is reset, deadline included,
    except the achieved rate and elapsed clock -- one window, one set of
    figures.
    """
    _tune(
        run,
        opener,
        interval=interval,
        max_seconds=max_seconds,
        event=event,
        closing_line_expected=closing_line_expected,
        loop_watch=loop_watch,
    )
    logger.info(
        "Cyclic message run %s is carrying its open window on into %s with no "
        "break in the frames",
        run.run_id,
        _STRIP_NAMES.get(run.pass_kind, run.pass_kind),
    )


async def _start_sampler(
    run: _ActiveRun,
    opener: game_log.DetectedEvent,
    *,
    interval: float,
    max_seconds: float,
    event: str,
    closing_line_expected: bool,
    loop_watch: cyclic_text.LoopWatch | None = None,
) -> None:
    """Begin capturing whatever the strip is about to show, replacing any window
    still running -- two spins close together should not leave two samplers on
    one run."""
    if not settings.CYCLIC_MESSAGES_SAMPLE_LINE_PAYS or run.finishing:
        return
    # Asked, not waited for: a recovery is the only other thing that can be
    # running when a window opens, and it gives way after its current frame
    # without this standing around for even that.
    _ask_recovery_to_stop(run)
    await _stop_sampler(run)
    run.stop_sampling = False
    _tune(
        run,
        opener,
        interval=interval,
        max_seconds=max_seconds,
        event=event,
        closing_line_expected=closing_line_expected,
        loop_watch=loop_watch,
    )
    run.sampler = asyncio.create_task(
        _window(run), name=f"cyclic-messages-sampler-{run.run_id}"
    )


async def _window(run: _ActiveRun) -> None:
    """Capture a window, and close it if it ends on its own terms (the strip
    looping round, or the deadline) rather than by a log line -- a log-line
    stop is closed by the handler that asked via :func:`_stop_sampler`.
    Otherwise nothing is waiting to close it, so it would sit open with its
    frames unread until the next spin happened to do so.
    """
    await _sampler(run)
    if run.stop_sampling:
        # Asked to stop: the caller owns the close, likely awaiting this very
        # task inside `_stop_sampler` right now.
        return
    run.pass_open = False
    run.pass_kind = ""
    # Before the reading, which can take a minute: leaving OBS recording
    # through it would put that minute in the file.
    await _close_clip(run, closed_by=_STRIP_LOOPED)
    await _read_pending(run)
    # The quietest moment a run has -- the strip has shown everything and the
    # next spin is at human speed -- so read this clip back now.
    await _start_recovery(run)


async def _stop_sampler(run: _ActiveRun) -> None:
    """End the capture loop cooperatively, with a cancel behind it. Asked, not
    cancelled directly: the loop is nearly always inside :func:`_record` with
    a sequence number already taken, and cancelling there would spend it with
    no event to carry it. The cancel behind it is for a screenshot OBS never
    answers, so a Stop press can't hang indefinitely.
    """
    task, run.sampler = run.sampler, None
    if task is None or task.done():
        return
    run.stop_sampling = True
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=_SAMPLER_STOP_SECONDS,
        )


def _live_on(run: _ActiveRun, cycle: int, *, kind: str, continues: int | None) -> None:
    """Point the live view at a window, or append to what it already shows.
    ``continues`` names the win sequence this window is the rest of; given
    one, the card keeps that win's frames rather than resetting, since a spin
    can be two windows (the win, then its between-spins strip) that a tester
    reads as one and would otherwise see emptied mid-spin.
    """
    if continues is not None and continues in run.live_cycles:
        run.live_cycles.append(cycle)
        # Names both halves; the frames themselves still carry their own
        # strip's bands (see :attr:`_Pending.regions`), so this is a caption
        # for the view only, never an input to a reading.
        run.live_kind = WIN_THEN_IDLE
        return
    run.live_cycles = [cycle]
    run.live_kind = kind


async def _carry_on(run: _ActiveRun, detected: game_log.DetectedEvent) -> None:
    """Run the open win window straight on into the between-spins strip, for
    a win taken before its line messages loop once -- on screen there is no
    break, so the same sampler/clip/sequence carries on rather than being
    stopped and reopened (which used to cost the line messages' last frames,
    an OBS round-trip gap, and the live view's win frames).

    Changes underneath the running window: the bands (stamped for both strips
    from here on, per ``CYCLIC_MESSAGES_IDLE_REGIONS``); the clip (becomes
    :data:`WIN_THEN_IDLE`, one file covering both strips); and the interval
    and sampled-event name (the between-spins strip's). The loop watch and
    deadline do *not* switch yet -- band 2 laps in ~5s while band 1 may still
    be walking the win's lines, so watching early closed one real window at
    line 9 of 40 -- they wait for ``cyclic-line-pays-cycle-finished``
    (:func:`_lines_finished`), and :attr:`_ActiveRun.pass_kind` becoming
    :data:`WIN_THEN_IDLE` is what keeps that same line from closing the
    window it names when it arrives late.
    """
    run.pass_kind = WIN_THEN_IDLE
    run.pass_regions = settings.cyclic_messages_idle_regions
    # The win's own sequence carries on, so nothing moves on the card and the
    # frames from here simply keep arriving after the ones already on it.
    run.win_cycle = None
    run.live_kind = WIN_THEN_IDLE
    if run.clip is not None:
        # Mutated, not reopened: restarting OBS here is the recording gap
        # this function exists to avoid.
        run.clip.kind = WIN_THEN_IDLE
    run.lines_running = True
    _retune(
        run,
        detected,
        interval=settings.CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS,
        # The win's deadline, not the strip's: the line messages are what this
        # window is still waiting out, and they are sized by how many lines
        # paid rather than by the between-spins strip's 90s.
        max_seconds=settings.CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS,
        event=_IDLE_MESSAGE_SHOWN,
        loop_watch=None,
        # The game *does* write the end of the line messages, so a window that
        # never sees it is worth saying so about -- exactly as for a win pass.
        closing_line_expected=True,
    )
    await _record(run, detected, position=None)


def _lines_finished(run: _ActiveRun, detected: game_log.DetectedEvent) -> None:
    """Hand a carried window over to the between-spins strip's own terms: it
    now ends on its first lap or its deadline, like that strip's own window.
    The lap is counted from a fresh watch, since band 2 was not being watched
    before this line.
    """
    run.lines_running = False
    _retune(
        run,
        detected,
        interval=settings.CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS,
        max_seconds=settings.CYCLIC_MESSAGES_IDLE_MAX_SECONDS,
        event=_IDLE_MESSAGE_SHOWN,
        loop_watch=_loop_watch(run),
        # Nothing writes the end of a between-spins strip; it stops when the
        # player spins again. The deadline is how it ordinarily ends.
        closing_line_expected=False,
    )


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
    """Capture one appended line, if a cyclic rule claims it. The idle strip
    holds a start until a message proves the loop real; the win strip opens on
    the amount and the sampler's lifetime is exactly the log-bracketed window.
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

        if detected.event == _NO_PAY:
            # This spin paid nothing, so there is no banner and no line
            # messages. Counted and recorded as a marker, and nothing more --
            # the strip it leaves behind is the *between-spins* one, which
            # opens a couple of hundred milliseconds later on
            # `cyclic-idle-strip-started` below. That line is where the window
            # belongs because a won spin reaches it too, once its win has been
            # taken, and the two show the same strip.
            run.no_pay_count += 1
            run.no_pay_since_win += 1
            # This spin has no win to continue, so the strip it is about to
            # show is a window of its own rather than the second half of one.
            run.win_cycle = None
            if run.cycle == 0:
                run.cycle = 1
            await _record(run, detected, position=None)
            return

        if detected.event == _IDLE_STRIP_STARTED:
            # Both ways a spin can end reach this line -- a loss within a few
            # hundred ms of its result, a win once it has been taken -- so it
            # covers the between-spins strip either way. Nothing logs its
            # messages either, so they're sampled from two bands
            # (CYCLIC_MESSAGES_IDLE_REGIONS). Three cases, in order:
            # 1. A win still capturing (taken before its lines looped once):
            #    the strip doesn't stop, so neither does the window --
            #    :func:`_carry_on`.
            # 2. A win already finished: a window of its own, but the same
            #    spin, so the live view keeps the win's frames and appends.
            # 3. Anything else (a loss, or a strip already round): a spin of
            #    its own, live view starts again.
            run.pending_start = None
            if run.pass_open and run.pass_kind == WIN_VIDEO:
                await _carry_on(run, detected)
                return

            # The win this strip belongs to, if any -- read before the cycle
            # moves, cleared either way (a win's between-spins strip runs once).
            continues, run.win_cycle = run.win_cycle, None
            run.cycle += 1
            run.position = 0
            # Appended, not replaced, for a spin's second half: swapping the
            # card for an empty one at Take Win made the win's own frames look
            # thrown away, when they were just filed under a sequence the view
            # had stopped showing.
            _live_on(run, run.cycle, kind=IDLE_VIDEO, continues=continues)
            run.pass_regions = settings.cyclic_messages_idle_regions
            run.pass_kind = IDLE_VIDEO
            run.pass_open = True
            # After the cycle is settled and before the first frame: the
            # between-spins strip's first message was captured by the log line
            # before this one. See :func:`_adopt`.
            _adopt(run)
            # Before the first frame, so the clip covers the strip from its
            # first message rather than joining it part-way.
            await _open_clip(run, kind=IDLE_VIDEO, replaces=_IDLE_STRIP_STARTED)
            await _record(run, detected, position=None)
            await _start_sampler(
                run,
                detected,
                interval=settings.CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS,
                max_seconds=settings.CYCLIC_MESSAGES_IDLE_MAX_SECONDS,
                event=_IDLE_MESSAGE_SHOWN,
                loop_watch=_loop_watch(run),
                # Nothing will write the end of this one: it stops when the
                # player spins again, which may be minutes away or never. The
                # deadline is how it ordinarily ends, not a fault.
                closing_line_expected=False,
            )
            return

        if detected.event == _IDLE_STRIP_ENDED:
            # The player bet or spun again, ending the between-spins strip.
            # Only closes that strip's own window -- this line fires whenever
            # the game leaves idle, whether or not a window is open.
            if not run.pass_open or run.pass_kind not in (
                IDLE_VIDEO,
                WIN_THEN_IDLE,
            ):
                return
            await _stop_sampler(run)
            run.pass_open = False
            run.pass_kind = ""
            run.win_cycle = None
            run.cycle_count += 1
            await _record(run, detected, position=None)
            await _close_clip(run, closed_by=_IDLE_STRIP_ENDED)
            await _read_pending(run)
            # Every clip filed since the last quiet moment (this window's, and
            # the win presentation before it) is read back in the background.
            await _start_recovery(run)
            return

        if detected.event == _GAME_PAYS:
            # A win presentation is its own cyclic sequence, opening here on
            # the line that says what the strip is about to display.
            run.pending_start = None
            run.no_pay_since_win = 0
            run.cycle += 1
            run.position = 1
            # Follows this sequence until the next win opens one, rather than
            # following `cycle` into the attract loops after it.
            _live_on(run, run.cycle, kind=WIN_VIDEO, continues=None)
            # The between-spins strip that follows this win, whenever taken,
            # is the rest of this same spin -- see :attr:`_ActiveRun.win_cycle`.
            run.win_cycle = run.cycle
            run.pass_regions = settings.cyclic_messages_text_regions
            run.pass_kind = WIN_VIDEO
            run.pass_open = True
            _adopt(run)
            # Recording starts before the frame is taken, so the GAME PAYS
            # banner this event screenshots is inside the video as well.
            await _open_clip(run)
            # This frame first, then the loop: it's the pass's only frame with
            # a log line behind it, and if the run is stopped while a frame
            # awaits its screenshot that event is lost -- worth losing a
            # sampled frame, not this one. Costs a 500ms settle plus one round
            # trip, inside the 1.5-7s a screenshot takes anyway.
            await _record(run, _pays_event(run, detected), position=run.position)
            # Capture opens here, on the amount, not on the rack-up ending
            # below -- the banner and count-up are as much the presentation as
            # the line messages after them; waiting for `cyclic-win-presented`
            # skipped them.
            await _start_sampler(
                run,
                detected,
                interval=settings.CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS,
                max_seconds=settings.CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS,
                event=_LINE_PAYS_SHOWN,
                closing_line_expected=True,
            )
            return

        if detected.event == _WIN_PRESENTED:
            # The meter finished counting; line messages start now and nothing
            # more is logged until the pass ends. Capture is already running
            # (opened on the amount); this only marks the boundary --
            # restarting the sampler here would reset its elapsed clock.
            if run.cycle == 0:
                run.cycle = 1
            if not run.pass_open:
                # No window open, so `cyclic-game-pays` never arrived (missed
                # log line, or tracking began mid-presentation). This is the
                # first boundary there is, so the window opens on it instead.
                _live_on(run, run.cycle, kind=WIN_VIDEO, continues=None)
                run.win_cycle = run.cycle
                run.pass_regions = settings.cyclic_messages_text_regions
                run.pass_kind = WIN_VIDEO
                run.pass_open = True
                _adopt(run)
                await _start_sampler(
                    run,
                    detected,
                    interval=settings.CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS,
                    max_seconds=settings.CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS,
                    event=_LINE_PAYS_SHOWN,
                    closing_line_expected=True,
                )
            run.position += 1
            await _record(run, detected, position=run.position)
            return

        if detected.event == _LINE_PAYS_CYCLE_DONE:
            # Only closes a window that is still purely the win one. A win
            # taken early can carry this line to an already-carried
            # (WIN_THEN_IDLE) or separate between-spins window, where closing
            # "the current window" would tear down that strip seconds after it
            # opened. Recorded either way, since the line is a real moment on
            # the strip.
            if run.pass_kind != WIN_VIDEO:
                await _record(run, detected, position=None)
                if run.pass_kind == WIN_THEN_IDLE and run.lines_running:
                    # Not the end of the window, just of what was keeping it
                    # from watching for the strip's lap.
                    _lines_finished(run, detected)
                return
            # Stop first: a frame taken after this line belongs to the next
            # pass, not this one.
            await _stop_sampler(run)
            run.cycle_count += 1
            run.position += 1
            # `pass_open` outlives the sampler by one frame on purpose: this
            # event's own screenshot is the last line message.
            await _record(run, detected, position=run.position)
            run.pass_open = False
            run.pass_kind = ""
            # Closed after that frame, not before: the last line message
            # belongs in the video too.
            await _close_clip(run, closed_by=_LINE_PAYS_CYCLE_DONE)
            # Reading is last: capture and filing are both quick and matter to
            # whatever log line comes next; reading is neither.
            await _read_pending(run)
            await _start_recovery(run)
            return

        if detected.event == _RESULTS_CYCLE_STOPPED:
            # The strip stopped cycling results, cutting short any pass still
            # being captured or recorded.
            await _stop_sampler(run)
            run.pass_open = False
            run.pass_kind = ""
            await _close_clip(run, closed_by=_RESULTS_CYCLE_STOPPED)
            # This ended the spin's presentation; the next
            # `cyclic-idle-strip-started` is not the rest of it.
            run.win_cycle = None
            await _record(run, detected, position=None)
            run.position = 0
            await _read_pending(run)
            await _start_recovery(run)
            return

        # Anything else the rule set recognises ('cyclic-game-over', the
        # rack-up marker) is a message in the current sequence.
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

    # The watcher may have been cancelled mid-filing; that continues without
    # it, so wait here so the record this returns has it.
    await _await_closing(run)

    # Set both before and after the catch-up read below: before, so a pass
    # still sampling ends with the run; after, so replayed lines can't open a
    # new one.
    run.finishing = True
    await _stop_sampler(run)
    # Before the catch-up read: the drain is the only other thing that could
    # still be running, and the final read wants the machine to itself.
    await _stop_recovery(run)

    # Events logged between the last poll and the stop request still count.
    for raw in run.log.new_lines():
        await _handle(run, raw)

    await _stop_sampler(run)
    run.pass_open = False
    # A presentation still recording ends with the run -- a short video is
    # still the win, so the clip is filed rather than dropped.
    await _close_clip(run, closed_by=_RUN_STOPPED)

    # Whatever the last pass left uncaptured is read now, same as if the pass
    # had closed on its own -- capturing has already stopped either way.
    await _read_pending(run)

    # Said out loud rather than left to be noticed: a queued clip holds
    # messages the stills missed and a sealed run never reads it back on its
    # own. Deliberately not drained here -- a queue of long clips is minutes
    # of OCR, and Stop should stop.
    if run.pending_recovery:
        remaining = len(run.pending_recovery)
        _note(
            run,
            f"{remaining} clip(s) were not read back before the run stopped, so "
            "their strips are recorded only as the frames the stills caught. "
            "'Read the messages' on each clip below is the complete list.",
        )

    detail = _detail(run, status=status, stopped=datetime.now())
    _write_manifest(run, detail)
    logger.info(
        "Cyclic message run %s %s with %d messages over %d loops, %d clips and "
        "%d captions read",
        run.run_id,
        status.value,
        detail.message_count,
        detail.cycle_count,
        len(detail.videos),
        run.read_count,
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

        # Before the run exists: the only moment a stray recording can be
        # cleared without costing a window its first frames.
        stray = await _clear_stray_recording()

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
            # Read backwards now: the game logs denomination only on a change,
            # so an already-running game would otherwise report cents.
            denomination=_read_denomination(log_path),
        )

        # On the run, not just the log: whoever reads the run needs to know a
        # stray recording was found and stopped.
        if stray is not None:
            _note(run, f"video: {stray}")

        # Not recording yet: a clip is a win presentation, and there hasn't
        # been one yet. See the module docstring.
        _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))
        run.last_write = time.monotonic()
        run.task = asyncio.create_task(_watch(run), name=f"cyclic-messages-{run_id}")
        # Nothing else to start: PaddleOCR's model builds on the first pass
        # that actually closes (:func:`_read_pending`), then stays cached at
        # process level for every pass after.
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
        reading=run.reading_now,
        recovering=run.recovering is not None and not run.recovering.done(),
        recovery_pending=len(run.pending_recovery),
        recovered_count=run.recovered_count,
        no_pay_count=run.no_pay_count,
        queue_depth=len(run.pending_reads),
        read_count=run.read_count,
        sample_rate=run.sample_rate,
        recent_events=sorted(run.events, key=lambda event: event.sequence)[-recent:],
        errors=list(run.errors),
    )


def live() -> CyclicLiveView:
    """The win presentation being captured right now, frame by frame. Read
    off the live run, not its throttled manifest, so a frame appears as soon
    as OBS writes it. Scoped to :attr:`_ActiveRun.live_cycles` (see
    :func:`_live_on`), so a spin captured as two windows still shows as one.
    """
    run = _run
    if run is None:
        return CyclicLiveView(active=False)

    cycles = set(run.live_cycles)
    frames = sorted(
        (event for event in run.events if event.captured and event.cycle in cycles),
        key=lambda event: event.sequence,
    )
    return CyclicLiveView(
        active=True,
        run_id=run.run_id,
        game=run.game,
        cycle=run.live_cycles[0] if run.live_cycles else None,
        strip=run.live_kind or None,
        capturing=run.pass_open,
        reading=run.reading_now,
        recovering=run.recovering is not None and not run.recovering.done(),
        recovery_pending=len(run.pending_recovery),
        spins_without_pay=run.no_pay_since_win,
        queue_depth=len(run.pending_reads),
        read_count=run.read_count,
        sample_rate=run.sample_rate,
        sample_interval_seconds=run.pass_interval,
        frame_count=len(frames),
        # The newest, not the oldest: a pass longer than the cap is one whose
        # last frames are the ones still being read.
        frames=frames[-settings.CYCLIC_MESSAGES_LIVE_FRAMES :],
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
        run.pass_open = False
        await _stop_watching(run)
        await _stop_sampler(run)
        await _stop_recovery(run)
        await _stop_guard(run)
        await _await_closing(run)
    _lock = None
