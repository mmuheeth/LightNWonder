"""Cyclic Messages: the rule set, the run engine and its endpoints.

No OBS socket opens here -- ``connect``, ``take_screenshot`` and the recording
calls are monkeypatched, as in ``test_event_capture.py``, because this module's
job is the log-to-record pipeline rather than the OBS conversation.

Every log line below is **verbatim** from
``c:\\logs\\Game\\FortuneOx\\Logs\\FortuneOx_Client.log``, copied rather than
composed. That matters more here than in most of these tests: the whole feature
rests on a claim about what the game does and does not write, and a hand-written
line would let a rule pass against a log shape the game never produces.

The interval is patched down in every sampling test. The shipped 0.9s is chosen
against a 6.5-7.3s pass on a real cabinet; waiting that out here would add
seconds per test for no extra coverage.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.config.cyclic_messages import CyclicMessagesSettings
from app.config.game_config import save_active_game
from app.config.runtime import settings
from app.exceptions.base import ObsRequestError
from app.schemas.cyclic_messages import CyclicEventSource, CyclicRunState
from app.schemas.obs import ObsRecordStatus, ScreenshotRequest, ScreenshotResult
from app.services import cyclic_messages as cyclic_service
from app.services import obs as obs_service
from app.utils import game_log
from tests.asserts import assert_failure, assert_success

API = "/api/cyclic-messages"

# --- the win strip, one presentation, verbatim ----------------------------
# The 16:36:16 spin: 16800 cents at denom 100 is the "GAME PAYS 168" a player
# sees, and the 6.75s between WinBangDone and FirstCycle... is the silent
# window. Nothing was omitted from between these lines -- the log really does
# say nothing else about the strip.
PRE_RACKUP = (
    "09/08/26 16:36:21.974 00 FortuneOx:12980 DBG: ButtonPanelState "
    "transitioned from [PanelStateSpinWithStops] to [PanelStatePreRackUp]"
)
GAME_PAYS = (
    "09/08/26 16:36:21.982 00 FortuneOx:12980 DBG: "
    "SpinBufferManager.OnGameStateResults "
    "resultsStateEvent.totalWin.Zero()=False:16800.000"
)
LOSING_SPIN = (
    "09/08/26 16:36:18.584 01 FortuneOx:12980 DBG: "
    "SpinBufferManager.OnGameStatePlay: hasWin=False"
)
WIN_BANG_DONE = (
    "09/08/26 16:36:23.554 01 FortuneOx:12980 DBG: "
    "InputManager - dispatchMessage: WinBangDone"
)
WIN_BANG_DONE_PUBLISH = (
    "09/08/26 16:36:23.554 02 FortuneOx:12980 DBG: "
    "[MessageQueue.Publish] msg[WinBangDone]"
)
LINE_CYCLE_DONE = (
    "09/08/26 16:36:30.305 00 FortuneOx:12980 DBG: [MessageQueue.Publish] "
    "msg[GDK.Client.ClientMessaging.FirstCycleResultsIterationFinishedMsg]"
)
GAME_OVER = (
    "09/08/26 16:36:35.133 00 FortuneOx:12980 DBG: [MessageQueue.Publish] "
    "msg[GDK.Common.ServerAPI.GameOverMsg]"
)
# The game's own suffix on this one is why the rule cannot use the bare name.
RESULTS_CYCLE_STOPPED = (
    "09/08/26 16:36:01.994 00 FortuneOx:12980 DBG: [MessageQueue.Publish] "
    "msg[GDK.Common.ServerAPI.CycleResultsStoppedMsg_BaseGame]"
)
PAYTABLE = (
    "09/08/26 16:16:35.838 00 FortuneOx:12980 DBG: "
    "[WagerGameApp.UpdatePayTable] current denom[100.000] current "
    "paytableId[FortuneOx-1102RX-100c-90] current supported "
    "denoms[1.000,2.000,5.000,10.000,100.000,200.000]"
)

# --- the idle strip, verbatim ---------------------------------------------
ATTRACT_STARTED = (
    "09/08/26 11:43:45.021 00 FortuneOx:22096 DBG: [MessageQueue.Publish] "
    "msg[GDK.Common.ServerAPI.AttractStartedMsg]"
)
ATTRACT_MESSAGE = (
    "09/08/26 11:43:45.040 01 FortuneOx:22096 INF: "
    "StateMachine[AttractStateMachine] transitioned from "
    "[stateSequenceSetupSingleGame] to [stateDisplayAttract] on event "
    "[GDK.Common.ServerAPI.AttractSequenceSetupCompleted]"
)
ATTRACT_ENDED = (
    "09/08/26 15:55:33.051 00 FortuneOx:12980 DBG: [MessageQueue.Publish] "
    "msg[GDK.Common.ServerAPI.AttractSequenceEndCompleted]"
)
# --- the between-spins strip, verbatim -------------------------------------
# The window a spin *ends* into, whichever way it ended -- a loss within a few
# hundred milliseconds of its result, a win only once it has been taken. It is
# bracketed on the state machine rather than on either result line for exactly
# that reason, so these two lines are what open and close it.
IDLE_STRIP_STARTED = (
    "09/08/26 16:36:35.256 01 FortuneOx:12980 INF: "
    "StateMachine[IdleStateMachine] transitioned from "
    "[stateEvaluateGameFlowState] to [stateIdleWithCredits] on event "
    "[GDK.Common.ServerAPI.GameOverMsg]"
)
IDLE_STRIP_ENDED = (
    "09/08/26 16:36:44.019 01 FortuneOx:12980 INF: "
    "StateMachine[IdleStateMachine] transitioned from "
    "[stateIdleWithCredits] to [stateStartGameFlow] on event "
    "[GDK.Client.ClientMessaging.PlayButtonPressedMsg]"
)
NOISE = "09/08/26 16:36:37.093 00 FortuneOx:12980 DBG: coin value: 200"


def at(line: str, time: str) -> str:
    """The same line at a different ``HH:MM:SS.mmm``.

    Every rule here is debounced on the game's own timestamps, so a test that
    needs two of one event has to move one of them. The stamp starts at index 9,
    straight after the ``MM/DD/YY `` the log opens every line with.
    """
    assert len(time) == len("00:00:00.000"), time
    moved = line[:9] + time + line[9 + len(time) :]
    assert game_log.parse_line(moved) is not None, moved
    return moved


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def game_log_file(tmp_path: Path) -> Path:
    log = tmp_path / "FortuneOx_Client.log"
    log.write_text("", encoding="utf-8")
    return log


@pytest.fixture
def capture_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the screenshot root at a temp directory. ``obs_screenshot_dir`` is
    a property, so it is patched on the class rather than the instance."""
    root = tmp_path / "obs-captured-files"
    root.mkdir()
    monkeypatch.setattr(
        type(settings), "obs_screenshot_dir", property(lambda _self: root)
    )
    return root / settings.CYCLIC_MESSAGES_DIR_NAME


