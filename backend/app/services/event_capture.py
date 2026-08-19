"""Event Based Capture: follow the game's log, screenshot what happens.

One run at a time. ``start`` opens a directory under the OBS screenshot root,
begins following the active game's log from its current end, and leaves a task
watching it. Every line that a rule recognises *and marks worth capturing* gets
a screenshot and a row in that run's ``run.json``. ``stop`` ends the task and
finalises the manifest.

The reading itself is not here: :class:`app.utils.log_tail.LogFollower` owns the
cursor and the poll loop, and :mod:`app.utils.game_log` owns what a line means.
What is left in this module is the part that is actually about capture -- which
recognised events are worth a frame, and how a run is recorded.

Four decisions are worth knowing before changing anything here.

**A run is not every event the rules know.** :mod:`app.utils.game_log` lists
every visual event either game logs, which is more than a run should be: the
credit meter ticks after every win, the paytable swaps behind every
denomination change, the attract loop cycles for as long as nobody touches the
machine. Screenshotting all of it would bury the moments someone opened the run
to look at, so a rule carries ``capture`` and this module honours it. Turning
one on or off for a game is a line in its config rather than a change here.

**The manifest is written after every event, not at the end.** A run can be
minutes or hours long, and losing all of it to a crash at minute 58 would be
worse than the cost of rewriting a small JSON file a hundred times. The write is
atomic (temp file then replace), so a crash mid-write cannot corrupt it either.

**The watcher task is owned by the endpoints, not by the app lifespan.** The
comment at the top of :mod:`app.services.obs` explains why this module has no
background reconnect task, and the same reasoning applies: the test transport
never runs lifespan, and ``filterwarnings = error`` turns a task still pending at
teardown into a failure. So the task is created by :func:`start` and always
cancelled *and awaited* -- by :func:`stop`, by :func:`abort` at shutdown, and by
:func:`reset` in tests. There is no path that leaves it running.

**Debounce compares the game's timestamps, not the wall clock.** The follower
reads whatever was appended since it last looked, so a single read can hand it a
hundred lines at once and processing them takes no measurable time. Debouncing
on the wall clock would collapse them all into one event; debouncing on the
timestamps in the lines themselves gives the same answer whether the lines
arrived one at a time or in a burst.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    AppException,
    EventCaptureAlreadyRunningError,
    EventCaptureLogUnavailableError,
    EventCaptureNotRunningError,
    EventCaptureRunNotFoundError,
    GameConfigInvalidError,
)
from app.schemas.event_capture import (
    CapturedEvent,
    CaptureRunDetail,
    CaptureRunState,
    CaptureRunSummary,
    CaptureStatus,
)
from app.schemas.obs import ScreenshotRequest
from app.services import obs as obs_service
from app.utils import game_log
from app.utils.log_tail import LogFollower
from app.utils.paths import UnsafeNameError, resolve_subdirectory, resolve_within

logger = get_logger("event_capture")

MANIFEST_NAME = "run.json"

# Directory name per run, and the stamp inside each screenshot's filename.
_RUN_ID_FORMAT = "%Y-%m-%d_%H-%M-%S"
_FILE_TIME_FORMAT = "%H-%M-%S"


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

    rules: tuple[game_log.EventRule, ...]
    started_at: datetime
    events: list[CapturedEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    last_seen: dict[str, datetime] = field(default_factory=dict)
    last_fields: dict[str, Mapping[str, str]] = field(default_factory=dict)
    """Values last recorded per event, for rules that only fire on a change."""

    task: asyncio.Task[None] | None = None


_run: _ActiveRun | None = None
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Serialise start and stop.

    Built lazily for the same reason as the OBS service's: a lock created at
    import time binds to whichever loop imported the module, and each test runs
    its own.
    """
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
    """The game's log, checked to actually be there.

    Refusing up front is the point: a run that starts against a log which never
    appears looks identical to a quiet game, and the difference would only show
    up as an empty record at the end.
    """
    if config.log_path is None:
        raise EventCaptureLogUnavailableError(
            f"The game config for {name!r} does not declare a 'log' path to follow"
        )
    if not config.log_path.is_file():
        raise EventCaptureLogUnavailableError(
            f"The log for {name!r} does not exist yet: {config.log_path}. "
            "Start the game and try again."
        )
    return config.log_path


