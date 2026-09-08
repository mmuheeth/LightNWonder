"""Analyze Spin: drives one spin end to end, then validates what it produced."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.config.runtime import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    AppException,
    GameConfigInvalidError,
    SpinAnalysisAlreadyRunningError,
    SpinAnalysisNotRunningError,
    SpinAnalysisUnavailableError,
    SpinClipNotFoundError,
)
from app.schemas.analyze_spin import (
    SpinAnalysisState,
    SpinExpectedAward,
    SpinFrame,
    SpinLineAward,
    SpinLogEvent,
    SpinMeterCheck,
    SpinMeterFigures,
    SpinMeterReading,
    SpinMeterUnit,
    SpinMeterValidation,
    SpinOutcome,
    SpinPaylineValidation,
    SpinRecording,
    SpinReelReading,
    SpinRun,
    SpinRunState,
    SpinStep,
    SpinStepState,
    SpinSymbolReading,
    SpinVerdict,
)
from app.schemas.grid import GridSplitRequest
from app.schemas.image_classifier import ClassifyRequest, ClassifyResult
from app.schemas.meter import MeterMode
from app.schemas.obs import ScreenshotRequest
from app.schemas.paylines import PaylineCheckResult
from app.schemas.paytable import DenominationInfo, PaylineComboInfo, PaytableView
from app.schemas.roi import RoiExtractRequest
from app.schemas.tile_clips import TileClipSet
from app.services import game_input as game_input_service
from app.services import grid as grid_service
from app.services import ideck as ideck_service
from app.services import image_classifier as classifier_service
from app.services import meter as meter_service
from app.services import obs as obs_service
from app.services import paylines as paylines_service
from app.services import paytable as paytable_service
from app.services import roi as roi_service
from app.services import tile_clips as tile_clips_service
from app.utils import game_log
from app.utils import paylines as payline_config
from app.utils.game_math import ANY_SYMBOL
from app.utils.log_tail import LogTail
from app.utils.paths import UnsafeNameError, resolve_subdirectory, resolve_within

logger = get_logger("analyze_spin")

MANIFEST_NAME = "run.json"

# Run ids are timestamps, so a directory listing is in time order.
_RUN_ID_FORMAT = "%Y-%m-%d_%H-%M-%S"

# The region whose crop :mod:`app.services.roi` also reads as a meter. Not
# configurable: that service keys its own meter reading off this exact name.
_METER_REGION = "cash_meter"

# How many snapshots a slow subscriber may fall behind before the oldest is
# dropped. A subscriber only ever wants the newest state, so dropping is right.
_QUEUE_SIZE = 32

# Cancellation is checked between slices of every wait, so no wait blocks for
# longer than this past a cancel.
_SLICE_SECONDS = 0.1

# Reels stopping is logged by the base game and by a free-spin sequence under
# different state machine names; either one ends the spin this module drove.
_REELS_STOPPED = frozenset({"reels-stopped", "free-spin-reels-stopped"})

# The win meter finishing its count-up: the only positive evidence that this
# spin paid something.
_WIN_COUNTED = frozenset({"win-collected"})

# The spin the press was meant to cause, in the game's own words.
_SPIN_STARTED = frozenset({"spin-started"})

# The shortest run a combo can match, so the shortest a wild-led alternative is
# worth looking up at all. The same floor :mod:`app.services.paylines` reports
# `paying` by; kept here rather than imported because this module applies it to a
# *second* length -- the leading wild count -- that the check never priced.
_MIN_RUN = 2


# --- the sequence ---------------------------------------------------------

STEP_PREPARE = "prepare"
STEP_RECORD_START = "record-start"
STEP_FRAME_INITIAL = "frame-initial"
STEP_SPIN = "spin"
STEP_REELS_STOP = "reels-stop"
STEP_WIN_DETECT = "win-detect"
STEP_FRAME_OUTCOME = "frame-outcome"
STEP_TAKE_WIN = "take-win"
STEP_FRAME_COLLECTED = "frame-collected"
STEP_RECORD_STOP = "record-stop"
STEP_METER = "meter"
STEP_CLASSIFY = "classify"
STEP_PAYLINES = "paylines"

_SEQUENCE: tuple[tuple[str, str], ...] = (
    (STEP_PREPARE, "Prepare"),
    (STEP_RECORD_START, "Start recording"),
    (STEP_FRAME_INITIAL, "Screenshot before the spin"),
    (STEP_SPIN, "Trigger the spin"),
    (STEP_REELS_STOP, "Wait for the reels to stop"),
    (STEP_WIN_DETECT, "Find out whether it won"),
    (STEP_FRAME_OUTCOME, "Screenshot the result"),
    (STEP_TAKE_WIN, "Take the win"),
    (STEP_FRAME_COLLECTED, "Screenshot after collecting"),
    (STEP_RECORD_STOP, "Stop recording"),
    # The meter first of the three: it settles which unit the strip was drawing
    # and what a bet unit cost, and the payline award is priced from both.
    (STEP_METER, "Validate the cash meter"),
    (STEP_CLASSIFY, "Name the symbols on the reels"),
    (STEP_PAYLINES, "Validate the paylines"),
)

FRAME_INITIAL = "initial"
FRAME_OUTCOME = "outcome"
FRAME_COLLECTED = "collected"

_FRAME_LABELS = {
    FRAME_INITIAL: "Before the spin",
    FRAME_OUTCOME: "Result on screen",
    FRAME_COLLECTED: "After collecting",
}


class _Cancelled(Exception):
    """Raised inside a run once someone has asked it to stop. Private: a
    cancelled run is a state on the record, never an HTTP failure."""


@dataclass
class _StepRecord:
    """One step's progress. Mutable twin of :class:`SpinStep`."""

    key: str
    label: str
    state: SpinStepState = SpinStepState.PENDING
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None
    error_code: str | None = None