@pytest.fixture
def active_game(
    tmp_path: Path, game_log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """A game config directory holding one game pointed at ``game_log_file``."""
    directory = tmp_path / "games"
    directory.mkdir()
    (directory / "FortuneOx.json").write_text(
        json.dumps(
            {"name": "FortuneOx", "process": "FortuneOx.exe", "log": str(game_log_file)}
        ),
        encoding="utf-8",
    )
    selection = tmp_path / "active_game.json"
    save_active_game(selection, "FortuneOx")

    monkeypatch.setattr(
        type(settings), "ideck_game_config_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game_path", property(lambda _self: selection)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game", property(lambda _self: "FortuneOx")
    )
    monkeypatch.setattr(
        type(settings),
        "ideck_game_config_path_for",
        lambda _self, game: directory / f"{game}.json",
    )
    yield directory


class FakeRecorder:
    """OBS's recording half: remembers every start and writes a file for every
    stop, in its own root -- so the move into the run directory is exercised
    rather than short-circuited by both paths being the same place."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.started: list[str] = []
        self.files: list[Path] = []

    async def start(self, output_dir: str | None = None) -> ObsRecordStatus:
        self.started.append(output_dir or "")
        return ObsRecordStatus(active=True, paused=False)

    async def stop(self) -> ObsRecordStatus:
        directory = self.root / (self.started[-1] if self.started else "")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"2026-09-08 16-36-{len(self.files) + 20}.mp4"
        target.write_bytes(b"\x00\x00\x00 ftypisom")
        self.files.append(target)
        return ObsRecordStatus(active=False, paused=False, output_path=str(target))


@pytest.fixture
def recorder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRecorder:
    """A stubbed OBS recorder writing into its own root, which is what OBS does
    when ``OBS_RECORDING_DIR`` and ``OBS_SCREENSHOT_DIR`` differ."""
    root = tmp_path / "obs-recordings"
    root.mkdir()
    monkeypatch.setattr(
        type(settings), "obs_recording_dir", property(lambda _self: root)
    )
    fake = FakeRecorder(root)
    monkeypatch.setattr(obs_service, "start_recording", fake.start)
    monkeypatch.setattr(obs_service, "stop_recording", fake.stop)
    return fake


@pytest.fixture
def fake_obs(
    monkeypatch: pytest.MonkeyPatch, recorder: FakeRecorder
) -> list[ScreenshotRequest]:
    """Record screenshot requests and write a file for each, as OBS would."""
    requests: list[ScreenshotRequest] = []

    async def take_screenshot(payload: ScreenshotRequest) -> ScreenshotResult:
        requests.append(payload)
        assert payload.file_name is not None
        assert payload.output_dir is not None
        target = (
            settings.obs_screenshot_dir
            / payload.output_dir
            / f"{payload.file_name}.png"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG\r\n\x1a\n")
        return ScreenshotResult(
            source_name="Main", image_format="png", file_path=str(target)
        )

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(obs_service, "take_screenshot", take_screenshot)
    monkeypatch.setattr(obs_service, "connect", noop)
    monkeypatch.setattr(obs_service, "select_current_game_window", noop)
    return requests


@pytest.fixture
def quick_sampling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sample fast enough for a test, both windows. See the module docstring.

    Both, because the two are sampled at their own intervals and against their
    own deadlines -- a test that patched only the win pass's would wait out the
    between-spins window's shipped 90s deadline.
    """
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS", 5.0)
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_IDLE_MAX_SECONDS", 5.0)
    # Nothing here can decode the fake one-line mp4 the recorder writes, and a
    # test asserting about *capture* should not be waiting on an OCR engine to
    # decide whether it ran. The queue itself is asserted directly instead --
    # see `test_a_taken_win_queues_its_clip_instead_of_reading_it_back`.
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_RECOVER_FROM_CLIP", False)


@pytest.fixture
async def running(
    active_game: Path, capture_root: Path, fake_obs: list[ScreenshotRequest]
) -> AsyncIterator[None]:
    """A started run, stopped afterwards even if the test fails."""
    await cyclic_service.start()
    try:
        yield
    finally:
        if cyclic_service.status().active:
            await cyclic_service.stop()


# --- helpers --------------------------------------------------------------


def append(log: Path, *lines: str) -> None:
    """Write lines the way the game does: opened, appended, closed."""
    with log.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


async def wait_for_messages(count: int, *, timeout: float = 5.0) -> None:
    """Wait until the run has taken ``count`` frames.

    Polling the real status rather than sleeping keeps the tests quick without
    making them depend on how loaded the machine is.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while cyclic_service.status().message_count < count:
        if asyncio.get_running_loop().time() >= deadline:
            state = cyclic_service.status()
            raise AssertionError(
                f"expected {count} messages, saw {state.message_count} "
                f"(errors: {state.errors})"
            )
        await asyncio.sleep(0.02)


async def wait_until(
    predicate: Callable[[], bool], *, what: str, timeout: float = 5.0
) -> None:
    """Poll the real status until something is true, as ``wait_for_messages``
    does -- a fixed sleep would make the test depend on how loaded the machine is."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            state = cyclic_service.status()
            raise AssertionError(
                f"timed out waiting for {what} (errors: {state.errors})"
            )
        await asyncio.sleep(0.02)


def events_named(detail: Any, name: str) -> list[Any]:
    return [event for event in detail.events if event.event == name]


def live_frames_named(name: str) -> list[Any]:
    """Frames of the window the live view is showing, by event name.

    Off ``live()`` rather than ``status()`` because that is the payload scoped
    to one window: the sampled counts on ``status()`` are the run's, so a
    window still to take its first frame is indistinguishable there from one
    whose predecessor is still going.
    """
    return [frame for frame in cyclic_service.live().frames if frame.event == name]


# --- the rules ------------------------------------------------------------
# Asserted against the rule set directly, because a rule that stops matching
# is the one failure that would leave the engine looking healthy and empty.


def detect(raw: str) -> game_log.DetectedEvent | None:
    line = game_log.parse_line(raw)
    assert line is not None
    return game_log.match(line, game_log.CYCLIC_MESSAGE_RULES)


@pytest.mark.parametrize(
    ("raw", "event"),
    [
        (PRE_RACKUP, "cyclic-rackup-started"),
        (GAME_PAYS, "cyclic-game-pays"),
        (WIN_BANG_DONE, "cyclic-win-presented"),
        (WIN_BANG_DONE_PUBLISH, "cyclic-win-presented"),
        (LINE_CYCLE_DONE, "cyclic-line-pays-cycle-finished"),
        (RESULTS_CYCLE_STOPPED, "cyclic-results-cycle-stopped"),
        (GAME_OVER, "cyclic-game-over"),
        (ATTRACT_STARTED, "cyclic-cycle-started"),
        (ATTRACT_MESSAGE, "cyclic-message-shown"),
        (ATTRACT_ENDED, "cyclic-cycle-completed"),
    ],
)
def test_every_rule_matches_the_line_the_game_writes(raw: str, event: str) -> None:
    detected = detect(raw)
    assert detected is not None, f"no rule matched: {raw}"
    assert detected.event == event


def test_the_amount_is_captured_from_the_only_line_that_states_one() -> None:
    detected = detect(GAME_PAYS)
    assert detected is not None
    assert detected.fields["win_cents"] == "16800.000"


def test_a_losing_spin_is_not_a_game_pays() -> None:
    """``Zero()=True`` shows no GAME PAYS at all, so there is nothing to shoot."""
    assert detect(LOSING_SPIN) is None
    assert detect(NOISE) is None


def test_the_boundary_markers_take_no_frame() -> None:
    for raw in (PRE_RACKUP, RESULTS_CYCLE_STOPPED, ATTRACT_STARTED, ATTRACT_ENDED):
        detected = detect(raw)
        assert detected is not None
        assert detected.capture is False, raw


def test_the_messages_do_take_a_frame() -> None:
    for raw in (GAME_PAYS, WIN_BANG_DONE, LINE_CYCLE_DONE, GAME_OVER, ATTRACT_MESSAGE):
        detected = detect(raw)
        assert detected is not None
        assert detected.capture is True, raw


# --- the amount, in the units the strip shows it in -----------------------


async def test_game_pays_reports_credits_using_the_logged_denomination(
    game_log_file: Path, running: None
) -> None:
    """16800 cents at denom 100 is the 168 on screen -- the point of the join."""
    append(game_log_file, PAYTABLE, GAME_PAYS)
    await wait_for_messages(1)
    detail = await cyclic_service.stop()

    (pays,) = events_named(detail, "cyclic-game-pays")
    assert pays.summary == "Game pays 168"
    assert pays.fields["win_credits"] == "168"
    assert pays.fields["win_cents"] == "16800.000"
    assert pays.fields["denom"] == "100"


async def test_the_denomination_is_read_backwards_out_of_the_log(
    game_log_file: Path,
    active_game: Path,
    capture_root: Path,
    fake_obs: list[ScreenshotRequest],
) -> None:
    """The game logs a denomination only when one *changes*, so a run started
    against an already-running game has to look behind its own cursor."""
    append(game_log_file, PAYTABLE)
    await cyclic_service.start()
    try:
        # Deliberately no paytable line after the cursor.
        append(game_log_file, GAME_PAYS)
        await wait_for_messages(1)
    finally:
        detail = await cyclic_service.stop()

    (pays,) = events_named(detail, "cyclic-game-pays")
    assert pays.summary == "Game pays 168"


async def test_cents_are_reported_as_cents_when_no_denomination_is_known(
    game_log_file: Path, running: None
) -> None:
    """Guessing a rate would be wrong by a factor of the denomination while
    looking exactly as confident as a right answer."""
    append(game_log_file, GAME_PAYS)
    await wait_for_messages(1)
    detail = await cyclic_service.stop()

    (pays,) = events_named(detail, "cyclic-game-pays")
    assert pays.summary == "Game pays 16800 cents (denomination not logged yet)"
    assert "win_credits" not in pays.fields


# --- sampling the silent window -------------------------------------------


async def test_the_line_message_pass_is_sampled_between_its_two_boundaries(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """The frames that fill the window the log says nothing inside of."""
    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE)
    # game-pays, win-presented, then sampled frames.
    await wait_for_messages(4)
    append(game_log_file, LINE_CYCLE_DONE)
    detail = await cyclic_service.stop()

    sampled = events_named(detail, "cyclic-line-pays-shown")
    assert sampled, "the silent window was not sampled"
    assert all(event.source is CyclicEventSource.SAMPLED for event in sampled)
    assert all(event.screenshot for event in sampled)
    assert detail.sampled_count == len(sampled)


async def test_a_sampled_event_quotes_the_boundary_that_opened_its_window(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """Not a line about the message in its own picture -- there is none, and
    inventing one is what would make a guess look like evidence.

    The boundary is ``cyclic-game-pays``, which is where capture opens: the
    strip is already showing the banner and then the count-up by the time
    ``WinBangDone`` lands, and those frames are part of the presentation too.
    """
    append(game_log_file, GAME_PAYS, WIN_BANG_DONE)
    await wait_for_messages(3)
    detail = await cyclic_service.stop()

    sampled = events_named(detail, "cyclic-line-pays-shown")
    assert sampled
    assert all(event.log_line == GAME_PAYS for event in sampled)


async def test_sampling_stops_when_the_log_says_the_pass_finished(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    append(game_log_file, GAME_PAYS, WIN_BANG_DONE)
    await wait_for_messages(3)
    append(game_log_file, LINE_CYCLE_DONE)

    deadline = asyncio.get_running_loop().time() + 5.0
    while cyclic_service.status().sampling:
        assert asyncio.get_running_loop().time() < deadline, "sampler never stopped"
        await asyncio.sleep(0.02)

    # Sampled frames specifically, not the message count: the pass-finished
    # frame is itself recorded *after* the sampler is stopped, so the total is
    # expected to move by exactly that one.
    settled = cyclic_service.status().sampled_count
    await asyncio.sleep(0.2)
    assert cyclic_service.status().sampled_count == settled


async def test_results_cycling_stopping_also_ends_a_pass(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """The ordinary way a pass is cut short is the player spinning again."""
    append(game_log_file, GAME_PAYS, WIN_BANG_DONE)
    await wait_for_messages(3)
    append(game_log_file, RESULTS_CYCLE_STOPPED)

    deadline = asyncio.get_running_loop().time() + 5.0
    while cyclic_service.status().sampling:
        assert asyncio.get_running_loop().time() < deadline, "sampler never stopped"
        await asyncio.sleep(0.02)


async def test_sampling_can_be_turned_off_leaving_the_boundaries(
    game_log_file: Path, running: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off, a run still records what the log actually says about a win."""
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_SAMPLE_LINE_PAYS", False)
    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE, LINE_CYCLE_DONE)
    await wait_for_messages(3)
    detail = await cyclic_service.stop()

    assert events_named(detail, "cyclic-line-pays-shown") == []
    assert detail.sampled_count == 0
    assert events_named(detail, "cyclic-game-pays")
    assert events_named(detail, "cyclic-win-presented")
    assert events_named(detail, "cyclic-line-pays-cycle-finished")


async def test_a_run_stops_cleanly_while_a_pass_is_still_being_sampled(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """No ``LINE_CYCLE_DONE`` ever arrives. Under ``filterwarnings = error`` a
    sampler outliving its run is a failure, not just untidy."""
    append(game_log_file, GAME_PAYS, WIN_BANG_DONE)
    await wait_for_messages(3)
    detail = await cyclic_service.stop()

    assert detail.status is CyclicRunState.COMPLETED
    assert cyclic_service.status().active is False


# --- video: one clip per win presentation ---------------------------------
# The boundaries are the whole point: a run records the win presentations and
# nothing else, so the video shows the thing the frames are evidence of rather
# than an hour of idle attract with a few seconds of win somewhere inside it.


async def test_nothing_is_recorded_until_a_win_pays(
    game_log_file: Path, recorder: FakeRecorder, running: None
) -> None:
    """Start tracking is deliberately not start recording: the idle strip logs
    a line per message, so every one of its messages already has a frame."""
    assert cyclic_service.status().recording is False

    append(game_log_file, ATTRACT_STARTED, ATTRACT_MESSAGE)
    await wait_for_messages(1)
    assert cyclic_service.status().recording is False

    detail = await cyclic_service.stop()
    assert recorder.started == []
    assert detail.videos == []


async def test_a_clip_runs_from_game_pays_to_the_line_messages_finishing(
    game_log_file: Path, quick_sampling: None, recorder: FakeRecorder, running: None
) -> None:
    append(game_log_file, PAYTABLE, GAME_PAYS)
    # The frame exists, so recording must already have been on when it was
    # taken -- that ordering is what puts the GAME PAYS banner in the video.
    await wait_for_messages(1)
    assert cyclic_service.status().recording is True

    append(game_log_file, WIN_BANG_DONE)
    await wait_for_messages(3)
    assert cyclic_service.status().recording is True, "the pass is still going"

    append(game_log_file, LINE_CYCLE_DONE)
    await wait_until(
        lambda: not cyclic_service.status().recording, what="the clip to close"
    )
    detail = await cyclic_service.stop()

    (video,) = detail.videos
    assert video.closed_by == "cyclic-line-pays-cycle-finished"
    assert video.cycle == 1
    assert video.error is None
    assert video.file_name is not None
    assert len(recorder.started) == 1, "one presentation is one recording"


async def test_the_clip_is_filed_beside_the_screenshots_and_served_back(
    client: AsyncClient, game_log_file: Path, capture_root: Path, running: None
) -> None:
    """Named after the sequence it covers rather than left under OBS's own
    timestamp, so it sits beside that sequence's frames in the directory."""
    append(game_log_file, PAYTABLE, GAME_PAYS, LINE_CYCLE_DONE)
    await wait_until(
        lambda: cyclic_service.status().video_count == 1, what="the clip to be filed"
    )
    detail = await cyclic_service.stop()

    (video,) = detail.videos
    assert video.file_name is not None
    assert video.file_name.startswith("cycle-001_win-video_")
    assert video.file_name.endswith(".mp4")
    assert (capture_root / detail.run_id / video.file_name).is_file()

    response = await client.get(f"{API}/runs/{detail.run_id}/files/{video.file_name}")
    assert response.status_code == 200
    assert response.content.startswith(b"\x00\x00\x00 ftyp")


async def test_the_next_spin_cutting_the_pass_short_closes_the_clip(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """A presentation the player interrupted is a shorter video, not a broken
    one -- which is why the clip says what closed it."""
    append(game_log_file, GAME_PAYS, WIN_BANG_DONE)
    await wait_for_messages(3)
    append(game_log_file, RESULTS_CYCLE_STOPPED)

    await wait_until(
        lambda: not cyclic_service.status().recording, what="the clip to close"
    )
    detail = await cyclic_service.stop()

    (video,) = detail.videos
    assert video.closed_by == "cyclic-results-cycle-stopped"
    assert video.file_name is not None


async def test_a_second_win_gets_its_own_clip(
    game_log_file: Path, running: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two presentations are two clips even when the first never reported
    finishing: one file covering both could not be split afterwards."""
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_SAMPLE_LINE_PAYS", False)
    append(game_log_file, GAME_PAYS)
    await wait_for_messages(1)
    append(game_log_file, at(GAME_PAYS, "16:36:51.982"))
    await wait_for_messages(2)
    detail = await cyclic_service.stop()

    first, second = detail.videos
    assert [video.cycle for video in detail.videos] == [1, 2]
    assert first.closed_by == "cyclic-game-pays"
    assert second.closed_by == "run-stopped"
    assert first.file_name != second.file_name


async def test_a_run_stopped_mid_presentation_files_what_it_recorded(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    append(game_log_file, GAME_PAYS, WIN_BANG_DONE)
    await wait_for_messages(3)
    detail = await cyclic_service.stop()

    (video,) = detail.videos
    assert video.closed_by == "run-stopped"
    assert video.file_name is not None
    assert cyclic_service.status().recording is False


@pytest.mark.parametrize(
    "name",
    ["CYCLIC_MESSAGES_VIDEO_MAX_SECONDS", "CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS"],
)
def test_the_deadlines_are_sized_against_the_longest_pass_not_a_typical_one(
    name: str,
) -> None:
    """Both deadlines exist for the closing line that never comes, and a 40-line
    win is not that. A pass shows every paying line for ~2s, so its length is a
    function of how many paid: 8.3s end to end for one line on FortuneOx and
    78.8s for forty. Sized against the typical win, the clip's deadline cut
    every big win off at about line 29 of 40 -- and a truncated clip looks
    exactly like a complete one to whoever watches it.

    Read off the field default rather than the live settings so a machine's own
    ``.env`` cannot make this pass while the shipped value is wrong again.
    """
    default = CyclicMessagesSettings.model_fields[name].default
    assert default >= 120.0, f"{name} is below the length of a 40-line pass"


async def test_a_pass_longer_than_a_typical_win_is_not_truncated(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """The counterpart in behaviour: a pass whose closing line arrives late is
    still closed by the log, not by a clock."""
    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE)
    await wait_for_messages(3)
    await asyncio.sleep(0.5)
    assert cyclic_service.status().recording is True

    append(game_log_file, LINE_CYCLE_DONE)
    await wait_until(
        lambda: not cyclic_service.status().recording, what="the clip to close"
    )
    detail = await cyclic_service.stop()

    (video,) = detail.videos
    assert video.closed_by == "cyclic-line-pays-cycle-finished"
    # Specific to truncation rather than `errors == []`: a run also notes when
    # its captions are not being read, and this fixture's game declares no
    # caption region -- true, reported, and nothing to do with a cut-off pass.
    assert not [message for message in detail.errors if "cut off" in message], (
        "nothing was cut short, so nothing to report"
    )


async def test_a_clip_whose_closing_line_never_arrives_is_closed_by_the_deadline(
    game_log_file: Path, running: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unbounded it would record the rest of the session, which is exactly the
    video recording per win exists not to make."""
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_SAMPLE_LINE_PAYS", False)
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_VIDEO_MAX_SECONDS", 0.2)
    append(game_log_file, GAME_PAYS)
    await wait_for_messages(1)

    await wait_until(
        lambda: not cyclic_service.status().recording, what="the deadline to close it"
    )
    detail = await cyclic_service.stop()

    (video,) = detail.videos
    assert video.closed_by == "time-limit"
    assert video.file_name is not None
    # On the run as well as on the clip: a cut-off video looks complete, so the
    # one place it can be noticed is the panel's own error line.
    assert any("cut off after 0.2s" in message for message in detail.errors)


async def test_recording_can_be_turned_off_leaving_the_screenshots(
    game_log_file: Path,
    recorder: FakeRecorder,
    running: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off, this is event capture with a narrower rule set -- a reasonable thing
    to want on a machine that cannot encode."""
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_RECORD_VIDEO", False)
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_SAMPLE_LINE_PAYS", False)
    append(game_log_file, PAYTABLE, GAME_PAYS, LINE_CYCLE_DONE)
    await wait_for_messages(2)
    detail = await cyclic_service.stop()

    assert recorder.started == []
    assert detail.videos == []
    assert events_named(detail, "cyclic-game-pays")[0].screenshot is not None


async def test_a_win_obs_refuses_to_record_still_gets_its_frames(
    game_log_file: Path, running: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clip is a record, never a reading, so a failure is reported on the run
    rather than raised into the watcher."""

    async def refuse(*_args: Any, **_kwargs: Any) -> ObsRecordStatus:
        raise ObsRequestError("OBS is busy recording something else")

    monkeypatch.setattr(obs_service, "start_recording", refuse)
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_SAMPLE_LINE_PAYS", False)

    append(game_log_file, PAYTABLE, GAME_PAYS)
    await wait_for_messages(1)
    detail = await cyclic_service.stop()

    (video,) = detail.videos
    assert video.cycle == 1
    assert video.file_name is None
    assert "busy recording" in (video.error or "")
    assert any("busy recording" in message for message in detail.errors)
    assert events_named(detail, "cyclic-game-pays")[0].screenshot is not None
    assert cyclic_service.status().recording is False


async def test_a_recording_obs_reports_no_path_for_says_so(
    game_log_file: Path, running: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stop_without_a_path(*_args: Any, **_kwargs: Any) -> ObsRecordStatus:
        return ObsRecordStatus(active=False, paused=False)

    monkeypatch.setattr(obs_service, "stop_recording", stop_without_a_path)
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_SAMPLE_LINE_PAYS", False)

    append(game_log_file, GAME_PAYS, LINE_CYCLE_DONE)
    await wait_until(
        lambda: cyclic_service.status().video_count == 1, what="the clip to be filed"
    )
    detail = await cyclic_service.stop()

    (video,) = detail.videos
    assert video.file_name is None
    assert video.error == "OBS did not report where it wrote the recording"


# --- sequences and ordering -----------------------------------------------


async def test_every_event_gets_its_own_sequence_with_two_producers(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """The watcher and the sampler both record; the numbering is what a reader
    trusts, so a collision would silently overwrite a frame in the UI."""
    append(game_log_file, PAYTABLE, PRE_RACKUP, GAME_PAYS, WIN_BANG_DONE)
    await wait_for_messages(5)
    append(game_log_file, LINE_CYCLE_DONE, GAME_OVER)
    detail = await cyclic_service.stop()

    sequences = [event.sequence for event in detail.events]
    assert len(sequences) == len(set(sequences)), f"duplicate sequences: {sequences}"
    assert sequences == sorted(sequences), "the manifest is not in sequence order"
    assert sequences == list(range(1, len(sequences) + 1))


async def test_a_win_presentation_is_its_own_cyclic_sequence(
    game_log_file: Path, running: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_SAMPLE_LINE_PAYS", False)
    append(game_log_file, GAME_PAYS, WIN_BANG_DONE, LINE_CYCLE_DONE)
    await wait_for_messages(3)
    append(game_log_file, at(GAME_PAYS, "16:36:51.982"))
    await wait_for_messages(4)
    detail = await cyclic_service.stop()

    first, second = events_named(detail, "cyclic-game-pays")
    assert second.cycle == first.cycle + 1
    assert detail.cycle_count == 1  # only the first pass reported finishing


async def test_the_idle_strip_still_records_one_event_per_message(
    game_log_file: Path, running: None
) -> None:
    """Family 1 is unchanged by any of the above: it logs per message, so it
    gets an event per message rather than a sampled window."""
    append(
        game_log_file,
        ATTRACT_STARTED,
        ATTRACT_MESSAGE,
        at(ATTRACT_MESSAGE, "11:43:53.040"),
        at(ATTRACT_ENDED, "11:44:01.051"),
    )
    await wait_for_messages(2)
    detail = await cyclic_service.stop()

    shown = events_named(detail, "cyclic-message-shown")
    assert len(shown) == 2
    assert [event.position for event in shown] == [1, 2]
    assert all(event.source is CyclicEventSource.LOG for event in shown)
    assert detail.cycle_count == 1


async def test_the_duplicate_forms_of_one_moment_collapse(
    game_log_file: Path, running: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The game writes ``WinBangDone`` twice in the same millisecond, and both
    forms are matched on purpose -- the debounce is what makes that safe."""
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_SAMPLE_LINE_PAYS", False)
    append(game_log_file, WIN_BANG_DONE, WIN_BANG_DONE_PUBLISH)
    await wait_for_messages(1)
    await asyncio.sleep(0.2)
    detail = await cyclic_service.stop()

    assert len(events_named(detail, "cyclic-win-presented")) == 1


# --- the manifest and the endpoints ---------------------------------------


async def test_the_manifest_on_disk_matches_what_the_api_returns(
    game_log_file: Path, capture_root: Path, quick_sampling: None, running: None
) -> None:
    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE)
    await wait_for_messages(3)
    detail = await cyclic_service.stop()

    document: Any = json.loads(
        (capture_root / detail.run_id / cyclic_service.MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert document["run_id"] == detail.run_id
    assert document["status"] == "completed"
    assert document["sampled_count"] == detail.sampled_count
    assert len(document["events"]) == len(detail.events)
    assert {event["source"] for event in document["events"]} <= {"log", "sampled"}


async def test_status_reports_nothing_running_by_default() -> None:
    state = cyclic_service.status()
    assert state.active is False
    assert state.run_id is None
    assert state.sampling is False


async def test_starting_twice_is_a_conflict(
    client: AsyncClient, active_game: Path, capture_root: Path, fake_obs: list[Any]
) -> None:
    assert_success((await client.post(f"{API}/start")).json())
    try:
        failure = assert_failure(
            (await client.post(f"{API}/start")).json(),
            code="CYCLIC_MESSAGES_ALREADY_RUNNING",
        )
        assert failure is not None
    finally:
        await client.post(f"{API}/stop")


async def test_stopping_without_a_run_is_a_conflict(client: AsyncClient) -> None:
    response = await client.post(f"{API}/stop")
    assert response.status_code == 409
    assert_failure(response.json(), code="CYCLIC_MESSAGES_NOT_RUNNING")


async def test_a_run_without_a_log_is_refused(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused up front: a run against a log that never appears looks exactly
    like a quiet game."""
    directory = tmp_path / "games"
    directory.mkdir()
    (directory / "FortuneOx.json").write_text(
        json.dumps(
            {
                "name": "FortuneOx",
                "process": "FortuneOx.exe",
                "log": str(tmp_path / "nope.log"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game", property(lambda _self: "FortuneOx")
    )
    monkeypatch.setattr(
        type(settings),
        "ideck_game_config_path_for",
        lambda _self, game: directory / f"{game}.json",
    )

    response = await client.post(f"{API}/start")
    assert response.status_code == 409
    assert_failure(response.json(), code="CYCLIC_MESSAGES_LOG_UNAVAILABLE")


async def test_runs_are_listed_and_fetched_back(
    client: AsyncClient, game_log_file: Path, capture_root: Path, running: None
) -> None:
    append(game_log_file, PAYTABLE, GAME_PAYS)
    await wait_for_messages(1)
    detail = await cyclic_service.stop()

    listed = assert_success((await client.get(f"{API}/runs")).json())
    assert [run["run_id"] for run in listed] == [detail.run_id]

    fetched = assert_success((await client.get(f"{API}/runs/{detail.run_id}")).json())
    assert fetched["message_count"] == detail.message_count
    assert any(event["event"] == "cyclic-game-pays" for event in fetched["events"])


async def test_an_unknown_run_is_a_404(client: AsyncClient, capture_root: Path) -> None:
    response = await client.get(f"{API}/runs/nope")
    assert response.status_code == 404
    assert_failure(response.json(), code="CYCLIC_MESSAGES_RUN_NOT_FOUND")


async def test_a_traversing_file_name_is_refused(
    client: AsyncClient, game_log_file: Path, capture_root: Path, running: None
) -> None:
    """Both halves of the URL came off the wire, so both go through the guards."""
    detail = await cyclic_service.stop()
    response = await client.get(f"{API}/runs/{detail.run_id}/files/..%2F..%2Frun.json")
    assert response.status_code == 404


async def test_a_captured_frame_is_served_back(
    client: AsyncClient, game_log_file: Path, capture_root: Path, running: None
) -> None:
    append(game_log_file, PAYTABLE, GAME_PAYS)
    await wait_for_messages(1)
    detail = await cyclic_service.stop()

    (pays,) = events_named(detail, "cyclic-game-pays")
    assert pays.screenshot is not None
    response = await client.get(f"{API}/runs/{detail.run_id}/files/{pays.screenshot}")
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")


# --- shutdown -------------------------------------------------------------


async def test_abort_seals_the_record_as_interrupted(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """Nobody pressed stop, so ``interrupted`` rather than ``completed`` -- and
    the sampler goes with it."""
    append(game_log_file, GAME_PAYS, WIN_BANG_DONE)
    await wait_for_messages(3)
    run_id = cyclic_service.status().run_id
    assert run_id is not None

    await cyclic_service.abort()

    assert cyclic_service.status().active is False
    assert cyclic_service.get_run(run_id).status is CyclicRunState.INTERRUPTED


# --- the between-spins strip, and the window after a taken win -------------
# Both of these are about the *second* set of messages a spin produces: the
# strip that runs once it is over, which is "GAME OVER", "GAME PAYS n" and
# "PLAY 880 CREDITS" whether the spin lost or its win has been collected. The
# game logs not one of those three, so the window is sampled exactly as a win
# presentation is -- and the bug both tests exist against is the window being
# *opened late*, which leaves the strip recorded as two boundaries with
# nothing between them and looks identical to a strip that showed nothing.


async def test_a_losing_spin_gets_the_between_spins_window_sampled(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """A loss pays nothing, so it has no win presentation at all -- but the
    strip it leaves up is a cyclic message strip like any other and is the only
    thing this feature can capture about that spin."""
    append(game_log_file, LOSING_SPIN, IDLE_STRIP_STARTED)
    await wait_until(
        lambda: cyclic_service.status().sampled_count >= 3,
        what="the between-spins strip to be sampled",
    )
    append(game_log_file, IDLE_STRIP_ENDED)
    await wait_until(
        lambda: not cyclic_service.status().sampling,
        what="the next spin to close the window",
    )
    detail = await cyclic_service.stop()

    frames = events_named(detail, "cyclic-idle-message-shown")
    assert len(frames) >= 3
    # Forced on rather than inherited from the opening line, which takes no
    # picture: inheriting it made every frame of this strip a marker with no
    # screenshot, so a losing spin recorded a dozen events and no images.
    assert all(frame.screenshot is not None for frame in frames)
    assert all(frame.source is CyclicEventSource.SAMPLED for frame in frames)


async def test_the_between_spins_strip_is_sampled_at_its_own_interval(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """Its own setting, and the live view reports *that* one rather than the
    win pass's -- reading the wrong one told a window it was behind when the
    interval it asked for was exactly what it got, and raised a coverage
    warning about it."""
    append(game_log_file, LOSING_SPIN, IDLE_STRIP_STARTED)
    await wait_until(
        lambda: cyclic_service.status().sampled_count >= 2,
        what="the between-spins strip to be sampled",
    )

    view = cyclic_service.live()
    assert view.sample_interval_seconds == pytest.approx(
        settings.CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS
    )


async def test_taking_a_win_early_carries_the_window_on_without_a_break(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """Take the win before its line messages have been round once.

    On screen nothing stops: the strip runs straight on from "LINE 1 PAYS 250"
    into "GAME OVER / GAME PAYS n / PLAY 880 CREDITS", and the only thing
    marking the boundary is a log line. So the window runs straight on with it
    -- one sequence, one unbroken run of frames, one clip.

    What this replaced closed the win window here and opened a fresh one. That
    cost the end of the line messages, which on a win taken this early is
    exactly where the messages it has not shown yet are; and it moved the live
    view onto the new sequence, which emptied the card of the win a tester was
    watching at the very moment they pressed the button.
    """
    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE)
    await wait_until(
        lambda: len(live_frames_named("cyclic-line-pays-shown")) >= 2,
        what="the win presentation to be sampled",
    )
    before = cyclic_service.live()
    assert before.strip == cyclic_service.WIN_VIDEO
    win_cycle = before.cycle

    # Take win: the game goes idle while the line messages are still running.
    append(game_log_file, at(IDLE_STRIP_STARTED, "16:36:26.000"))
    # Waited on the *strip's own* frames rather than on the sampled count,
    # which the win pass is still climbing: the claim is that this strip gets
    # frames of its own, and promptly.
    await wait_until(
        lambda: len(live_frames_named("cyclic-idle-message-shown")) >= 2,
        what="the between-spins strip to start being sampled",
    )

    # Never stopped, and never on a second sequence. Both halves are the same
    # window, which is what "no break in the frames" means concretely.
    carried = cyclic_service.live()
    assert carried.capturing is True
    assert carried.cycle == win_cycle
    assert carried.strip == cyclic_service.WIN_THEN_IDLE

    # And the line messages captured before the button was pressed are still
    # on the card, with the strip's own frames appended after them.
    assert len(live_frames_named("cyclic-line-pays-shown")) >= 2

    # The line the game writes *after* going idle. It names the win window, so
    # read as "close the current window" it tore down the strip a second after
    # it started -- here it is a moment on the timeline and nothing more.
    append(game_log_file, at(LINE_CYCLE_DONE, "16:36:30.000"))
    await wait_until(
        lambda: len(live_frames_named("cyclic-line-pays-cycle-finished")) == 1,
        what="the line-message cycle's own finishing line to be recorded",
    )
    assert cyclic_service.live().capturing is True

    append(game_log_file, at(IDLE_STRIP_ENDED, "16:36:34.000"))
    await wait_until(
        lambda: not cyclic_service.status().sampling,
        what="the next spin to close the window",
    )
    detail = await cyclic_service.stop()

    # Both sets of messages, and both under the one sequence: the spin is one
    # thing that happened, however the log brackets its halves.
    line_pays = events_named(detail, "cyclic-line-pays-shown")
    idle = events_named(detail, "cyclic-idle-message-shown")
    assert line_pays, "the win presentation captured no line messages"
    assert idle, "the strip after the win was taken captured nothing"
    assert {event.cycle for event in line_pays} == {event.cycle for event in idle}
    assert all(frame.screenshot is not None for frame in idle)


async def test_a_win_taken_early_is_captured_to_its_own_closing_line(
    game_log_file: Path,
    quick_sampling: None,
    running: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A carried-on window ends on the line messages, not on the strip beneath
    them.

    Take the win early and two strips run at once: the top band goes on
    cycling "LINE 4 PAYS 15" for as long as the win takes to pay -- minutes on
    a 40-line one -- while the small band underneath laps its three
    between-spins captions every six seconds. Arming the lap watch at the
    carry-on therefore closed the window on that first six-second lap, with
    the line messages still running: measured on a real run, six frames and
    then 69 uncaptured seconds, with ``cyclic-line-pays-cycle-finished``
    arriving to a window that had already been read and filed.

    The watch is stubbed to fire on its very first frame, so "was it armed"
    is the only thing this can be measuring.
    """

    class _AlwaysLooped:
        """A lap watch that says the strip has come round immediately."""

        # Only read to log how much of the strip a window saw.
        seen: tuple[object, ...] = ()

        def saw(self, path: Path) -> bool:  # noqa: ARG002 - the stub's point
            return True

    armed: list[str] = []

    def _stub(run: Any) -> Any:
        armed.append(run.pass_kind)
        return _AlwaysLooped()

    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE)
    await wait_until(
        lambda: len(live_frames_named("cyclic-line-pays-shown")) >= 2,
        what="the win presentation to be sampled",
    )

    monkeypatch.setattr(cyclic_service, "_loop_watch", _stub)

    # Take win, before the line messages have been round once.
    append(game_log_file, at(IDLE_STRIP_STARTED, "16:36:26.000"))
    await wait_until(
        lambda: len(live_frames_named("cyclic-idle-message-shown")) >= 4,
        what="the carried-on window to go on sampling past the strip's lap",
    )
    # Which is the claim: the between-spins strip lapping is not the end of a
    # window whose line messages have not reported finishing.
    assert armed == [], "the lap watch was armed while the line messages ran"
    assert cyclic_service.status().sampling is True

    # And here is the line that really does end them. The window is still one
    # window -- it now watches for the lap it could not watch for before.
    append(game_log_file, at(LINE_CYCLE_DONE, "16:36:30.000"))
    await wait_until(
        lambda: not cyclic_service.status().sampling,
        what="the strip's own lap to end the window, now the win pass has",
    )
    assert armed == [cyclic_service.WIN_THEN_IDLE]

    detail = await cyclic_service.stop()
    # Every frame of the spin under the one sequence, the closing line's own
    # included -- it is the last line message, not a marker on a dead window.
    finished = events_named(detail, "cyclic-line-pays-cycle-finished")
    assert len(finished) == 1
    assert finished[0].screenshot is not None
    idle = events_named(detail, "cyclic-idle-message-shown")
    assert len(idle) >= 4
    assert {event.cycle for event in idle} == {finished[0].cycle}


async def test_a_carried_window_reads_the_second_line_from_the_moment_it_appears(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """The second band is the point of carrying on, not a side effect.

    A win presentation draws one line and the between-spins strip draws two,
    and which bands a frame is read for is stamped on it when it is captured
    (see ``_Pending.regions``) -- so carrying the window on has to widen them
    at the boundary or every frame after the button press is read for the one
    band the strip has stopped using.

    Asserted on the pending reads rather than on captions, which need an OCR
    engine the suite has no business requiring.
    """
    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE)
    await wait_until(
        lambda: len(live_frames_named("cyclic-line-pays-shown")) >= 2,
        what="the win presentation to be sampled",
    )
    run = cyclic_service._run
    assert run is not None

    append(game_log_file, at(IDLE_STRIP_STARTED, "16:36:26.000"))
    await wait_until(
        lambda: len(live_frames_named("cyclic-idle-message-shown")) >= 2,
        what="the between-spins strip to start being sampled",
    )

    # Asserted as a *shape* rather than against a frame count taken before the
    # boundary, deliberately: the capture loop is still running while the
    # watcher reads the line, so where exactly the window widened is a race and
    # is not the claim. The claim is that it widened once and stayed widened --
    # one band for as long as only the win presentation was on screen, both
    # from the boundary on, and never back again.
    one = settings.cyclic_messages_text_regions
    both = settings.cyclic_messages_idle_regions
    bands = [pending.regions for pending in run.pending_reads]
    assert set(bands) == {one, both}
    widened = bands.index(both)
    assert bands[:widened] == [one] * widened
    assert bands[widened:] == [both] * (len(bands) - widened)

    await cyclic_service.stop()


async def test_a_win_taken_early_records_one_clip_of_both_strips(
    game_log_file: Path,
    quick_sampling: None,
    recorder: FakeRecorder,
    running: None,
) -> None:
    """One continuous stretch of strip is one recording.

    The ordinary case still gets a clip each -- a win presentation and the
    strip after it are two different things to watch. But a win taken early
    never lets the first one end: stopping and restarting OBS at the boundary
    is a hole in the recording at the one moment the recording is for, so the
    clip carries on and becomes a clip of both. Its kind says so, which is
    what makes ``cyclic_text.clip_regions`` crop both bands out of it.
    """
    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE)
    await wait_until(
        lambda: len(live_frames_named("cyclic-line-pays-shown")) >= 2,
        what="the win presentation to be sampled",
    )
    append(game_log_file, at(IDLE_STRIP_STARTED, "16:36:26.000"))
    await wait_until(
        lambda: len(live_frames_named("cyclic-idle-message-shown")) >= 2,
        what="the between-spins strip to start being sampled",
    )
    # Still the same recording: nothing was stopped at the boundary.
    assert recorder.files == []

    append(game_log_file, at(IDLE_STRIP_ENDED, "16:36:34.000"))
    await wait_until(
        lambda: not cyclic_service.status().sampling,
        what="the next spin to close the window",
    )
    detail = await cyclic_service.stop()

    filed = [video for video in detail.videos if video.file_name]
    assert len(filed) == 1
    assert filed[0].kind == cyclic_service.WIN_THEN_IDLE
    assert cyclic_service.WIN_THEN_IDLE in filed[0].file_name


async def test_a_win_taken_after_its_messages_finish_keeps_them_on_the_card(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """The other way round, and the one that is *two* windows.

    Let the line messages finish and the win window closes on its own line, as
    it should. Taking the win then opens the between-spins strip as a window
    of its own -- a second sequence, on the record as a second sequence,
    because that is what it is. But it is the same spin, so the live view
    keeps the win's frames and files the strip's in after them: the card a
    tester is watching grows rather than emptying.
    """
    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE)
    await wait_until(
        lambda: len(live_frames_named("cyclic-line-pays-shown")) >= 2,
        what="the win presentation to be sampled",
    )
    append(game_log_file, at(LINE_CYCLE_DONE, "16:36:30.000"))
    await wait_until(
        lambda: not cyclic_service.status().sampling,
        what="the line messages to finish",
    )
    finished = cyclic_service.live()
    win_cycle = finished.cycle
    won = len(live_frames_named("cyclic-line-pays-shown"))
    assert won >= 2

    # Take win, a beat later.
    append(game_log_file, at(IDLE_STRIP_STARTED, "16:36:35.500"))
    await wait_until(
        lambda: len(live_frames_named("cyclic-idle-message-shown")) >= 2,
        what="the between-spins strip to be sampled",
    )

    live = cyclic_service.live()
    # Named for the win it began with, and still carrying every frame of it.
    assert live.cycle == win_cycle
    assert live.strip == cyclic_service.WIN_THEN_IDLE
    assert len(live_frames_named("cyclic-line-pays-shown")) == won
    # In order, and the strip's frames after the win's rather than instead.
    assert [frame.sequence for frame in live.frames] == sorted(
        frame.sequence for frame in live.frames
    )

    append(game_log_file, at(IDLE_STRIP_ENDED, "16:36:44.000"))
    await wait_until(
        lambda: not cyclic_service.status().sampling,
        what="the next spin to close the window",
    )
    detail = await cyclic_service.stop()

    # Two sequences on the record, unlike the win taken early: here the strip
    # really did stop and start again, and the manifest should say so.
    line_pays = events_named(detail, "cyclic-line-pays-shown")
    idle = events_named(detail, "cyclic-idle-message-shown")
    assert {event.cycle for event in line_pays} != {event.cycle for event in idle}


async def test_a_losing_spin_starts_the_live_view_again(
    game_log_file: Path, quick_sampling: None, running: None
) -> None:
    """The view grows across one spin, never across two.

    A spin's two halves belong together; the spin after it does not. Without
    this the card would accumulate every window of a session and show a tester
    the spin before last beside the one in front of them.
    """
    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE)
    await wait_until(
        lambda: len(live_frames_named("cyclic-line-pays-shown")) >= 2,
        what="the win presentation to be sampled",
    )
    append(game_log_file, at(LINE_CYCLE_DONE, "16:36:30.000"))
    append(game_log_file, at(IDLE_STRIP_STARTED, "16:36:35.500"))
    await wait_until(
        lambda: len(live_frames_named("cyclic-idle-message-shown")) >= 2,
        what="the strip after the win to be sampled",
    )
    append(game_log_file, at(IDLE_STRIP_ENDED, "16:36:44.000"))
    await wait_until(
        lambda: not cyclic_service.status().sampling,
        what="the next spin to close that window",
    )

    spin = cyclic_service.live().cycle

    # The next spin loses, so its strip is nobody's second half.
    append(game_log_file, at(LOSING_SPIN, "16:36:50.000"))
    append(game_log_file, at(IDLE_STRIP_STARTED, "16:36:50.400"))
    # Waited on the view *moving*, not on frames named for the strip: the
    # previous spin's own between-spins frames carry that name too and are
    # still on the card, so a count of them is satisfied before this spin has
    # taken a single one.
    await wait_until(
        lambda: cyclic_service.live().cycle != spin,
        what="the losing spin to start the live view again",
    )

    live = cyclic_service.live()
    assert live.strip == cyclic_service.IDLE_VIDEO
    assert live_frames_named("cyclic-line-pays-shown") == []

    await cyclic_service.stop()


async def test_a_taken_win_queues_its_clip_instead_of_reading_it_back(
    game_log_file: Path,
    quick_sampling: None,
    monkeypatch: pytest.MonkeyPatch,
    recorder: FakeRecorder,
    running: None,
) -> None:
    """A clip is queued the moment it is filed, never read back inline.

    The handler that closes a window is very often about to open the next one
    -- let a win finish and take it, and the between-spins strip is on screen
    within a beat of the win's clip being filed. A decode costs seconds to
    tens of seconds, so recovering there cost that strip its first fifteen and
    the run recorded a window opening and closing with not one frame in
    between. The queue is what fixed it, and this asserts the queue rather
    than the symptom.
    """
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_RECOVER_FROM_CLIP", True)
    # Nothing in the suite can decode the recorder's one-line mp4, so the real
    # drain would fail per clip and tell us nothing. Stood in for by one that
    # merely takes its time, which is the property under test: a recovery is
    # slow, and the window after a taken win must not wait for it.
    drained: list[float] = []

    async def slow_drain(_run: Any) -> None:
        drained.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.5)

    monkeypatch.setattr(cyclic_service, "_drain_recovery", slow_drain)

    append(game_log_file, PAYTABLE, GAME_PAYS, WIN_BANG_DONE)
    await wait_until(
        lambda: cyclic_service.status().sampled_count >= 2,
        what="the win presentation to be sampled",
    )
    append(game_log_file, at(LINE_CYCLE_DONE, "16:36:30.000"))
    await wait_until(
        lambda: cyclic_service.status().recovery_pending >= 1,
        what="the win's clip to be queued for recovery",
    )

    # Queued, and the window that follows it opens anyway -- which is the
    # whole point. Recovering here, inline, is what left that strip with its
    # window opened and closed and not one frame in between.
    append(game_log_file, at(IDLE_STRIP_STARTED, "16:36:35.500"))
    await wait_until(
        lambda: len(live_frames_named("cyclic-idle-message-shown")) >= 2,
        what="the between-spins strip to be sampled while a clip waits",
    )
    assert cyclic_service.live().capturing is True

    append(game_log_file, at(IDLE_STRIP_ENDED, "16:36:44.000"))
    await wait_until(
        lambda: cyclic_service.status().recovery_pending >= 2,
        what="the between-spins clip to be queued too",
    )
    detail = await cyclic_service.stop()

    # And the drain did get asked, rather than the queue simply never being
    # worked: queued is not the same fact as dropped.
    assert drained

    # Both windows recorded a clip, and both are on the queue: the win
    # presentation and the strip that followed it.
    kinds = [video.kind for video in detail.videos if video.file_name]
    assert cyclic_service.WIN_VIDEO in kinds
    assert cyclic_service.IDLE_VIDEO in kinds
    # And it is *not* said on `errors`. A queue left unread is how a run
    # ordinarily ends -- Stop should stop rather than spend minutes on OCR --
    # so noting it there put a red line on almost every run to say something
    # the "Read the messages" button beside each clip already offers.
    assert not any("read back" in note for note in detail.errors)