# --- the manifest ---------------------------------------------------------


def _capture_root() -> Path:
    """Directory holding one subdirectory per run."""
    return resolve_subdirectory(
        settings.obs_screenshot_dir, settings.EVENT_CAPTURE_DIR_NAME
    )


def _detail(
    run: _ActiveRun, *, status: CaptureRunState, stopped: datetime | None
) -> CaptureRunDetail:
    """Build the record that both the manifest and the API return."""
    return CaptureRunDetail(
        run_id=run.run_id,
        game=run.game,
        status=status,
        started_at=run.started_at,
        stopped_at=stopped,
        event_count=len(run.events),
        log_path=str(run.log.path),
        events=list(run.events),
        errors=list(run.errors),
    )


def _write_manifest(run: _ActiveRun, detail: CaptureRunDetail) -> None:
    """Persist the run record atomically.

    Never raises: losing the manifest is bad, but taking down a live capture
    because one write failed is worse, and the next event rewrites it anyway.
    """
    target = run.directory / MANIFEST_NAME
    temporary = target.with_name(f".{MANIFEST_NAME}.tmp")
    try:
        temporary.write_text(
            detail.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8"
        )
        temporary.replace(target)
    except OSError:
        logger.exception("Could not write the capture manifest at %s", target)
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _read_manifest(path: Path) -> CaptureRunDetail | None:
    """Read one ``run.json``, or ``None`` if it is unusable.

    A half-written or hand-edited manifest should drop out of the listing rather
    than break it.
    """
    try:
        document: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Skipping unreadable capture manifest at %s", path)
        return None
    try:
        return CaptureRunDetail.model_validate(document)
    except ValueError:
        logger.warning("Skipping malformed capture manifest at %s", path)
        return None


# --- capturing ------------------------------------------------------------


def _screenshot_name(sequence: int, event: str, at: datetime | None) -> str:
    """Filename that sorts by sequence and still says what it is.

    ``007_reels-stopped_14-32-14``. The sequence leads so a directory listing is
    in event order; the event name is there so the folder is readable without
    opening the manifest.
    """
    stamp = (at or datetime.now()).strftime(_FILE_TIME_FORMAT)
    return f"{sequence:03d}_{event}_{stamp}"


async def _capture(run: _ActiveRun, detected: game_log.DetectedEvent) -> None:
    """Screenshot one event and append it to the run."""
    sequence = len(run.events) + 1
    at = detected.line.timestamp

    if detected.delay_ms:
        # The log records the decision; the screen catches up a beat later.
        await asyncio.sleep(detected.delay_ms / 1000)

    screenshot: str | None = None
    capture_error: str | None = None

    if sequence > settings.EVENT_CAPTURE_MAX_EVENTS:
        capture_error = (
            f"Event cap of {settings.EVENT_CAPTURE_MAX_EVENTS} reached; "
            "the event was recorded without a screenshot"
        )
    else:
        name = _screenshot_name(sequence, detected.event, at)
        try:
            result = await obs_service.take_screenshot(
                ScreenshotRequest(
                    image_format="png",
                    width=settings.EVENT_CAPTURE_SCREENSHOT_WIDTH,
                    file_name=name,
                    output_dir=run.output_dir,
                )
            )
        except AppException as exc:
            # One failed screenshot is not a reason to end the session -- OBS
            # dropping its socket mid-run is exactly the case this covers, and
            # it re-identifies on the next request.
            capture_error = exc.message
            message = f"{detected.event}: {exc.message}"
            if message not in run.errors:
                run.errors.append(message)
            logger.warning("Screenshot failed for %s: %s", detected.event, exc.message)
        else:
            screenshot = Path(result.file_path).name if result.file_path else None

    run.events.append(
        CapturedEvent(
            sequence=sequence,
            event=detected.event,
            at=at,
            summary=detected.summary,
            fields=dict(detected.fields),
            screenshot=screenshot,
            capture_error=capture_error,
            log_line=detected.line.raw,
        )
    )
    _write_manifest(run, _detail(run, status=CaptureRunState.RUNNING, stopped=None))