@dataclass
class _ActiveRun:
    """Everything one run needs, and nothing anyone else does."""

    run_id: str
    game: str
    label: str
    directory: Path
    recording_dir: str
    """Run's recording directory, relative to the recording root, as OBS wants it."""

    log_path: Path
    rules: tuple[game_log.EventRule, ...]
    started_at: datetime
    architecture: str
    """Which trained network names this run's tiles. Resolved when the run is
    asked for rather than when the classify step runs, so an unknown name is a
    refused request instead of a failure eight steps in -- and so the record says
    which model graded the spin even if the step never got there."""

    record: bool
    """Whether this run also makes a video of itself. When false the two
    recording steps are left out of `steps` entirely, rather than shown and
    immediately skipped -- a caller who did not ask to record should not see
    anything about recording at all."""

    steps: dict[str, _StepRecord]
    state: SpinRunState = SpinRunState.RUNNING
    outcome: SpinOutcome = SpinOutcome.UNKNOWN
    message: str = "Starting"
    finished_at: datetime | None = None

    frames: list[SpinFrame] = field(default_factory=list)
    events: list[SpinLogEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    recording: SpinRecording | None = None
    recording_started: bool = False

    tile_clips: TileClipSet | None = None
    """One short video per reel position, from the seconds after a win landed.
    Only ever on a run that was recording and only when the spin paid, so
    ``None`` is the ordinary case rather than a failure."""

    paytable: PaytableView | None = None
    paytable_error: str | None = None
    bet_per_unit: int | None = None
    """Credits staked on each bet unit -- the multiplier that turns a line's
    paytable rate into an award. Worked out at the meter step from the bet the
    glass drew, which is why it is not known when the run is created."""

    meter: SpinMeterValidation | None = None
    reels: SpinReelReading | None = None
    reels_error: str | None = None
    symbols: dict[str, str | None] = field(default_factory=dict)
    """Symbol code per tile position, as the classifier named it -- ``None`` for a
    tile below the confidence floor. What the payline check reads the lines by."""

    paylines: SpinPaylineValidation | None = None

    cancel_requested: bool = False
    task: asyncio.Task[None] | None = None

    def frame(self, key: str) -> SpinFrame | None:
        """One captured frame by its moment, or ``None`` if it was never taken."""
        return next((one for one in self.frames if one.key == key), None)


_run: _ActiveRun | None = None
_lock: asyncio.Lock | None = None
_subscribers: set[asyncio.Queue[SpinAnalysisState]] = set()


def _get_lock() -> asyncio.Lock:
    """Serialise starting a run. Built lazily, like every other service's here,
    since a lock made at import time would bind to the wrong loop in tests."""
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
    """The game's log, checked to be there."""
    if config.log_path is None:
        raise SpinAnalysisUnavailableError(
            f"The game config for {name!r} declares no 'log' path, so a spin "
            "cannot be followed from press to result"
        )
    if not config.log_path.is_file():
        raise SpinAnalysisUnavailableError(
            f"The log for {name!r} does not exist yet: {config.log_path}. "
            "Start the game and try again."
        )
    return config.log_path


# --- reading the log ------------------------------------------------------


class _LogReader:
    """A one-way read over the game's log, from where the run opened it."""

    def __init__(
        self,
        path: Path,
        rules: tuple[game_log.EventRule, ...],
        *,
        poll_seconds: float,
    ) -> None:
        self._tail = LogTail(path, poll_seconds=poll_seconds)
        self._rules = rules
        self._cursor = self._tail.offset()
        self._pending: deque[str] = deque()
        self.poll_seconds = poll_seconds

    def drain(self) -> None:
        """Take in whatever the game has appended since the last look."""
        chunk, self._cursor = self._tail.read_since(self._cursor)
        if chunk:
            self._pending.extend(chunk.splitlines())

    def next_event(self) -> game_log.DetectedEvent | None:
        """The next recognised event in the buffer, consuming everything before
        it. ``None`` once the buffer holds nothing recognisable."""
        while self._pending:
            line = game_log.parse_line(self._pending.popleft())
            if line is None:
                continue
            found = game_log.match(line, self._rules)
            if found is not None:
                return found
        return None


# --- progress -------------------------------------------------------------


def _lean_meter(meter: SpinMeterValidation) -> SpinMeterValidation:
    """The meter validation with its crop pictures dropped."""
    return meter.model_copy(
        update={
            "readings": [
                reading.model_copy(update={"crop_image": None})
                for reading in meter.readings
            ]
        }
    )


def _lean_paylines(paylines: SpinPaylineValidation) -> SpinPaylineValidation:
    """The payline validation with its overlay and per-line pictures dropped."""
    return paylines.model_copy(
        update={
            "overlay_image": None,
            "lines": [
                line.model_copy(update={"image_data": None}) for line in paylines.lines
            ],
        }
    )


def _detail(run: _ActiveRun, *, images: bool) -> SpinRun:
    """The record both the manifest and the API return."""
    finished = run.finished_at or datetime.now()
    meter = run.meter
    reels = run.reels
    paylines = run.paylines
    return SpinRun(
        run_id=run.run_id,
        game=run.game,
        label=run.label,
        state=run.state,
        outcome=run.outcome,
        message=run.message,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=max(0, int((finished - run.started_at).total_seconds() * 1000)),
        steps=[
            SpinStep(
                key=step.key,
                label=step.label,
                state=step.state,
                detail=step.detail,
                started_at=step.started_at,
                finished_at=step.finished_at,
                duration_ms=step.duration_ms,
                error=step.error,
                error_code=step.error_code,
            )
            for step in run.steps.values()
        ],
        frames=list(run.frames),
        events=list(run.events),
        recording=run.recording,
        # Carried on the progress stream too: the set holds file names and
        # figures, never the videos themselves, so there is nothing on it a
        # snapshot would rather not send.
        tile_clips=run.tile_clips,
        meter=(meter if images or meter is None else _lean_meter(meter)),
        # Carried whole either way: the reading holds no pictures, so there
        # is nothing on it that the progress stream would rather not send.
        reels=reels,
        paylines=(paylines if images or paylines is None else _lean_paylines(paylines)),
        errors=list(run.errors),
    )


def _snapshot(run: _ActiveRun | None, *, images: bool) -> SpinAnalysisState:
    """The whole service's state, as every caller and the stream see it."""
    if run is None:
        return SpinAnalysisState(active=False, run=None)
    return SpinAnalysisState(
        active=run.state is SpinRunState.RUNNING,
        run=_detail(run, images=images),
    )


def _offer(queue: asyncio.Queue[SpinAnalysisState], state: SpinAnalysisState) -> None:
    """Hand one snapshot to one subscriber, dropping the oldest if it is behind."""
    while True:
        try:
            queue.put_nowait(state)
            return
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - drained concurrently
                return


def _publish(run: _ActiveRun) -> None:
    """Push the current state to every subscriber. Never raises: a broken
    stream is not a reason to fail a spin."""
    if not _subscribers:
        return
    state = _snapshot(run, images=False)
    for queue in list(_subscribers):
        _offer(queue, state)


@contextlib.contextmanager
def subscribe() -> Iterator[asyncio.Queue[SpinAnalysisState]]:
    """Follow the run's progress. The queue carries whole snapshots, not deltas,
    so a subscriber that joins mid-run or misses one is still correct."""
    queue: asyncio.Queue[SpinAnalysisState] = asyncio.Queue(maxsize=_QUEUE_SIZE)
    _subscribers.add(queue)
    try:
        yield queue
    finally:
        _subscribers.discard(queue)


def _note_error(run: _ActiveRun, message: str) -> None:
    """Record something that went wrong, once."""
    if message not in run.errors:
        run.errors.append(message)


def _record_event(run: _ActiveRun, detected: game_log.DetectedEvent) -> None:
    """Keep one recognised log line on the run, capped so a chatty game cannot
    grow the record without bound."""
    run.events.append(
        SpinLogEvent(
            event=detected.event,
            summary=detected.summary,
            at=detected.line.timestamp,
            log_line=detected.line.raw,
        )
    )
    cap = settings.ANALYZE_SPIN_MAX_EVENTS
    if len(run.events) > cap:
        del run.events[: len(run.events) - cap]
    _publish(run)


# --- steps ----------------------------------------------------------------


def _check_cancelled(run: _ActiveRun) -> None:
    """Give up here if someone has asked the run to stop."""
    if run.cancel_requested:
        raise _Cancelled


def _finish_step(step: _StepRecord, state: SpinStepState) -> None:
    """Close one step off and time it."""
    step.state = state
    step.finished_at = datetime.now()
    if step.started_at is not None:
        step.duration_ms = max(
            0, int((step.finished_at - step.started_at).total_seconds() * 1000)
        )


@contextlib.asynccontextmanager
async def _step(run: _ActiveRun, key: str) -> AsyncIterator[_StepRecord]:
    """Run one step of the sequence, recording how it went either way."""
    _check_cancelled(run)
    step = run.steps[key]
    step.state = SpinStepState.RUNNING
    step.started_at = datetime.now()
    run.message = step.label
    _publish(run)
    try:
        yield step
    except _Cancelled:
        step.detail = step.detail or "Cancelled before it finished"
        _finish_step(step, SpinStepState.SKIPPED)
        raise
    except AppException as exc:
        step.error = exc.message
        step.error_code = exc.error_code
        _finish_step(step, SpinStepState.FAILED)
        _note_error(run, f"{step.label}: {exc.message}")
        logger.warning("Spin %s failed at %s: %s", run.run_id, key, exc.message)
        raise
    except Exception as exc:
        step.error = f"{type(exc).__name__}: {exc}"
        _finish_step(step, SpinStepState.FAILED)
        _note_error(run, f"{step.label}: {step.error}")
        logger.exception("Spin %s hit an unexpected error at %s", run.run_id, key)
        raise
    else:
        _finish_step(
            step,
            SpinStepState.FAILED if step.error else SpinStepState.COMPLETED,
        )
    finally:
        _publish(run)


def _skip(run: _ActiveRun, key: str, detail: str) -> None:
    """Mark a step deliberately not run, which is not the same as unreached."""
    step = run.steps[key]
    step.state = SpinStepState.SKIPPED
    step.detail = detail
    _publish(run)


async def _sleep(run: _ActiveRun, seconds: float) -> None:
    """Wait, in slices, so a cancel does not have to wait it out."""
    deadline = time.monotonic() + seconds
    while True:
        _check_cancelled(run)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, _SLICE_SECONDS))


async def _wait_for(
    run: _ActiveRun,
    reader: _LogReader,
    wanted: frozenset[str],
    *,
    timeout: float,
) -> game_log.DetectedEvent | None:
    """Wait for the game to log one of ``wanted``, or give up."""
    deadline = time.monotonic() + timeout
    while True:
        _check_cancelled(run)
        reader.drain()
        while (found := reader.next_event()) is not None:
            _record_event(run, found)
            if found.event in wanted:
                return found
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(min(reader.poll_seconds, _SLICE_SECONDS))


# --- driving the spin -----------------------------------------------------


