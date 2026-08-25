"""Analyze Spin: drives one spin end to end, then validates what it produced.

Every piece of this already existed -- OBS records and screenshots, the i-deck
presses, the game log names events, ROI reads the meter, the grid splits the
reels, the payline service compares tiles, the paytable service joins a running
game to its maths. This module is the *order* they go in, and nothing else. It
owns no image handling, no XML, no win32.

Five things about the sequence are worth knowing before reading it:

**A losing spin is proven by silence.** The game logs a win meter count-up
(``win-collected``) when there is something to collect and logs nothing at all
when there is not, so "no win" is the absence of a line within
``ANALYZE_SPIN_WIN_WAIT_SECONDS``. That wait is therefore paid in full on every
losing spin, and setting it too short reports a win as a loss -- the one
mis-tuning here that produces a confidently wrong answer rather than a timeout.

**A confirmed press is not a spin.** :mod:`app.services.ideck` proves the panel
registered the key; the deck's layout belongs to the cabinet, so a key the
panel confirms may be one this game binds nothing to. The spin step therefore
waits for the game's own ``SpinButtonMsg`` as well, and says which half failed.

**The steps exist before they run.** A run is created with its whole sequence
``pending``, so a failure on step four leaves the six steps after it visibly
unreached rather than simply absent -- and take-win on a losing spin is
``skipped``, which is a different fact from not having got there.

**An empty frame is caught, not accepted.** OBS renders nothing for a moment
after its window-capture source is re-pointed, and reports a perfectly
successful write of the black frame that comes out. So a capture is read back
rather than trusted, retried, and then reported -- a blank frame fails its own
step without ending the run, because every reading taken off it is meaningless
rather than merely dark.

**Cancellation is cooperative, not a task cancellation.** Every wait polls, and
every poll checks the flag, so a cancelled run unwinds through its own code:
the recording gets stopped, the manifest gets written, and no ``finally`` has to
run under a pending ``CancelledError``. The cost is that a cancel lands only
once whatever call is in flight returns (a Tesseract read, an OBS request).

The two validations at the end are deliberately independent of each other and
neither can fail the other: a machine with no OCR engine still gets its payline
check, and a machine without the game installed still gets its meter arithmetic.

The payline half of that reads two sources and keeps them strictly apart. **The
picture decides what paid** -- cosine similarity between the split's tiles, via
:func:`app.services.paylines.check_lines` -- and **the game's logged reel stops
only name the symbols** of the runs it found, via :mod:`app.utils.reel_stops`,
which is the one thing similarity cannot say and what makes an award exact
instead of a range. Taking the run length from the log instead would make the
check agree with the game by construction.
"""

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
from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    AppException,
    GameConfigInvalidError,
    SpinAnalysisAlreadyRunningError,
    SpinAnalysisNotRunningError,
    SpinAnalysisUnavailableError,
)
from app.schemas.analyze_spin import (
    SpinAnalysisState,
    SpinExpectedAward,
    SpinFrame,
    SpinLineAward,
    SpinLineAwardCandidate,
    SpinLogEvent,
    SpinMeterCheck,
    SpinMeterReading,
    SpinMeterValidation,
    SpinOutcome,
    SpinPaylineValidation,
    SpinRecording,
    SpinRun,
    SpinRunState,
    SpinStep,
    SpinStepState,
    SpinVerdict,
)
from app.schemas.grid import GridSplitRequest
from app.schemas.obs import ScreenshotRequest
from app.schemas.paylines import PaylineCheckResult
from app.schemas.paytable import PaylineComboInfo, PaytableView
from app.schemas.roi import RoiExtractRequest
from app.services import game_input as game_input_service
from app.services import grid as grid_service
from app.services import ideck as ideck_service
from app.services import obs as obs_service
from app.services import paylines as paylines_service
from app.services import paytable as paytable_service
from app.services import roi as roi_service
from app.utils import game_log, reel_stops
from app.utils import paylines as payline_config
from app.utils.game_math import ANY_SYMBOL
from app.utils.log_tail import LogTail
from app.utils.paths import resolve_subdirectory

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
    (STEP_METER, "Validate the cash meter"),
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

    paytable: PaytableView | None = None
    paytable_error: str | None = None

    meter: SpinMeterValidation | None = None
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
    """The game's log, checked to be there. Refused up front rather than
    mid-spin: without it there is no way to know the reels ever stopped, and a
    run that pressed spin and then gave up is worse than one that never
    pressed."""
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
    """A one-way read over the game's log, from where the run opened it.

    Buffers whole lines rather than handing back the first match in a chunk: one
    read can carry the reels stopping *and* the win counting up, and returning
    at the first while advancing the cursor past the second is exactly how a
    winning spin would come back as a loss.
    """

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

        # Where the reels landed, taken off a line no rule claims. Kept here
        # rather than waited for, because it is written *before* the stop
        # animation and would otherwise be read past and thrown away while
        # waiting for the reels-stopped transition that follows it.
        self.stops: tuple[int, ...] | None = None
        self.stops_line: str | None = None
        self.stops_error: str | None = None

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
            self._note_stops(line)
            found = game_log.match(line, self._rules)
            if found is not None:
                return found
        return None

    def _note_stops(self, line: game_log.LogLine) -> None:
        """Remember the reel stops, if this is the line that carries them.

        The newest wins: a free-spin sequence logs its own stops, and the last
        set before the reels were photographed is the one the picture shows.
        """
        found = game_log.REEL_STOPS.search(line.message)
        if found is None:
            return
        try:
            self.stops = reel_stops.parse_stops(found.group("stops"))
        except reel_stops.ReelStopError as exc:
            self.stops_error = str(exc)
            return
        self.stops_line = line.raw
        self.stops_error = None


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
    """The record both the manifest and the API return.

    ``images`` is what separates the progress stream from the report: a snapshot
    goes out on every step transition and every recognised log line, and forty
    line pictures per push would make the stream the slowest part of a spin.
    """
    finished = run.finished_at or datetime.now()
    meter = run.meter
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
        meter=(meter if images or meter is None else _lean_meter(meter)),
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
    """Hand one snapshot to one subscriber, dropping the oldest if it is behind.
    A subscriber only ever wants the newest state, so a full queue is a reason
    to discard history rather than to block the run."""
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
    """Run one step of the sequence, recording how it went either way.

    The body sets :attr:`_StepRecord.detail` to whatever it found; everything
    else -- timing, state, the error and its code, the progress push -- happens
    here so no step can forget it.

    A body that sets :attr:`_StepRecord.error` without raising is recorded as
    failed and lets the run carry on. That is for the step that did its work and
    knows the result is unusable -- a screenshot OBS wrote empty -- where
    aborting would throw away the eight things that would still have worked.
    """
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
    """Wait for the game to log one of ``wanted``, or give up.

    Everything recognised on the way is recorded on the run, so the timeline
    shows what the game *was* doing while a wait ran out -- which is the
    difference between "the reels never stopped" and "the reels stopped and a
    bonus took over".
    """
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
    """Get OBS pointed at the game, and read the maths this spin will play by.

    The paytable is read *before* the spin on purpose: it is what the game had
    loaded when the reels turned, and a denomination change mid-run would
    otherwise have the validation grading a spin against the wrong maths. Its
    failure is carried rather than raised -- the game not being installed on
    this machine costs the payline validation, not the spin.
    """
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
    if not settings.ANALYZE_SPIN_RECORD:
        _skip(run, STEP_RECORD_START, "ANALYZE_SPIN_RECORD is off")
        return
    async with _step(run, STEP_RECORD_START) as step:
        await obs_service.start_recording(run.recording_dir)
        run.recording_started = True
        step.detail = f"Recording into {run.recording_dir}"