def _is_repeat(run: _ActiveRun, detected: game_log.DetectedEvent) -> bool:
    """Whether this event is the same one already recorded moments ago.

    Compares the game's timestamps, not the clock -- see the module docstring.
    A line with no usable timestamp is always let through: silently dropping
    events is worse than an occasional duplicate.
    """
    at = detected.line.timestamp
    if at is None:
        return False
    previous = run.last_seen.get(detected.event)
    run.last_seen[detected.event] = at
    if previous is None:
        return False
    gap = (at - previous).total_seconds()
    return 0 <= gap < settings.EVENT_CAPTURE_DEBOUNCE_SECONDS


def _is_unchanged(run: _ActiveRun, detected: game_log.DetectedEvent) -> bool:
    """Whether a rule that only fires on a change was handed the old values.

    Some lines are re-logged as a statement of the current state rather than of
    a change to it -- HuffNPuffLink writes ``[BetManager.UpdateCurrentBet]``
    several times a round with the bet it already had. The debounce cannot help
    with those: they are seconds apart and genuinely separate lines. What makes
    them a non-event is that nothing in them moved.
    """
    if not detected.only_on_change:
        return False
    fields = dict(detected.fields)
    unchanged = run.last_fields.get(detected.event) == fields
    run.last_fields[detected.event] = fields
    return unchanged


async def _handle(run: _ActiveRun, raw: str) -> None:
    """Capture one appended line, if a rule claims it as something to see.

    Never raises: a live run outranks any one line, so a failure is recorded on
    the run and the next line is still read. Cancellation is a ``BaseException``
    and so passes straight through, which is how the watcher is stopped.
    """
    try:
        line = game_log.parse_line(raw)
        if line is None:
            return
        detected = game_log.match(line, run.rules)
        if detected is None or not detected.capture:
            return
        if _is_repeat(run, detected) or _is_unchanged(run, detected):
            return
        await _capture(run, detected)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if message not in run.errors:
            run.errors.append(message)
        logger.exception("Event capture %s hit an error", run.run_id)


async def _watch(run: _ActiveRun) -> None:
    """Follow the log until cancelled."""
    logger.info("Event capture %s following %s", run.run_id, run.log.path)
    await run.log.follow(lambda raw: _handle(run, raw))


async def _stop_watching(run: _ActiveRun) -> None:
    """End the watcher task.

    Awaiting the cancellation is not optional: a cancelled-but-unawaited task is
    exactly the pending-task warning that fails the test suite.
    """
    task = run.task
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _finish(run: _ActiveRun, *, status: CaptureRunState) -> CaptureRunDetail:
    """Stop the watcher, take one last look, and seal the manifest."""
    await _stop_watching(run)

    # Events logged between the last poll and the stop request are still part of
    # this session, so read once more before sealing.
    for raw in run.log.new_lines():
        await _handle(run, raw)

    detail = _detail(run, status=status, stopped=datetime.now())
    _write_manifest(run, detail)
    logger.info(
        "Event capture %s %s with %d events", run.run_id, status.value, len(run.events)
    )
    return detail


# --- public API -----------------------------------------------------------


async def start() -> CaptureStatus:
    """Begin a run against the active game's log.

    OBS is connected first and deliberately allowed to fail the request: a run
    that cannot take screenshots is a folder of text, which is not what anyone
    pressed the button for.
    """
    global _run
    async with _get_lock():
        if _run is not None:
            raise EventCaptureAlreadyRunningError(
                f"Capture run {_run.run_id} is already in progress; stop it first"
            )

        name, config = _active_config()
        log_path = _log_for(name, config)

        await obs_service.connect()
        # Pointing the source at the game is worth trying but not worth failing
        # for: the scene may already be correct, and connect() attempts it too.
        with contextlib.suppress(AppException):
            await obs_service.select_current_game_window()

        started = datetime.now()
        run_id = started.strftime(_RUN_ID_FORMAT)
        output_dir = f"{settings.EVENT_CAPTURE_DIR_NAME}/{run_id}"
        directory = resolve_subdirectory(settings.obs_screenshot_dir, output_dir)
        directory.mkdir(parents=True, exist_ok=True)

        run = _ActiveRun(
            run_id=run_id,
            game=name,
            directory=directory,
            output_dir=output_dir,
            # Opening the follower here is what makes the run start from the
            # end of the log: what the game did before the button was pressed is
            # not part of it.
            log=LogFollower(log_path, poll_seconds=settings.EVENT_CAPTURE_POLL_SECONDS),
            rules=game_log.resolve_rules(
                extra=config.event_rules, disabled=config.disabled_events
            ),
            started_at=started,
        )
        _write_manifest(run, _detail(run, status=CaptureRunState.RUNNING, stopped=None))
        run.task = asyncio.create_task(_watch(run), name=f"event-capture-{run_id}")
        _run = run

    return status()