async def _prepare(run: _ActiveRun) -> None:
    """Get OBS pointed at the game, and read the maths this spin will play by."""
    async with _step(run, STEP_PREPARE) as step:
        await obs_service.connect()
        # Worth trying, not worth failing for -- the scene may already be right.
        retargeted = True
        try:
            await obs_service.select_current_game_window()
        except AppException:
            retargeted = False

        panel = await ideck_service.status()
        window = await game_input_service.status()

        try:
            run.paytable = await paytable_service.view()
        except AppException as exc:
            run.paytable_error = exc.message
            _note_error(run, f"Paytable: {exc.message}")
            logger.warning("Spin %s has no paytable: %s", run.run_id, exc.message)

        loaded = (
            f"paytable {run.paytable.paytable_id}"
            if run.paytable is not None
            else "no paytable"
        )
        step.detail = (
            f"OBS connected, i-deck {panel.state.value}, "
            f"game window {window.state.value}, {loaded}"
        )

        # Re-pointing a window capture makes OBS render nothing for a moment,
        # and a screenshot taken inside it comes back black -- an empty frame
        # that is an error nowhere and quietly invalidates both validations.
        if retargeted:
            await _sleep(run, settings.ANALYZE_SPIN_SOURCE_SETTLE_SECONDS)


async def _start_recording(run: _ActiveRun) -> None:
    """Begin the video of the spin."""
    if not run.record:
        return
    async with _step(run, STEP_RECORD_START) as step:
        await obs_service.start_recording(run.recording_dir)
        run.recording_started = True
        step.detail = f"Recording into {run.recording_dir}"


async def _stop_recording(run: _ActiveRun) -> None:
    """End the video. Idempotent, so the same call serves the happy path and the tidy-up
    after a failure -- a run that died mid-spin must not leave OBS recording."""
    if not run.record:
        return
    if run.steps[STEP_RECORD_STOP].state is not SpinStepState.PENDING:
        return
    if not run.recording_started:
        _skip(run, STEP_RECORD_STOP, "Nothing was recorded")
        return
    async with _step(run, STEP_RECORD_STOP) as step:
        status = await obs_service.stop_recording()
        run.recording = SpinRecording(
            output_path=status.output_path, duration_ms=status.duration_ms
        )
        step.detail = status.output_path or "OBS did not report an output path"


async def _stop_recording_quietly(run: _ActiveRun) -> None:
    """Stop the recording without letting the attempt raise."""
    if not run.record:
        return
    if run.steps[STEP_RECORD_STOP].state is not SpinStepState.PENDING:
        return
    if not run.recording_started:
        _skip(run, STEP_RECORD_STOP, "Nothing was recorded")
        return
    step = run.steps[STEP_RECORD_STOP]
    try:
        status = await obs_service.stop_recording()
    except AppException as exc:
        step.state = SpinStepState.FAILED
        step.error = exc.message
        step.error_code = exc.error_code
        _note_error(run, f"{step.label}: {exc.message}")
    else:
        run.recording = SpinRecording(
            output_path=status.output_path, duration_ms=status.duration_ms
        )
        step.state = SpinStepState.COMPLETED
        step.detail = status.output_path or "OBS did not report an output path"
    step.finished_at = datetime.now()


async def _capture(run: _ActiveRun, key: str, step_key: str) -> None:
    """Take one screenshot of this moment."""
    async with _step(run, step_key) as step:
        sequence = len(run.frames) + 1
        attempts = 0
        blank = True
        path: Path | None = None
        while True:
            attempts += 1
            result = await obs_service.take_screenshot(
                ScreenshotRequest(
                    image_format="png",
                    width=settings.ANALYZE_SPIN_SCREENSHOT_WIDTH,
                    file_name=f"spin-{run.run_id}-{sequence}-{key}",
                    output_dir=settings.OBS_SCREENSHOT_SUBDIR,
                )
            )
            if result.file_path is None:
                raise SpinAnalysisUnavailableError(
                    "OBS returned the screenshot but wrote no file, so there is "
                    "nothing for the validations to read"
                )
            path = Path(result.file_path)
            # Read back rather than trusted: OBS reports a successful write of a
            # frame it rendered nothing into, so the only way to know the capture
            # happened is to look at it.
            blank = await asyncio.to_thread(roi_service.is_blank, path)
            if not blank or attempts > settings.ANALYZE_SPIN_BLANK_RETRIES:
                break
            logger.warning(
                "Spin %s captured an empty %s frame; retrying", run.run_id, key
            )
            await _sleep(run, settings.ANALYZE_SPIN_BLANK_RETRY_SECONDS)

        run.frames.append(
            SpinFrame(
                key=key,
                label=_FRAME_LABELS[key],
                file_name=path.name,
                at=datetime.now(),
                blank=blank,
                attempts=attempts,
            )
        )
        step.detail = path.name
        if blank:
            # Recorded, not raised: the spin and everything after it is still
            # worth having, and a frame nobody can read is visible here and on
            # every reading taken off it.
            step.error = (
                f"OBS wrote {path.name} with nothing in it, after {attempts} "
                "attempt(s). Check that its window-capture source is pointed at "
                "the game and showing it."
            )
            _note_error(run, f"{step.label}: {step.error}")


async def _spin(run: _ActiveRun) -> _LogReader:
    """Press the spin key, and wait for the game to agree that it spun."""
    reader = _LogReader(
        run.log_path, run.rules, poll_seconds=settings.ANALYZE_SPIN_POLL_SECONDS
    )
    button = settings.ANALYZE_SPIN_SPIN_BUTTON
    async with _step(run, STEP_SPIN) as step:
        press = await ideck_service.press(button)
        timeout = settings.ANALYZE_SPIN_SPIN_TIMEOUT_SECONDS
        detected = await _wait_for(run, reader, _SPIN_STARTED, timeout=timeout)
        if detected is None:
            # The panel confirmed the key; the game did nothing with it. The
            # deck's layout belongs to the cabinet, so this is a real and
            # common state rather than a fault.
            raise SpinAnalysisUnavailableError(
                f"The panel registered {button!r} but {run.label} published no "
                f"spin within {timeout}s. That key may not be the one this game "
                "spins on -- check ANALYZE_SPIN_SPIN_BUTTON against "
                "GET /api/ideck/buttons and the game's own log."
            )
        step.detail = (
            f"{button} pressed in {press.elapsed_ms}ms, and the game published the spin"
        )
    return reader


async def _wait_reels(run: _ActiveRun, reader: _LogReader) -> None:
    """Wait for the reels to settle."""
    timeout = settings.ANALYZE_SPIN_REELS_TIMEOUT_SECONDS
    async with _step(run, STEP_REELS_STOP) as step:
        detected = await _wait_for(run, reader, _REELS_STOPPED, timeout=timeout)
        if detected is None:
            raise SpinAnalysisUnavailableError(
                f"The reels of {run.label} had not stopped {timeout}s after the "
                "spin started. The run's events list shows what the game was "
                "doing instead."
            )
        # The rule's own delay: the log records the decision, the screen catches
        # up a beat later. Honoured rather than re-guessed here.
        if detected.delay_ms:
            await _sleep(run, detected.delay_ms / 1000)
        step.detail = detected.summary


async def _detect_win(run: _ActiveRun, reader: _LogReader) -> None:
    """Decide whether this spin paid, by whether the win meter counts up."""
    wait = settings.ANALYZE_SPIN_WIN_WAIT_SECONDS
    async with _step(run, STEP_WIN_DETECT) as step:
        detected = await _wait_for(run, reader, _WIN_COUNTED, timeout=wait)
        if detected is None:
            run.outcome = SpinOutcome.NO_WIN
            step.detail = (
                f"No win meter count-up within {wait}s, so the spin paid nothing"
            )
            return
        run.outcome = SpinOutcome.WIN
        await _sleep(run, settings.ANALYZE_SPIN_WIN_SETTLE_SECONDS)
        step.detail = detected.summary


async def _record_tile_clips(run: _ActiveRun) -> None:
    """Film each reel position on its own for a few seconds after a win lands."""
    if not run.record or not settings.ANALYZE_SPIN_TILE_CLIPS:
        return
    _check_cancelled(run)
    directory = run.directory / settings.ANALYZE_SPIN_TILE_CLIP_DIR_NAME
    try:
        run.tile_clips = await tile_clips_service.record(
            directory,
            seconds=settings.ANALYZE_SPIN_TILE_CLIP_SECONDS,
            fps=settings.ANALYZE_SPIN_TILE_CLIP_FPS,
            # Cooperative, like every wait here: the capture ends at its next
            # frame rather than being waited out, and writes what it has.
            should_stop=lambda: run.cancel_requested,
        )
    except Exception as exc:
        # Broad on purpose: `record` documents that it never raises, and the
        # cost of that being wrong must not be a spin lost after it was driven.
        logger.exception("Spin %s could not film its tiles", run.run_id)
        run.tile_clips = TileClipSet(error=f"{type(exc).__name__}: {exc}")

    if run.tile_clips.error is not None:
        _note_error(run, f"Tile clips: {run.tile_clips.error}")
    _publish(run)
    _check_cancelled(run)