async def _stop_recording(run: _ActiveRun) -> None:
    """End the video. Idempotent, so the same call serves the happy path and the
    tidy-up after a failure -- a run that died mid-spin must not leave OBS
    recording."""
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
    """Stop the recording without letting the attempt raise. For the paths where
    the run is already over -- a failure, a cancel, a shutdown -- and leaving
    OBS running is the worse of the two outcomes."""
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
    """Take one screenshot of this moment.

    Written into the dashboard's own screenshot directory rather than a
    per-run one, because that is where :mod:`app.services.roi` and
    :mod:`app.services.grid` read a frame *by name* -- putting them anywhere
    else would mean neither validation could open them.
    """
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
    """Press the spin key, and wait for the game to agree that it spun.

    The reader is opened *before* the press so its cursor predates it; a reader
    opened after would start reading past the very line it is waiting for.
    """
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
    """Decide whether this spin paid, by whether the win meter counts up.

    There is no line saying a spin lost, so the absence of one is the answer --
    which is why this step reports the wait it made rather than only its verdict.
    """
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


async def _read_meter(frame: SpinFrame) -> SpinMeterReading:
    """Read the meter off one frame. Never raises for a bad *reading* -- ROI
    carries that as ``meter.error`` -- only for a frame or region it cannot
    reach at all."""
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