async def stop() -> CaptureRunDetail:
    """End the run and return its finished record."""
    global _run
    async with _get_lock():
        run = _run
        if run is None:
            raise EventCaptureNotRunningError(
                "No capture run is in progress, so there is nothing to stop"
            )
        _run = None

    return await _finish(run, status=CaptureRunState.COMPLETED)


def status() -> CaptureStatus:
    """Report the run in progress, if any. Never fails."""
    run = _run
    if run is None:
        return CaptureStatus(active=False)

    elapsed = datetime.now() - run.started_at
    recent = settings.EVENT_CAPTURE_RECENT_EVENTS
    return CaptureStatus(
        active=True,
        run_id=run.run_id,
        game=run.game,
        started_at=run.started_at,
        duration_ms=max(0, int(elapsed.total_seconds() * 1000)),
        event_count=len(run.events),
        recent_events=list(run.events[-recent:]),
        errors=list(run.errors),
    )


def list_runs() -> list[CaptureRunSummary]:
    """Every run on disk, newest first.

    Run ids are timestamps, so sorting by name is sorting by time.
    """
    root = _capture_root()
    if not root.is_dir():
        return []

    summaries: list[CaptureRunSummary] = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name, reverse=True):
        if not directory.is_dir():
            continue
        detail = _read_manifest(directory / MANIFEST_NAME)
        if detail is not None:
            summaries.append(CaptureRunSummary.model_validate(detail.model_dump()))
    return summaries


def _run_directory(run_id: str) -> Path:
    """Resolve one run's directory from an id that came off the wire."""
    try:
        directory = resolve_within(_capture_root(), run_id)
    except UnsafeNameError as exc:
        raise EventCaptureRunNotFoundError(
            f"Invalid capture run id: {exc.reason}"
        ) from exc
    if not directory.is_dir():
        raise EventCaptureRunNotFoundError(f"No capture run named {run_id!r}")
    return directory


def get_run(run_id: str) -> CaptureRunDetail:
    """One run, with all of its events."""
    detail = _read_manifest(_run_directory(run_id) / MANIFEST_NAME)
    if detail is None:
        raise EventCaptureRunNotFoundError(
            f"The record for capture run {run_id!r} is missing or unreadable"
        )
    return detail


def screenshot_path(run_id: str, file_name: str) -> Path:
    """Resolve one screenshot for serving.

    Both halves come from the URL, so both go through the path guards -- this is
    the only route that turns a request into a file read.
    """
    directory = _run_directory(run_id)
    try:
        target = resolve_within(directory, file_name)
    except UnsafeNameError as exc:
        raise EventCaptureRunNotFoundError(
            f"Invalid screenshot name: {exc.reason}"
        ) from exc
    if not target.is_file():
        raise EventCaptureRunNotFoundError(
            f"No screenshot named {file_name!r} in capture run {run_id!r}"
        )
    return target


async def abort() -> None:
    """End any run because the process is shutting down.

    Marks the record ``interrupted`` rather than ``completed``: nobody pressed
    stop, and a reader should be able to tell the difference.
    """
    global _run
    run = _run
    if run is None:
        return
    _run = None
    await _finish(run, status=CaptureRunState.INTERRUPTED)


async def reset() -> None:
    """Drop all state, including the lock bound to this loop. Tests only."""
    global _run, _lock
    run, _run = _run, None
    if run is not None:
        await _stop_watching(run)
    _lock = None