async def _take_win(run: _ActiveRun) -> None:
    """Collect the win by clicking the game's own glass -- take-win is not one
    of the deck's fourteen keys."""
    target = settings.ANALYZE_SPIN_TAKE_WIN_TARGET
    async with _step(run, STEP_TAKE_WIN) as step:
        result = await game_input_service.click(target)
        proof = result.confirmed_by.value if result.confirmed_by else "unverified"
        step.detail = (
            f"Clicked {target} at ({result.client_x}, {result.client_y}); "
            f"confirmed by {proof}"
        )
        # The click is proven by the log; the balance moving is an animation.
        await _sleep(run, settings.ANALYZE_SPIN_COLLECT_SETTLE_SECONDS)


# --- cash meter validation ------------------------------------------------


def _balance(reading_cash: float | None, reading_credits: float | None) -> float | None:
    """Whichever of the two the meter was showing."""
    return reading_cash if reading_cash is not None else reading_credits


def _units(
    mode: MeterMode, currency: str | None, denomination: DenominationInfo | None
) -> str:
    """What the step's own one-line detail calls the numbers it read."""
    if mode is not MeterMode.CASH:
        read = mode.value
    elif currency is None or currency == meter_service.UNNAMED_SYMBOL:
        read = "cash (currency symbol unreadable)"
    else:
        read = f"cash ({currency})"
    return read if denomination is None else f"{read} at {denomination.label}"


async def _read_meter(frame: SpinFrame) -> SpinMeterReading:
    """Read the meter off one frame. Never raises for a bad *reading* -- ROI carries
    that as ``meter.error`` -- only for a frame or region it cannot reach at all."""
    result = await roi_service.extract(
        RoiExtractRequest(region=_METER_REGION, file_name=frame.file_name)
    )
    values = result.meter
    return SpinMeterReading(
        frame=frame.key,
        label=frame.label,
        file_name=frame.file_name,
        balance=None if values is None else _balance(values.cash, values.credits),
        win=None if values is None else values.win,
        bet=None if values is None else values.bet,
        values=values,
        error=(
            "the cash meter region produced no reading"
            if values is None
            else values.error
        ),
        crop_image=result.image_data,
    )


def _scaled(figures: SpinMeterFigures, factor: float | None) -> SpinMeterFigures:
    """``figures`` in the other unit, or empty when there is no rate to use."""
    if factor is None:
        return SpinMeterFigures()

    def convert(value: float | None) -> float | None:
        return None if value is None else round(value * factor, 4)

    return SpinMeterFigures(
        balance=convert(figures.balance),
        win=convert(figures.win),
        bet=convert(figures.bet),
    )


def _in_both_units(
    reading: SpinMeterReading, mode: MeterMode, rate: float | None
) -> SpinMeterReading:
    """The reading's three numbers in credits *and* in money."""
    if mode is MeterMode.UNKNOWN:
        return reading
    drawn = SpinMeterFigures(balance=reading.balance, win=reading.win, bet=reading.bet)
    if mode is MeterMode.CASH:
        # Money to credits divides, so a zero rate would raise rather than report.
        return reading.model_copy(
            update={
                "cash": drawn,
                "credits": _scaled(drawn, None if not rate else 1 / rate),
            }
        )
    return reading.model_copy(update={"credits": drawn, "cash": _scaled(drawn, rate)})


def _figures(reading: SpinMeterReading, unit: SpinMeterUnit) -> SpinMeterFigures:
    """One reading's numbers in ``unit``."""
    return reading.credits if unit is SpinMeterUnit.CREDITS else reading.cash


def _tolerance(unit: SpinMeterUnit) -> float:
    """How far two amounts of ``unit`` may differ and still be called equal."""
    return (
        settings.ANALYZE_SPIN_METER_CREDIT_TOLERANCE
        if unit is SpinMeterUnit.CREDITS
        else settings.ANALYZE_SPIN_METER_TOLERANCE
    )


def _close(left: float, right: float, tolerance: float) -> bool:
    """Whether two amounts are the same number to the precision they were read at."""
    return abs(left - right) <= tolerance


def _plus(left: float | None, right: float | None) -> float | None:
    """Sum of two amounts, or ``None`` when either could not be read."""
    return None if left is None or right is None else left + right


def _minus(left: float | None, right: float | None) -> float | None:
    """Difference of two amounts, or ``None`` when either could not be read."""
    return None if left is None or right is None else left - right


def _amount(value: float | None) -> str:
    """One amount for a detail line, or a placeholder when it could not be read."""
    return "unreadable" if value is None else f"{value:.2f}"


def _relation(
    key: str,
    label: str,
    *,
    unit: SpinMeterUnit,
    expected: float | None,
    actual: float | None,
    tolerance: float,
    detail: str,
) -> SpinMeterCheck:
    """One expected-versus-actual comparison, indeterminate if either is missing."""
    if expected is None or actual is None:
        return SpinMeterCheck(
            key=key,
            unit=unit,
            label=label,
            verdict=SpinVerdict.INDETERMINATE,
            expected=expected,
            actual=actual,
            detail=detail,
        )
    return SpinMeterCheck(
        key=key,
        unit=unit,
        label=label,
        verdict=(
            SpinVerdict.PASSED
            if _close(expected, actual, tolerance)
            else SpinVerdict.FAILED
        ),
        expected=round(expected, 4),
        actual=round(actual, 4),
        difference=round(actual - expected, 4),
        detail=detail,
    )


def _unit_checks(
    by_frame: dict[str, SpinMeterReading], unit: SpinMeterUnit
) -> list[SpinMeterCheck]:
    """The balance arithmetic over one unit's figures."""
    initial = by_frame.get(FRAME_INITIAL)
    outcome = by_frame.get(FRAME_OUTCOME)
    collected = by_frame.get(FRAME_COLLECTED)
    figures = [_figures(reading, unit) for reading in by_frame.values()]
    if not any(f.balance is not None or f.bet is not None for f in figures):
        return []

    tolerance = _tolerance(unit)
    named = "credits" if unit is SpinMeterUnit.CREDITS else "money"
    checks: list[SpinMeterCheck] = []

    def add(
        key: str,
        label: str,
        expected: float | None,
        actual: float | None,
        detail: str,
    ) -> None:
        checks.append(
            _relation(
                f"{key}-{unit.value}",
                f"{label} ({named})",
                unit=unit,
                expected=expected,
                actual=actual,
                tolerance=tolerance,
                detail=detail,
            )
        )

    if initial is not None and outcome is not None:
        before, after = _figures(initial, unit), _figures(outcome, unit)
        add(
            "bet-stable",
            "The bet is unchanged by the spin",
            before.bet,
            after.bet,
            f"bet was {_amount(before.bet)} before the spin and "
            f"{_amount(after.bet)} after it",
        )
        deducted = _minus(before.balance, before.bet)
        add(
            "bet-deducted",
            "The bet came off the balance",
            deducted,
            after.balance,
            f"{_amount(before.balance)} - {_amount(before.bet)} = "
            f"{_amount(deducted)}, and the balance reads {_amount(after.balance)}",
        )

    if outcome is not None and collected is not None:
        after, end = _figures(outcome, unit), _figures(collected, unit)
        paid = _plus(after.balance, after.win)
        add(
            "win-collected",
            "The win went onto the balance",
            paid,
            end.balance,
            f"{_amount(after.balance)} + {_amount(after.win)} = "
            f"{_amount(paid)}, and the balance reads {_amount(end.balance)}",
        )
        # Deliberately no "the win meter cleared" check: these games leave the
        # last win on the WIN cell after it has been collected, so an empty one
        # would be the surprise. The balance moving is the proof of collection,
        # and that is the check above.

        # And the whole spin as one identity, end to end. Not implied by the two
        # relations above even though it follows from them: each of those compares
        # one *pair* of frames, so a compensating misread in the middle frame
        # cancels out across them and only this notices.
        before = _figures(initial, unit) if initial is not None else SpinMeterFigures()
        reconciled = _plus(_minus(before.balance, before.bet), after.win)
        add(
            "balance-reconciled",
            "The whole spin adds up",
            reconciled,
            end.balance,
            f"{_amount(before.balance)} - {_amount(before.bet)} bet + "
            f"{_amount(after.win)} won = {_amount(reconciled)}, and the "
            f"balance ends at {_amount(end.balance)}",
        )

    return checks