def _close(left: float, right: float, tolerance: float) -> bool:
    """Whether two amounts are the same number to the precision they were read at."""
    return abs(left - right) <= tolerance


def _amount(value: float | None) -> str:
    """One amount for a detail line, or a placeholder when it could not be read."""
    return "unreadable" if value is None else f"{value:.2f}"


def _relation(
    key: str,
    label: str,
    *,
    expected: float | None,
    actual: float | None,
    tolerance: float,
    detail: str,
) -> SpinMeterCheck:
    """One expected-versus-actual comparison, indeterminate if either is missing."""
    if expected is None or actual is None:
        return SpinMeterCheck(
            key=key,
            label=label,
            verdict=SpinVerdict.INDETERMINATE,
            expected=expected,
            actual=actual,
            detail=detail,
        )
    return SpinMeterCheck(
        key=key,
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


def _meter_checks(
    run: _ActiveRun, readings: list[SpinMeterReading], tolerance: float
) -> list[SpinMeterCheck]:
    """What the differences between the frames' meters ought to be.

    Each relation is stated as arithmetic over two readings rather than as a
    rule about the game, because that is what a reader can check by eye against
    the crops beside it: the bet leaves the balance when the reels turn, and the
    win joins it when it is collected.
    """
    by_frame = {reading.frame: reading for reading in readings}
    initial = by_frame.get(FRAME_INITIAL)
    outcome = by_frame.get(FRAME_OUTCOME)
    collected = by_frame.get(FRAME_COLLECTED)
    checks: list[SpinMeterCheck] = []

    if initial is not None and outcome is not None:
        checks.append(
            _relation(
                "bet-stable",
                "The bet is unchanged by the spin",
                expected=initial.bet,
                actual=outcome.bet,
                tolerance=tolerance,
                detail=(
                    f"bet was {_amount(initial.bet)} before the spin and "
                    f"{_amount(outcome.bet)} after it"
                ),
            )
        )
        expected = (
            None
            if initial.balance is None or initial.bet is None
            else initial.balance - initial.bet
        )
        checks.append(
            _relation(
                "bet-deducted",
                "The bet came off the balance",
                expected=expected,
                actual=outcome.balance,
                tolerance=tolerance,
                detail=(
                    f"{_amount(initial.balance)} - {_amount(initial.bet)} = "
                    f"{_amount(expected)}, and the balance reads "
                    f"{_amount(outcome.balance)}"
                ),
            )
        )

    if outcome is not None:
        won = run.outcome is SpinOutcome.WIN
        registered = outcome.win is not None and outcome.win > 0
        if outcome.error is not None:
            verdict = SpinVerdict.INDETERMINATE
        else:
            verdict = SpinVerdict.PASSED if registered == won else SpinVerdict.FAILED
        checks.append(
            SpinMeterCheck(
                key="win-registered" if won else "win-empty",
                label=(
                    "The win meter shows an amount"
                    if won
                    else "The win meter stayed empty"
                ),
                verdict=verdict,
                actual=outcome.win,
                detail=(
                    f"the game logged {'a win' if won else 'no win'} and the WIN "
                    f"cell reads {'nothing' if outcome.win is None else _amount(outcome.win)}"
                ),
            )
        )

    if outcome is not None and collected is not None:
        expected = (
            None
            if outcome.balance is None or outcome.win is None
            else outcome.balance + outcome.win
        )
        checks.append(
            _relation(
                "win-collected",
                "The win went onto the balance",
                expected=expected,
                actual=collected.balance,
                tolerance=tolerance,
                detail=(
                    f"{_amount(outcome.balance)} + {_amount(outcome.win)} = "
                    f"{_amount(expected)}, and the balance reads "
                    f"{_amount(collected.balance)}"
                ),
            )
        )
        # Deliberately no "the win meter cleared" check: these games leave the
        # last win on the WIN cell after it has been collected, so an empty one
        # would be the surprise. The balance moving is the proof of collection,
        # and that is the check above.

    return checks


def _verdict_of(verdicts: list[SpinVerdict]) -> SpinVerdict:
    """The worst of several verdicts, with 'failed' ranking below
    'indeterminate' -- a real discrepancy is not softened by an unreadable
    neighbour."""
    if not verdicts:
        return SpinVerdict.INDETERMINATE
    if SpinVerdict.FAILED in verdicts:
        return SpinVerdict.FAILED
    if SpinVerdict.INDETERMINATE in verdicts:
        return SpinVerdict.INDETERMINATE
    return SpinVerdict.PASSED


async def _validate_meter(run: _ActiveRun) -> None:
    """Read the meter off every screenshot the run took, and check the
    arithmetic between them. Never fails the run: the payline validation after
    it is independent, and a machine with no OCR engine should still get one."""
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

            checks = _meter_checks(run, readings, tolerance)
            validation = SpinMeterValidation(
                readings=readings,
                checks=checks,
                tolerance=tolerance,
                verdict=_verdict_of([check.verdict for check in checks]),
                error=failure,
            )
            run.meter = validation
            if failure is not None:
                raise SpinAnalysisUnavailableError(failure)
            step.detail = (
                f"{len(readings)} frames read, {len(checks)} checks: "
                f"{validation.verdict.value}"
            )
    except _Cancelled:
        raise
    except Exception:  # noqa: BLE001 - already recorded on the step
        return


# --- payline validation ---------------------------------------------------


def _geometry_line_set(view: PaytableView) -> payline_config.PaylineSet:
    """The lines the running game actually plays, as the payline service wants them.

    ``winGeometry.xml`` writes a line as ``[reel, position]`` 0-indexed and the
    payline service reads ``[row, column]`` 1-indexed;
    :meth:`app.utils.win_geometry.Payline.grid` has already converted, and the
    paytable response carries both. Going back through
    :func:`app.utils.paylines.read_set` rather than building the dataclasses
    directly is deliberate: that is where a line is checked to run left to
    right, and a line that backtracks would otherwise compare a tile with
    itself and score a perfect match.
    """
    set_id = view.win_geometry.payline_set_id or "lines"
    name = f"geometry-{set_id}"
    block = {
        name: {
            str(line.line): [list(pair) for pair in line.grid]
            for line in view.win_geometry.paylines
        }
    }
    return payline_config.read_set(block, name, where=f"{view.win_geometry.path}")


def _candidates(
    view: PaytableView, pays: int
) -> tuple[list[SpinLineAwardCandidate], str | None]:
    """Every paytable row that pays for a run this long, and why there is none.

    What the *picture alone* narrows an award to. A list rather than one row
    because similarity says the tiles of a run are alike and never which symbol
    they are -- the logged reel stops are what closes that gap, in
    :func:`_award`, and this is kept beside the answer so the narrowing is
    visible rather than assumed.
    """
    lengths = view.math.pay_lengths
    if pays not in lengths:
        return [], f"the paytable pays nothing for a run of {pays}"
    index = lengths.index(pays)
    found: list[SpinLineAwardCandidate] = []
    for row in view.math.pay_table:
        value = row.values[index] if index < len(row.values) else None
        if value is None:
            continue
        found.append(
            SpinLineAwardCandidate(
                codes=list(row.codes), names=list(row.names), value=value
            )
        )
    if not found:
        return [], f"no symbol in the paytable pays at a run of {pays}"
    return found, None


def _base_strips(view: PaytableView) -> tuple[list[list[str]], str | None]:
    """The base game's reel strips, in reel order, for indexing a stop into.

    Refuses a truncated strip rather than indexing into a short one: a stop is an
    index, and ``PAYTABLE_MAX_STRIP_STOPS`` cutting a strip short would move
    every symbol after the cut without anything looking wrong.
    """
    default = next(
        (group for group in view.math.reel_strip_sets if group.is_default), None
    )
    if default is None or not default.strip_ids:
        return [], (
            "math.xml names no default reel strip set, so a logged stop index "
            "has no strip to look up"
        )
    by_id = {strip.identifier: strip for strip in view.math.reel_strips}
    strips: list[list[str]] = []
    for identifier in default.strip_ids:
        strip = by_id.get(identifier)
        if strip is None:
            return [], (
                f"reel strip {identifier!r} of set {default.identifier!r} is not "
                "in the maths"
            )
        if strip.truncated:
            return [], (
                f"reel strip {identifier!r} was cut short at "
                "PAYTABLE_MAX_STRIP_STOPS, so its stop indices cannot be trusted"
            )
        strips.append(list(strip.symbols))
    return strips, None


def _measured_pairs(check: PaylineCheckResult) -> list[tuple[str, str, bool]]:
    """Every distinct tile pair similarity compared, and what it decided.

    Distinct because lines share pairs, and a pair two lines run through is one
    measurement -- counting it twice would weight the middle row of the grid when
    choosing which row a stop index refers to.
    """
    seen: dict[tuple[str, str], bool] = {}
    for line in check.lines:
        for step in line.steps:
            key = (
                (step.left, step.right)
                if step.left <= step.right
                else (step.right, step.left)
            )
            seen.setdefault(key, step.matched)
    return [(left, right, matched) for (left, right), matched in seen.items()]


def _resolve_stops(
    view: PaytableView, check: PaylineCheckResult, stops: tuple[int, ...]
) -> tuple[reel_stops.Resolution | None, str | None]:
    """Name the symbols this spin put on screen, or say why they cannot be named.

    Never raises into the run: naming the symbols makes an award exact, and
    failing to name them leaves it a range -- a worse answer, not a broken one.
    """
    strips, problem = _base_strips(view)
    if problem is not None:
        return None, problem
    configured = settings.ANALYZE_SPIN_REEL_STOP_ANCHOR
    try:
        resolution = reel_stops.resolve(
            stops,
            strips,
            rows=check.source.rows,
            pairs=_measured_pairs(check),
            anchor=None if configured == "auto" else configured,
        )
    except reel_stops.ReelStopError as exc:
        return None, str(exc)
    return resolution, None


def _combo_for(view: PaytableView, symbol: str, pays: int) -> PaylineComboInfo | None:
    """The ``math.xml`` combo a run of ``pays`` of ``symbol`` matches.

    This is the "match it against the combo" step: a line combo is one symbol
    repeated with an ``ANY`` tail, so a run matches when the combo's own match
    length is the run's and every symbol it names is that one.
    """
    for combo in view.math.payline_combos:
        if combo.match_length != pays:
            continue
        named = {code for code in combo.symbols if code != ANY_SYMBOL}
        if named == {symbol}:
            return combo
    return None


def _row_pay(view: PaytableView, symbol: str, pays: int) -> float | None:
    """What the pivoted paytable pays that symbol at that run length.

    The fallback for a run no single combo matched -- the pivot merges symbols
    paying alike, so it answers where a one-to-one combo lookup does not.
    """
    lengths = view.math.pay_lengths
    if pays not in lengths:
        return None
    index = lengths.index(pays)
    for row in view.math.pay_table:
        if symbol in row.codes and index < len(row.values):
            return row.values[index]
    return None


def _min_pay_length(view: PaytableView, symbol: str) -> int | None:
    """Shortest run this symbol pays at, or ``None`` if it never pays on a line.

    What a cancelled run is measured against: the picture can find two of a
    symbol whose paytable row starts at three, and saying "pays from 3" is the
    difference between a verdict and a bare refusal.
    """
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
    grid: reel_stops.SymbolGrid | None,
) -> list[SpinLineAward]:
    """Price every line similarity evaluated.

    Three separate judgements, kept separate on purpose:

    * **what landed** -- ``pays``, the leading run of tiles cosine similarity
      found alike, with the scores it read on ``steps``. This is the only thing
      measured off the picture, and taking it from the log instead would make
      the check agree with the game by construction.
    * **what it was** -- ``symbol``, from the reel stops the game logged. The one
      thing similarity cannot say about a run it found.
    * **whether it pays** -- ``awarded``, which is the paytable's answer and
      nobody else's. A run of two of a symbol that pays from three is a real run
      and no win, and reporting it as a win because the tiles matched is exactly
      the mistake this separation prevents. ``app.services.paylines`` deliberately
      stops at "two or more"; this is the module that has the paytable.
    """
    labels = {symbol.code: symbol.name for symbol in view.math.symbols}
    awards: list[SpinLineAward] = []
    for line in check.lines:
        candidates: list[SpinLineAwardCandidate] = []
        note: str | None = None
        if line.paying:
            candidates, note = _candidates(view, line.pays)

        symbols = grid.along(line.positions) if grid is not None else []
        run_from_stops = grid.leading_run(line.positions) if grid is not None else 0
        agrees = None if grid is None else run_from_stops == line.pays

        symbol: str | None = None
        combo: PaylineComboInfo | None = None
        credits: float | None = None
        shortest: int | None = None
        if line.paying and symbols and symbols[0] is not None:
            symbol = symbols[0]
            shortest = _min_pay_length(view, symbol)
            combo = _combo_for(view, symbol, line.pays)
            credits = (
                combo.value if combo is not None else _row_pay(view, symbol, line.pays)
            )
            if credits is None:
                # Cancelled: the tiles really do match, and the maths pays
                # nothing for a run this short. Said in the paytable's own terms
                # so the reason is checkable rather than a bare refusal.
                name = labels.get(symbol) or symbol
                note = (
                    f"{name} pays from {shortest} on, so this run of {line.pays} "
                    "awards nothing"
                    if shortest is not None
                    else f"{name} has no line pay at any length, so this run "
                    "awards nothing"
                )

        # Awarded is the paytable's answer: a known symbol has to have a pay at
        # this length, and an unknown one has to have at least one row that
        # could. Either way a run the maths does not pay is not a win.
        awarded = bool(line.paying) and (
            credits is not None if symbol is not None else bool(candidates)
        )

        values = [candidate.value for candidate in candidates]
        if credits is not None:
            value_min: float | None = credits
            value_max: float | None = credits
            exact = True
        elif awarded:
            value_min = min(values) if values else None
            value_max = max(values) if values else None
            exact = bool(values) and len(set(values)) == 1
        else:
            # Nothing is owed, so nothing is offered -- leaving the candidates
            # priced would read as an award this line could have earned.
            value_min = None
            value_max = None
            exact = False

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
                candidates=candidates,
                symbols=list(symbols),
                symbol=symbol,
                symbol_name=labels.get(symbol) if symbol is not None else None,
                run_from_stops=run_from_stops,
                agrees=agrees,
                combo_id=combo.combo_id if combo is not None else None,
                combo_symbols=list(combo.symbols) if combo is not None else [],
                credits=credits,
                value_min=value_min,
                value_max=value_max,
                exact=exact,
                min_pay_length=shortest,
                note=note,
                # A cancelled run's own picture *is* the pattern the paytable
                # does not pay, so it is not carried -- the scores on `steps`
                # are the part of that line still worth reading.
                image_data=line.image_data if awarded else None,
            )
        )
    return awards