def _win_registered(run: _ActiveRun, outcome: SpinMeterReading) -> SpinMeterCheck:
    """Whether the WIN cell agrees with what the game's log said."""
    won = run.outcome is SpinOutcome.WIN
    registered = outcome.win is not None and outcome.win > 0
    if outcome.error is not None:
        verdict = SpinVerdict.INDETERMINATE
    else:
        verdict = SpinVerdict.PASSED if registered == won else SpinVerdict.FAILED
    return SpinMeterCheck(
        key="win-registered" if won else "win-empty",
        label=(
            "The win meter shows an amount" if won else "The win meter stayed empty"
        ),
        verdict=verdict,
        actual=outcome.win,
        detail=(
            f"the game logged {'a win' if won else 'no win'} and the WIN cell reads "
            f"{'nothing' if outcome.win is None else _amount(outcome.win)}"
        ),
    )


def _settle_mode(
    run: _ActiveRun, mode: MeterMode, readings: list[SpinMeterReading]
) -> tuple[MeterMode, str | None]:
    """Decide which unit the strip drew, using what the cabinet may be bet at."""
    bet = None if run.paytable is None else run.paytable.bet_config
    denomination = None if run.paytable is None else run.paytable.denomination
    rate = None if denomination is None else denomination.money_per_credit
    if bet is None or not bet.unit_cost or not bet.ladder or not rate:
        return mode, None

    initial = next((r for r in readings if r.frame == FRAME_INITIAL), None)
    drawn = None if initial is None else initial.bet
    if drawn is None:
        return mode, None

    totals = [bet.unit_cost * rung for rung in bet.ladder]
    as_credits = any(
        _close(total, drawn, _tolerance(SpinMeterUnit.CREDITS)) for total in totals
    )
    as_cash = any(
        _close(total * rate, drawn, _tolerance(SpinMeterUnit.CASH)) for total in totals
    )
    if as_credits == as_cash:
        # Both or neither: at a $1 denomination a bet reads the same in either
        # unit, and a bet matching nothing is not evidence about the strip.
        return mode, None

    settled = MeterMode.CREDITS if as_credits else MeterMode.CASH
    if settled is mode:
        return mode, None
    return settled, (
        f"The meter read as {mode.value}, but a bet of {drawn:g} is a bet this "
        f"paytable takes in {settled.value} and not in {mode.value}, so the "
        f"strip was drawing {settled.value}"
    )


def _derive_bet_per_unit(
    run: _ActiveRun, readings: list[SpinMeterReading]
) -> int | None:
    """Work out what was staked on each bet unit from the bet the meter drew."""
    bet = None if run.paytable is None else run.paytable.bet_config
    if bet is None or not bet.unit_cost:
        return None
    cost = bet.unit_cost

    initial = next((r for r in readings if r.frame == FRAME_INITIAL), None)
    staked = None if initial is None else initial.credits.bet
    if staked is None:
        return None

    rungs = round(staked / cost)
    if rungs < 1:
        return None
    # Rounding is not enough on its own: 300 credits over an 88-credit spin
    # rounds to 3, and 3 is a rung, but 88 x 3 is 264 and not 300. So the
    # product has to come back to the bet that was read -- otherwise a misread
    # BET cell silently misprices every line on the run, which is the one
    # failure worth refusing to guess through.
    if not _close(rungs * cost, staked, _tolerance(SpinMeterUnit.CREDITS)):
        return None
    # A ladder is a whitelist when the game shipped one. Without it any whole
    # number is allowed -- a machine with no install has no rungs to check.
    if bet.ladder and rungs not in bet.ladder:
        return None
    return rungs


def _meter_checks(
    run: _ActiveRun, readings: list[SpinMeterReading]
) -> list[SpinMeterCheck]:
    """Every relation between the frames' meters, checked in both units."""
    by_frame = {reading.frame: reading for reading in readings}
    outcome = by_frame.get(FRAME_OUTCOME)
    checks: list[SpinMeterCheck] = []
    if outcome is not None:
        checks.append(_win_registered(run, outcome))
    for unit in (SpinMeterUnit.CREDITS, SpinMeterUnit.CASH):
        checks.extend(_unit_checks(by_frame, unit))
    return checks


def _verdict_of(verdicts: list[SpinVerdict]) -> SpinVerdict:
    """The worst of several verdicts, with 'failed' ranking below 'indeterminate' -- a
    real discrepancy is not softened by an unreadable neighbour."""
    if not verdicts:
        return SpinVerdict.INDETERMINATE
    if SpinVerdict.FAILED in verdicts:
        return SpinVerdict.FAILED
    if SpinVerdict.INDETERMINATE in verdicts:
        return SpinVerdict.INDETERMINATE
    return SpinVerdict.PASSED


async def _validate_meter(run: _ActiveRun) -> None:
    """Read the meter off every screenshot the run took, and check the arithmetic
    between them."""
    tolerance = settings.ANALYZE_SPIN_METER_TOLERANCE
    try:
        async with _step(run, STEP_METER) as step:
            readings: list[SpinMeterReading] = []
            failure: str | None = None
            for frame in run.frames:
                _check_cancelled(run)
                try:
                    readings.append(await _read_meter(frame))
                except AppException as exc:
                    # An undeclared region or a missing engine is one problem for
                    # every frame, so there is nothing to gain from the rest.
                    failure = exc.message
                    break

            # The units are settled before anything is checked, and in this
            # order: which side the strip was drawing comes from every frame at
            # once, the rate that converts to the other side comes from the
            # paytable read at `prepare` -- before the reels turned, so it is the
            # denomination this spin actually played at -- and only then can a
            # reading be expressed in both.
            mode, currency = meter_service.combine(
                reading.values for reading in readings
            )
            denomination = None if run.paytable is None else run.paytable.denomination
            rate = None if denomination is None else denomination.money_per_credit
            # Which unit was drawn is settled before anything is converted by it,
            # because getting it wrong divides every figure on the run by the
            # denomination a second time.
            mode, misread = _settle_mode(run, mode, readings)
            if misread is not None:
                _note_error(run, f"Cash meter: {misread}")
            readings = [_in_both_units(reading, mode, rate) for reading in readings]

            # What the player had staked, when nobody said. This step runs
            # before the reels are read, so the stake is known in time to price
            # every line the first time rather than re-pricing them after.
            if run.bet_per_unit is None:
                derived = _derive_bet_per_unit(run, readings)
                if derived is not None:
                    run.bet_per_unit = derived

            checks = _meter_checks(run, readings)
            validation = SpinMeterValidation(
                mode=mode,
                currency=currency,
                denomination=denomination,
                readings=readings,
                checks=checks,
                tolerance=tolerance,
                credit_tolerance=settings.ANALYZE_SPIN_METER_CREDIT_TOLERANCE,
                verdict=_verdict_of([check.verdict for check in checks]),
                error=failure,
            )
            run.meter = validation

            if failure is not None:
                raise SpinAnalysisUnavailableError(failure)
            step.detail = (
                f"{len(readings)} frames read in "
                f"{_units(mode, currency, denomination)}, "
                f"{len(checks)} checks: {validation.verdict.value}"
            )
    except _Cancelled:
        raise
    except Exception:  # noqa: BLE001 - already recorded on the step
        return


# --- reading the reels ----------------------------------------------------


def _reading(result: ClassifyResult) -> SpinReelReading:
    """The classifier's answer, as this feature's record of what landed."""
    return SpinReelReading(
        split=result.split,
        architecture=result.model.architecture,
        label=result.model.label,
        trained_at=result.model.trained_at,
        min_confidence=result.min_confidence,
        rows=result.rows,
        columns=result.columns,
        symbol_grid=[list(row) for row in result.symbol_grid],
        label_grid=[list(row) for row in result.label_grid],
        tiles=[
            SpinSymbolReading(
                name=tile.name,
                row=tile.row,
                column=tile.column,
                symbol=tile.symbol,
                label=tile.label,
                # The winner even when it was rejected: a tile that came back
                # unnamed still leaned somewhere, and that is the whole evidence
                # for the rejection.
                leading=(
                    tile.predictions[0].symbol if tile.predictions else tile.symbol
                ),
                confidence=tile.confidence,
                known=tile.known,
            )
            for tile in result.tiles
        ],
        named=result.named,
        unknown=result.unknown,
        summary=result.summary,
        output_dir=result.output_dir,
        overlay_file=result.overlay_file,
    )


async def _read_reels(run: _ActiveRun) -> None:
    """Split the result screenshot and name every tile of it."""
    try:
        async with _step(run, STEP_CLASSIFY) as step:
            frame = run.frame(FRAME_OUTCOME)
            if frame is None:
                raise SpinAnalysisUnavailableError(
                    "No result screenshot was taken, so there are no reels to read"
                )
            # The split is written here rather than in the payline step because
            # the tiles are what gets classified: the grid names the directory,
            # the classifier names the tiles in it, and the payline check reads
            # both back by that name.
            split = await grid_service.split(
                GridSplitRequest(file_name=frame.file_name, include_images=False)
            )
            result = await classifier_service.classify(
                ClassifyRequest(
                    split=Path(split.output_dir).name,
                    architecture=run.architecture,
                    min_confidence=settings.ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE,
                    # No pictures on the payload: the fifteen per-tile crops
                    # and the ringed reels are both already on disk, and the
                    # dashboard draws neither -- the codes and their
                    # confidences are what it reads. The overlay is still
                    # *written*, since a ring over a cell is the cheapest way
                    # to check the split was named the way it looks.
                    include_images=False,
                    include_overlay=True,
                )
            )
            run.reels = _reading(result)
            run.symbols = {tile.name: tile.symbol for tile in result.tiles}
            step.detail = f"{result.model.label}: {result.summary}"
    except _Cancelled:
        raise
    except AppException as exc:
        # Recorded on the step already; kept here so the payline step can say
        # *why* it has no symbols rather than only that it has none.
        run.reels_error = exc.message
    except Exception as exc:  # noqa: BLE001 - already recorded on the step
        run.reels_error = f"{type(exc).__name__}: {exc}"


# --- payline validation ---------------------------------------------------


def _geometry_line_set(view: PaytableView) -> payline_config.PaylineSet:
    """The lines the running game actually plays, as the payline service wants them."""
    set_id = view.win_geometry.payline_set_id or "lines"
    name = f"geometry-{set_id}"
    block = {
        name: {
            str(line.line): [list(pair) for pair in line.grid]
            for line in view.win_geometry.paylines
        }
    }
    return payline_config.read_set(block, name, where=f"{view.win_geometry.path}")


def _combo_for(view: PaytableView, symbol: str, pays: int) -> PaylineComboInfo | None:
    """The ``math.xml`` combo a run of ``pays`` of ``symbol`` matches."""
    for combo in view.math.payline_combos:
        if combo.match_length != pays:
            continue
        named = {code for code in combo.symbols if code != ANY_SYMBOL}
        if named == {symbol}:
            return combo
    return None


def _row_pay(view: PaytableView, symbol: str, pays: int) -> float | None:
    """What the pivoted paytable pays that symbol at that run length."""
    lengths = view.math.pay_lengths
    if pays not in lengths:
        return None
    index = lengths.index(pays)
    for row in view.math.pay_table:
        if symbol in row.codes and index < len(row.values):
            return row.values[index]
    return None


def _priced(
    view: PaytableView, symbol: str, pays: int
) -> tuple[PaylineComboInfo | None, float | None]:
    """The combo a run of ``pays`` of ``symbol`` matches, and what it is worth."""
    combo = _combo_for(view, symbol, pays)
    value = combo.value if combo is not None else _row_pay(view, symbol, pays)
    return combo, value


def _min_pay_length(view: PaytableView, symbol: str) -> int | None:
    """Shortest run this symbol pays at, or ``None`` if it never pays on a line."""
    lengths = [
        length
        for index, length in enumerate(view.math.pay_lengths)
        for row in view.math.pay_table
        if symbol in row.codes
        and index < len(row.values)
        and row.values[index] is not None
    ]
    return min(lengths) if lengths else None


def _award(
    view: PaytableView,
    check: PaylineCheckResult,
    elements: dict[str, list[list[int]]],
    bet_per_unit: int | None,
) -> list[SpinLineAward]:
    """Price every line the reels were read against."""
    labels = {symbol.code: symbol.name for symbol in view.math.symbols}
    wild = check.wild_symbol

    def named(code: str) -> str:
        """A symbol as a reader knows it, falling back to its bare code."""
        return labels.get(code) or code

    awards: list[SpinLineAward] = []
    for line in check.lines:
        symbols = list(line.symbols)
        # What the *run* pays as, which the payline check already settled: the
        # symbol the wilds stood in for, or the wild itself when every position
        # of the run was one. Deliberately not `symbols[0]` -- that is the tile
        # on reel 1, and a line that lands a wild there is not a line of wilds.
        symbol = line.symbol

        note: str | None = None
        combo: PaylineComboInfo | None = None
        combo_value: float | None = None
        shortest: int | None = None
        combo_pays: int | None = None
        if symbol is not None:
            shortest = _min_pay_length(view, symbol)
            combo, combo_value = _priced(view, symbol, line.pays)
            combo_pays = line.pays if combo_value is not None else None

            # A line that leads with wilds resolves two ways and the game pays
            # the better of them: as the symbol the wilds stood in for over the
            # whole run, or as the wild's own combo over just the leading wilds.
            # On FortuneOx four wilds then an Ox is five Ox (50 a bet unit) or
            # four wilds (100), so taking the substituted reading on its own
            # would halve a real win while looking certain about it.
            if wild is not None and symbol != wild and line.leading_wilds >= _MIN_RUN:
                other, value = _priced(view, wild, line.leading_wilds)
                if value is not None and (combo_value is None or value > combo_value):
                    note = (
                        f"{line.leading_wilds} x {named(wild)} pays {value:g} a "
                        f"bet unit, more than {line.pays} x {named(symbol)}"
                        + ("" if combo_value is None else f" at {combo_value:g}")
                    )
                    symbol, combo, combo_value = wild, other, value
                    combo_pays = line.leading_wilds
                    shortest = _min_pay_length(view, wild)

            if combo_value is None:
                # Cancelled: the reels really did land this run, and the maths
                # pays nothing for one this short. Said in the paytable's own
                # terms so the reason is checkable rather than a bare refusal.
                name = named(symbol)
                note = (
                    f"{name} pays from {shortest} on, so this run of {line.pays} "
                    "awards nothing"
                    if shortest is not None
                    else f"{name} has no line pay at any length, so this run "
                    "awards nothing"
                )

        # `awarded` is the maths' answer, so it is keyed on the paytable's own
        # figure and never on the priced one: a run the maths pays for is a win
        # whether or not this process was told what a unit cost. The stake
        # scales an award; it cannot create or cancel one.
        awarded = combo_value is not None
        credits = (
            None
            if combo_value is None or bet_per_unit is None
            else combo_value * bet_per_unit
        )

        awards.append(
            SpinLineAward(
                line=line.name,
                label=line.label,
                positions=list(line.positions),
                elements=elements.get(line.name, []),
                pays=line.pays,
                paying=line.paying,
                awarded=awarded,
                steps=list(line.steps),
                color=line.color,
                break_position=line.break_position,
                symbols=symbols,
                symbol=symbol,
                symbol_name=labels.get(symbol) if symbol is not None else None,
                leading_wilds=line.leading_wilds,
                combo_id=combo.combo_id if combo is not None else None,
                combo_symbols=list(combo.symbols) if combo is not None else [],
                combo_pays=combo_pays,
                combo_value=combo_value,
                credits=credits,
                min_pay_length=shortest,
                note=note,
                # A cancelled run's own picture *is* the pattern the paytable
                # does not pay, so it is not carried -- the codes on `steps` are
                # the part of that line still worth reading.
                image_data=line.image_data if awarded else None,
            )
        )
    return awards


def _summarise_awards(awards: list[SpinLineAward]) -> str:
    """What the spin is owed, in one sentence."""
    paid = [award for award in awards if award.awarded]
    if not paid:
        return "No line pays"
    return ", ".join(
        f"{award.label} pays {award.credits:g}"
        if award.credits is not None
        else f"{award.label} pays {award.combo_value:g} a bet unit"
        if award.combo_value is not None
        else f"{award.label} pays an unpriced award"
        for award in paid
    )