def _summarise_awards(awards: list[SpinLineAward]) -> str:
    """What the spin is owed, in one sentence.

    Written here rather than reused from the payline check, because that check
    reports the runs it *found* and only the paytable knows which of them pay --
    a summary reading "Line 2 pays 2" for a run the maths awards nothing for is
    the confusion this replaces.

    "Pays" is credits throughout, never the run length: the two are different
    numbers and a sentence that used the word for both is exactly how a run of
    five gets read as five credits. The run length is ``pays`` on the line and is
    spoken of as *matching*.

    Cancelled runs are counted on ``runs_found``/``awarded_lines`` and explained
    on their own line's ``note``; they are deliberately not in this sentence,
    which is about what the spin owes.
    """
    paid = [award for award in awards if award.awarded]
    if not paid:
        return "No line pays"
    return ", ".join(
        f"{award.label} pays {award.credits:g}"
        if award.credits is not None
        else f"{award.label} pays {award.value_min:g}-{award.value_max:g}"
        if award.value_min is not None and award.value_max is not None
        else f"{award.label} pays an unpriced award"
        for award in paid
    )


def _number(value: str | None) -> float | None:
    """A denomination as the log wrote it, or ``None`` when it is unusable."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _expected(
    run: _ActiveRun, view: PaytableView, awards: list[SpinLineAward]
) -> SpinExpectedAward:
    """What the paytable says this spin owed, and whether the meter agrees.

    The conversion from paytable credits to money on the glass is spelled out
    field by field rather than collapsed into one number, because it runs through
    the denomination and the line count and a wrong verdict is nearly always one
    of those. It rests on one assumption, stated here and nowhere else: a line
    combo's value is credits per line at one credit staked on that line. Anything
    missing makes the verdict ``indeterminate`` rather than a guess.
    """
    paying = [award for award in awards if award.awarded]
    credits_min = sum(award.value_min or 0.0 for award in paying)
    credits_max = sum(award.value_max or 0.0 for award in paying)

    line_count = view.win_geometry.line_count or (
        view.identity.number_of_lines if view.identity is not None else None
    )
    denomination = _number(view.source.denomination)

    meter = run.meter
    readings = {} if meter is None else {r.frame: r for r in meter.readings}
    total_bet = readings[FRAME_INITIAL].bet if FRAME_INITIAL in readings else None
    observed = readings[FRAME_OUTCOME].win if FRAME_OUTCOME in readings else None

    bet_credits = (
        None if total_bet is None or denomination is None else total_bet / denomination
    )
    per_line = (
        None if bet_credits is None or not line_count else bet_credits / line_count
    )
    cash_min = (
        None
        if per_line is None or denomination is None
        else credits_min * per_line * denomination
    )
    cash_max = (
        None
        if per_line is None or denomination is None
        else credits_max * per_line * denomination
    )

    tolerance = settings.ANALYZE_SPIN_METER_TOLERANCE
    if cash_min is None or cash_max is None or observed is None:
        # A spin that paid nothing and a meter that shows nothing agree without
        # needing any of the conversion above.
        if not paying and observed is None and run.outcome is SpinOutcome.NO_WIN:
            verdict = SpinVerdict.PASSED
            detail = "No line pays, and the win meter is empty"
        else:
            verdict = SpinVerdict.INDETERMINATE
            detail = (
                "Not enough was readable to price the spin: needs the "
                "denomination, the bet off the meter, the line count and the "
                "win off the meter"
            )
    else:
        low, high = min(cash_min, cash_max), max(cash_min, cash_max)
        verdict = (
            SpinVerdict.PASSED
            if low - tolerance <= observed <= high + tolerance
            else SpinVerdict.FAILED
        )
        span = f"{low:.2f}" if _close(low, high, tolerance) else f"{low:.2f}-{high:.2f}"
        detail = (
            f"{len(paying)} line(s) pay {credits_min:g}"
            + ("" if credits_min == credits_max else f"-{credits_max:g}")
            + f" credits; at {per_line:g} credit(s) per line and denom "
            f"{denomination:g} that is {span}, and the meter shows {observed:.2f}"
        )

    return SpinExpectedAward(
        paying_lines=len(paying),
        credits_min=round(credits_min, 4),
        credits_max=round(credits_max, 4),
        exact=all(award.exact for award in paying),
        line_count=line_count,
        denomination=denomination,
        total_bet=total_bet,
        bet_credits=None if bet_credits is None else round(bet_credits, 4),
        credits_per_line=None if per_line is None else round(per_line, 6),
        cash_min=None if cash_min is None else round(cash_min, 4),
        cash_max=None if cash_max is None else round(cash_max, 4),
        observed_win=observed,
        verdict=verdict,
        detail=detail,
    )


async def _check_paylines(
    run: _ActiveRun, frame: SpinFrame, reader: _LogReader | None
) -> SpinPaylineValidation:
    """Split the result frame's reels and check the *running* game's own lines."""
    view = run.paytable
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
        """The validation as it reads when the lines could not be checked --
        still naming which paytable and which set, since that is usually where
        the reason is."""
        return SpinPaylineValidation(
            frame=frame.file_name,
            paytable_id=view.paytable_id,
            paytable_origin=view.source.origin,
            payline_set_id=geometry.payline_set_id,
            resolved_from=geometry.resolved_from,
            line_count=geometry.line_count,
            pay_lengths=list(view.math.pay_lengths),
            error=reason,
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

    split = await grid_service.split(
        GridSplitRequest(file_name=frame.file_name, include_images=False)
    )
    check = await paylines_service.check_lines(
        line_set,
        split=Path(split.output_dir).name,
        images=paylines_service.ImageOptions(overlay=True, lines="paying"),
    )

    # Only now, with the picture already read: the stops name the symbols of the
    # runs similarity found, and never which runs there were.
    stops = None if reader is None else reader.stops
    stops_error = None if reader is None else reader.stops_error
    resolution: reel_stops.Resolution | None = None
    if stops is not None:
        resolution, stops_error = await asyncio.to_thread(
            _resolve_stops, view, check, stops
        )
    elif stops_error is None:
        stops_error = (
            "The game logged no reel stops for this spin, so the symbols on the "
            "reels could not be named and each award stays a range"
        )

    grid = resolution.grid if resolution is not None else None
    elements = {
        str(line.line): [list(pair) for pair in line.elements]
        for line in geometry.paylines
    }
    awards = _award(view, check, elements, grid)

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
        pay_lengths=list(view.math.pay_lengths),
        split=check.source.split,
        threshold=check.threshold,
        summary=_summarise_awards(awards),
        runs_found=sum(1 for award in awards if award.paying),
        awarded_lines=sum(1 for award in awards if award.awarded),
        lines=awards,
        stats=check.stats,
        stops=list(stops or ()),
        stops_log_line=None if reader is None else reader.stops_line,
        stop_anchor=grid.anchor if grid is not None else None,
        stop_anchor_decided=resolution.decided if resolution is not None else False,
        stop_agreed=resolution.agreed if resolution is not None else None,
        stop_compared=resolution.compared if resolution is not None else None,
        symbol_grid=grid.matrix() if grid is not None else [],
        stops_error=stops_error,
        expected=_expected(run, view, awards),
        output_dir=output_dir,
        output_file=output_file,
        overlay_image=overlay,
    )


async def _validate_paylines(run: _ActiveRun, reader: _LogReader | None) -> None:
    """Check the lines the running game declares against the reels it landed.

    Never fails the run for the same reason the meter step does not: the two
    validations answer different questions and a machine that can only do one of
    them should still get that one.
    """
    try:
        async with _step(run, STEP_PAYLINES) as step:
            frame = run.frame(FRAME_OUTCOME)
            if frame is None:
                raise SpinAnalysisUnavailableError(
                    "No result screenshot was taken, so there are no reels to check"
                )
            validation = await _check_paylines(run, frame, reader)
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
            await _take_win(run)
            await _capture(run, FRAME_COLLECTED, STEP_FRAME_COLLECTED)
        else:
            await _capture(run, FRAME_OUTCOME, STEP_FRAME_OUTCOME)
            _skip(run, STEP_TAKE_WIN, "Nothing was won, so there was nothing to take")
            _skip(run, STEP_FRAME_COLLECTED, "Nothing was collected to photograph")

        await _stop_recording(run)
        await _validate_meter(run)
        await _validate_paylines(run, reader)
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
    if run.meter is not None:
        parts.append(f"cash meter {run.meter.verdict.value}")
    if run.paylines is not None and run.paylines.expected is not None:
        parts.append(f"paylines {run.paylines.expected.verdict.value}")
    return "; ".join(parts)


# --- public API -----------------------------------------------------------


async def start() -> SpinAnalysisState:
    """Drive one spin, and validate it.

    Returns as soon as the run is under way: the whole point is the sequence,
    and it is followed over :func:`subscribe` (or polled from :func:`state`)
    rather than awaited. Only the two cheap local preconditions -- the game
    config parsing, and its log existing -- are checked here, so they come back
    as a refused request; everything else is a step, where a failure says which
    part of the machine was not ready.
    """
    global _run
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
            steps={key: _StepRecord(key=key, label=label) for key, label in _SEQUENCE},
        )
        _run = run
        run.task = asyncio.create_task(_execute(run), name=f"analyze-spin-{run_id}")

    logger.info("Spin %s started for %s", run.run_id, run.game)
    return _snapshot(run, images=False)


async def cancel() -> SpinAnalysisState:
    """Ask the run in progress to stop.

    Cooperative: the flag is checked between steps and between slices of every
    wait, so the run stops itself -- tidying up its recording and sealing its
    record on the way -- rather than being torn down mid-call. A cancel
    therefore lands once whatever call is in flight returns.
    """
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
    """One of a run's screenshots, resolved for serving.

    Delegated to :func:`app.services.roi.resolve_frame` rather than re-derived:
    a run writes its frames into the dashboard's screenshot directory precisely
    so ROI and the reel grid can read them, and that function already owns
    resolving an untrusted name inside it -- along with the 400 for a name that
    escapes it and the 404 for one that is not there.
    """
    return roi_service.resolve_frame(file_name)


def state(*, images: bool = False) -> SpinAnalysisState:
    """The run in progress, or the last one that finished. Never fails.

    ``images`` asks for the meter crops, the annotated reels and the paying
    lines' own pictures as data URIs -- everything the progress stream leaves
    out. It is the report, so it is a separate ask rather than the default.
    """
    return _snapshot(_run, images=images)


async def abort() -> None:
    """End any run because the process is shutting down.

    Asks first and cancels hard only if that does not take, because the one
    thing worth getting right here is OBS not being left recording -- which
    means this has to run before the OBS session is closed.
    """
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
    """Drop all state, including the lock bound to this loop. Tests only.

    Async, like :func:`app.services.event_capture.reset`, because ending the run
    means awaiting its cancellation -- a task cancelled but never awaited is the
    pending-task warning that ``filterwarnings = error`` turns into a failure.
    """
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