def _expected(
    run: _ActiveRun, view: PaytableView, awards: list[SpinLineAward]
) -> SpinExpectedAward:
    """What the paytable says this spin owed, and whether the meter agrees."""
    paying = [award for award in awards if award.awarded]
    credits = sum(award.credits or 0.0 for award in paying)

    line_count = view.win_geometry.line_count or (
        view.identity.number_of_lines if view.identity is not None else None
    )
    denomination = view.denomination
    rate = None if denomination is None else denomination.money_per_credit

    meter = run.meter
    readings = {} if meter is None else {r.frame: r for r in meter.readings}
    initial = readings.get(FRAME_INITIAL)
    outcome = readings.get(FRAME_OUTCOME)
    total_bet = None if initial is None else initial.bet
    # Credits mode is the case that needs no rate at all: the meter is already
    # counting the thing the paytable is denominated in.
    in_credits = meter is not None and meter.mode is MeterMode.CREDITS

    # Both sides of the comparison in both units, off the readings' own converted
    # figures rather than divided again here -- one conversion per run, done where
    # the mode and the rate were settled together.
    cash = None if rate is None else credits * rate
    observed_credits = None if outcome is None else outcome.credits.win
    observed_cash = None if outcome is None else outcome.cash.win

    # The bet in credits. Context rather than a step: what it prices is the
    # stake, and that was worked out at the meter step.
    bet_credits = None if initial is None else initial.credits.bet
    per_line = (
        None if bet_credits is None or not line_count else bet_credits / line_count
    )

    # Compare in whatever the glass was drawing, since that side was read and the
    # other is derived from it -- and it is the side whose tolerance means
    # something. `observed` falls back to the raw reading so a run with no
    # denomination is still graded in the one unit it does know.
    unit = SpinMeterUnit.CREDITS if in_credits else SpinMeterUnit.CASH
    # Without a bet per unit there is no award to compare: `credits` is 0 because
    # every line's own figure is a rate this run was never given a multiplier
    # for. Gated here rather than left to fall through, since 0 against an empty
    # meter would otherwise read as a spin that correctly paid nothing.
    staked = run.bet_per_unit
    owed = None if staked is None else (credits if in_credits else cash)
    observed = observed_credits if in_credits else observed_cash
    if observed is None and outcome is not None:
        observed = outcome.win
    tolerance = _tolerance(unit)
    if owed is None or observed is None:
        # A spin that paid nothing and a meter that shows nothing agree without
        # needing any of the conversion above.
        if not paying and observed is None and run.outcome is SpinOutcome.NO_WIN:
            verdict = SpinVerdict.PASSED
            detail = "No line pays, and the win meter is empty"
        elif staked is None:
            # The BET cell did not read, or the paytable never said what a
            # spin costs. Named before the denomination below because a paytable
            # value is a rate per bet unit, so without the number of units there
            # is nothing to compare however well the rest of the meter read.
            verdict = SpinVerdict.INDETERMINATE
            detail = (
                "The bet per unit could not be read off the BET cell, so each "
                "line's paytable value is a rate rather than an "
                "award"
            )
        elif denomination is not None and rate is None:
            # Reported but not priceable: a different failure from never reported,
            # and a different fix -- the paytable id has to name its unit.
            verdict = SpinVerdict.INDETERMINATE
            detail = (
                f"The denomination reads {denomination.value:g} but paytable "
                f"{view.paytable_id} names no unit for it, so credits cannot be "
                "priced in money"
            )
        else:
            # Short list on purpose: what is left to price the award is the
            # denomination and the win. The bet and the line count are not on it
            # -- the multiplier is the bet *per unit*, which is given rather than
            # read, and it was checked above.
            verdict = SpinVerdict.INDETERMINATE
            detail = (
                "Not enough was readable to price the spin: needs the "
                "denomination and the win off the meter"
            )
    else:
        verdict = (
            SpinVerdict.PASSED
            if _close(owed, observed, tolerance)
            else SpinVerdict.FAILED
        )
        priced = (
            "a credit meter is already counting them"
            if in_credits
            else f"at {rate:g} a credit on denom "
            f"{denomination.label if denomination else '?'}"
        )
        detail = (
            f"{len(paying)} line(s) pay {credits:g} credits at {staked} a bet "
            f"unit; {priced} that is {owed:.2f} in {unit.value}, and the meter "
            f"shows {observed:.2f}"
        )

    return SpinExpectedAward(
        paying_lines=len(paying),
        credits=round(credits, 4),
        bet_per_unit=staked,
        line_count=line_count,
        denomination_label=None if denomination is None else denomination.label,
        money_per_credit=rate,
        total_bet=total_bet,
        bet_credits=None if bet_credits is None else round(bet_credits, 4),
        credits_per_line=None if per_line is None else round(per_line, 6),
        cash=None if cash is None else round(cash, 4),
        unit=unit,
        observed_win=observed,
        observed_credits=observed_credits,
        observed_cash=observed_cash,
        verdict=verdict,
        detail=detail,
    )


async def _check_paylines(run: _ActiveRun, frame: SpinFrame) -> SpinPaylineValidation:
    """Check the *running* game's own lines against the symbols that were read."""
    view = run.paytable
    reels = run.reels
    if view is None:
        return SpinPaylineValidation(
            frame=frame.file_name,
            paytable_id="",
            paytable_origin="unread",
            error=(
                run.paytable_error
                or "The paytable was never read, so there are no lines to check"
            ),
        )

    geometry = view.win_geometry

    def unchecked(reason: str) -> SpinPaylineValidation:
        """The validation as it reads when the lines could not be checked, still naming
        which paytable and which set."""
        return SpinPaylineValidation(
            frame=frame.file_name,
            paytable_id=view.paytable_id,
            paytable_origin=view.source.origin,
            payline_set_id=geometry.payline_set_id,
            resolved_from=geometry.resolved_from,
            line_count=geometry.line_count,
            min_confidence=reels.min_confidence if reels is not None else None,
            pay_lengths=list(view.math.pay_lengths),
            error=reason,
        )

    if reels is None:
        # No fallback on purpose: cosine similarity would answer a different
        # question (which tiles are alike) and could not name the symbol this
        # award is priced from. See the module docstring.
        return unchecked(
            run.reels_error
            or "The reels were never read, so there are no symbols to line up"
        )
    if geometry.error is not None:
        return unchecked(geometry.error)
    if not geometry.paylines:
        return unchecked(
            f"{geometry.path} declares no lines for payline set "
            f"{geometry.payline_set_id!r}, so there is nothing to check"
        )

    try:
        line_set = _geometry_line_set(view)
    except payline_config.PaylineError as exc:
        return unchecked(str(exc))

    check = await paylines_service.check_symbols(
        line_set,
        run.symbols,
        split=reels.split,
        images=paylines_service.ImageOptions(overlay=True, lines="paying"),
    )

    elements = {
        str(line.line): [list(pair) for pair in line.elements]
        for line in geometry.paylines
    }
    awards = _award(view, check, elements, run.bet_per_unit)

    # The check drew every run it found, because it had no paytable to ask. Now
    # that there is one, the picture is redrawn over the lines that actually pay
    # and replaces it -- a cancelled run traced across the reels is the same
    # claim of a win the numbers just withdrew. Skipped when nothing was
    # cancelled, which is the common case and already correct.
    overlay = check.overlay_image
    output_dir, output_file = check.output_dir, check.output_file
    awarded = {award.line for award in awards if award.awarded}
    if awarded != {line.name for line in check.lines if line.paying}:
        redrawn = await paylines_service.redraw(check, awarded)
        overlay = redrawn.image_data
        output_dir, output_file = redrawn.output_dir, redrawn.output_file

    return SpinPaylineValidation(
        frame=frame.file_name,
        paytable_id=view.paytable_id,
        paytable_origin=view.source.origin,
        payline_set_id=geometry.payline_set_id,
        resolved_from=geometry.resolved_from,
        line_count=geometry.line_count,
        min_confidence=reels.min_confidence,
        wild_symbol=check.wild_symbol,
        pay_lengths=list(view.math.pay_lengths),
        split=check.source.split,
        summary=_summarise_awards(awards),
        runs_found=sum(1 for award in awards if award.paying),
        awarded_lines=sum(1 for award in awards if award.awarded),
        unnamed_positions=[tile.name for tile in reels.tiles if not tile.known],
        lines=awards,
        stats=check.stats,
        # Priced here, where the awards are made: the meter reads before this
        # step, so what the glass showed and what a bet unit cost are both known
        # by now. This used to be patched in from the meter step afterwards.
        expected=_expected(run, view, awards),
        output_dir=output_dir,
        output_file=output_file,
        overlay_image=overlay,
    )


async def _validate_paylines(run: _ActiveRun) -> None:
    """Check the lines the running game declares against the symbols that landed."""
    try:
        async with _step(run, STEP_PAYLINES) as step:
            frame = run.frame(FRAME_OUTCOME)
            if frame is None:
                raise SpinAnalysisUnavailableError(
                    "No result screenshot was taken, so there are no reels to check"
                )
            validation = await _check_paylines(run, frame)
            run.paylines = validation
            if validation.error is not None:
                raise SpinAnalysisUnavailableError(validation.error)
            step.detail = validation.summary
    except _Cancelled:
        raise
    except Exception:  # noqa: BLE001 - already recorded on the step
        return


# --- the run --------------------------------------------------------------


def _finish_run(run: _ActiveRun, state: SpinRunState, message: str) -> None:
    """Close the run off, and leave it as the state the service reports."""
    run.state = state
    run.message = message
    run.finished_at = datetime.now()
    _write_manifest(run)
    _publish(run)
    logger.info(
        "Spin %s %s (%s): %s", run.run_id, state.value, run.outcome.value, message
    )


def _write_manifest(run: _ActiveRun) -> None:
    """Persist the run's record atomically. Never raises -- losing the write is
    better than losing the answer that was already returned over the stream."""
    target = run.directory / MANIFEST_NAME
    temporary = target.with_name(f".{MANIFEST_NAME}.tmp")
    detail = _detail(run, images=False)
    try:
        temporary.write_text(
            detail.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8"
        )
        temporary.replace(target)
    except OSError:
        logger.exception("Could not write the spin manifest at %s", target)
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


async def _execute(run: _ActiveRun) -> None:
    """The whole sequence, in order. Records how it went; never raises."""
    try:
        await _prepare(run)
        await _start_recording(run)
        await _capture(run, FRAME_INITIAL, STEP_FRAME_INITIAL)
        reader = await _spin(run)
        await _wait_reels(run, reader)
        await _detect_win(run, reader)

        if run.outcome is SpinOutcome.WIN:
            await _capture(run, FRAME_OUTCOME, STEP_FRAME_OUTCOME)
            # Between the result screenshot and collecting it: the presentation
            # these are a close-up of is on screen for exactly that window, and
            # take-win ends it. Not a step -- see `_record_tile_clips`.
            await _record_tile_clips(run)
            await _take_win(run)
            await _capture(run, FRAME_COLLECTED, STEP_FRAME_COLLECTED)
        else:
            await _capture(run, FRAME_OUTCOME, STEP_FRAME_OUTCOME)
            _skip(run, STEP_TAKE_WIN, "Nothing was won, so there was nothing to take")
            _skip(run, STEP_FRAME_COLLECTED, "Nothing was collected to photograph")

        await _stop_recording(run)
        # The meter reads *first* of the three, because it is the only one of
        # them that produces an input to another: which unit the strip drew and
        # what was staked on a bet unit are what turn a line's paytable rate into
        # an award. It used to read last, which meant the lines were priced
        # before the stake was known and had to be re-priced afterwards.
        await _validate_meter(run)
        await _read_reels(run)
        await _validate_paylines(run)
    except _Cancelled:
        await _stop_recording_quietly(run)
        _finish_run(run, SpinRunState.CANCELLED, "Cancelled")
        return
    except AppException as exc:
        await _stop_recording_quietly(run)
        _finish_run(run, SpinRunState.FAILED, exc.message)
        return
    except Exception as exc:  # noqa: BLE001 - a run records its own crash
        # A bug in the orchestration must still leave OBS not recording and the
        # record sealed, so nothing here is allowed to escape into the task.
        await _stop_recording_quietly(run)
        _finish_run(run, SpinRunState.FAILED, f"{type(exc).__name__}: {exc}")
        return

    failed = [step for step in run.steps.values() if step.state is SpinStepState.FAILED]
    if failed:
        # The spin itself finished -- one of the validations could not run. That
        # is a failed run with a usable record, not a lost one.
        _finish_run(
            run,
            SpinRunState.FAILED,
            f"The spin completed but {len(failed)} step(s) failed: "
            + ", ".join(step.label for step in failed),
        )
        return
    _finish_run(run, SpinRunState.COMPLETED, _summary(run))


def _summary(run: _ActiveRun) -> str:
    """The finished run in one line: what the spin did, and what the two
    validations made of it."""
    parts = [
        "The spin won" if run.outcome is SpinOutcome.WIN else "The spin paid nothing"
    ]
    if run.paylines is not None and run.paylines.expected is not None:
        parts.append(f"paylines {run.paylines.expected.verdict.value}")
    if run.meter is not None:
        parts.append(f"cash meter {run.meter.verdict.value}")
    return "; ".join(parts)


# --- public API -----------------------------------------------------------


async def start(
    *, record: bool | None = None, architecture: str | None = None
) -> SpinAnalysisState:
    """Drive one spin, and validate it."""
    global _run
    record_enabled = settings.ANALYZE_SPIN_RECORD if record is None else record
    chosen = classifier_service.resolve_architecture(
        architecture or settings.analyze_spin_classifier_architecture
    )
    async with _get_lock():
        current = _run
        if current is not None and current.state is SpinRunState.RUNNING:
            raise SpinAnalysisAlreadyRunningError(
                f"Spin {current.run_id} is still in progress ({current.message}); "
                "wait for it or cancel it first"
            )

        name, config = _active_config()
        log_path = _log_for(name, config)

        started = datetime.now()
        run_id = started.strftime(_RUN_ID_FORMAT)
        relative = f"{settings.ANALYZE_SPIN_DIR_NAME}/{run_id}"
        directory = resolve_subdirectory(settings.obs_capture_dir, relative)
        directory.mkdir(parents=True, exist_ok=True)

        recording_steps = {STEP_RECORD_START, STEP_RECORD_STOP}
        run = _ActiveRun(
            run_id=run_id,
            game=name,
            label=config.name,
            directory=directory,
            recording_dir=relative,
            log_path=log_path,
            rules=game_log.resolve_rules(
                extra=config.event_rules, disabled=config.disabled_events
            ),
            started_at=started,
            architecture=chosen,
            record=record_enabled,
            steps={
                key: _StepRecord(key=key, label=label)
                for key, label in _SEQUENCE
                if record_enabled or key not in recording_steps
            },
        )
        _run = run
        run.task = asyncio.create_task(_execute(run), name=f"analyze-spin-{run_id}")

    logger.info(
        "Spin %s started for %s, reading its reels with %s",
        run.run_id,
        run.game,
        run.architecture,
    )
    return _snapshot(run, images=False)


async def cancel() -> SpinAnalysisState:
    """Ask the run in progress to stop."""
    run = _run
    if run is None or run.state is not SpinRunState.RUNNING:
        raise SpinAnalysisNotRunningError(
            "No spin analysis is in progress, so there is nothing to cancel"
        )
    run.cancel_requested = True
    run.message = "Cancelling"
    _publish(run)
    return _snapshot(run, images=False)


def frame_path(file_name: str) -> Path:
    """One of a run's screenshots, resolved for serving."""
    return roi_service.resolve_frame(file_name)


def clip_path(run_id: str, file_name: str) -> Path:
    """One run's per-tile clip, resolved for serving."""
    try:
        root = resolve_subdirectory(
            settings.obs_capture_dir, settings.ANALYZE_SPIN_DIR_NAME
        )
        directory = (
            resolve_within(root, run_id) / settings.ANALYZE_SPIN_TILE_CLIP_DIR_NAME
        )
        target = resolve_within(directory, file_name)
    except UnsafeNameError as exc:
        raise SpinClipNotFoundError(f"Invalid tile clip name: {exc.reason}") from exc
    if not target.is_file():
        raise SpinClipNotFoundError(
            f"Spin {run_id!r} has no tile clip named {file_name!r}"
        )
    return target


def state(*, images: bool = False) -> SpinAnalysisState:
    """The run in progress, or the last one that finished. Never fails."""
    return _snapshot(_run, images=images)


async def abort() -> None:
    """End any run because the process is shutting down."""
    run = _run
    if run is None or run.state is not SpinRunState.RUNNING:
        return
    run.cancel_requested = True
    task = run.task
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if run.state is SpinRunState.RUNNING:
        await _stop_recording_quietly(run)
        _finish_run(run, SpinRunState.CANCELLED, "Interrupted by shutdown")


async def reset() -> None:
    """Drop all state, including the lock bound to this loop. Tests only."""
    global _run, _lock
    run, _run = _run, None
    if run is not None:
        run.cancel_requested = True
        task = run.task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    _subscribers.clear()
    _lock = None
