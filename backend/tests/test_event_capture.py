"""Event Based Capture: the run engine and its endpoints.

No OBS socket opens here. ``obs_service.connect`` and ``take_screenshot`` are
monkeypatched, which is enough because this module's job is the log-to-record
pipeline, not the OBS conversation -- ``test_obs.py`` covers that.

The awkward part of testing this is that the watcher is a real background task,
so a test has to wait for it rather than call it. :func:`wait_for_events` polls
the service's own status instead of sleeping for a fixed time, so the tests stay
fast and do not go flaky on a loaded machine.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.config.game_config import save_active_game
from app.config.runtime import settings
from app.exceptions.base import ObsConnectionError
from app.schemas.event_capture import CaptureRunState
from app.schemas.obs import ScreenshotRequest, ScreenshotResult
from app.services import event_capture as capture_service
from app.services import obs as obs_service
from tests.asserts import assert_failure, assert_success

API = "/api/event-capture"

# Verbatim lines, as in test_game_log.py -- see that module's docstring.
SPIN = (
    "08/18/26 19:46:20.196 00 FortuneOx:9244 DBG: [MessageQueue.Publish] "
    "msg[GDK.Common.ServerAPI.SpinButtonMsg]"
)
# The echo the same spin produces, which no rule should match.
SPIN_ECHO = (
    "08/18/26 19:46:20.197 01 FortuneOx:9244 INF: StateMachine[GameStateMachine] "
    "[Non-queued] [GDK.Common.ServerAPI.SpinButtonMsg] not handled by state [stateEnd]"
)
DENOM = (
    "08/18/26 19:47:07.702 00 FortuneOx:9244 INF: [WagerGameApp.UpdateDenom] "
    "New denom[1.000] Did denom Change[True]"
)
NOISE = (
    "08/18/26 19:46:21.000 00 FortuneOx:9244 DBG: "
    "AssetBundleUtility.LoadAssetBundleHelper(): Finish Load AssetBundle"
)
BET = (
    "08/18/26 19:46:20.196 04 FortuneOx:9244 INF: [BetManager.UpdateCurrentBet]"
    "[CurrentBet {{ BetsPerUnit:100.000, UnitData:[ units: 5, cost: 10 ], "
    "TotalBetCost:1000.000, TotalBetValue:100.000, DirectPlayData:{{ "
    "DirectPlayType:NotDirectPlay, BonusID:, BonusIndex:0, BonusOption:0 }} }}]"
)


def spin_at(time: str) -> str:
    """A spin line at a given ``HH:MM:SS.mmm``, for exercising the debounce."""
    return SPIN.replace("19:46:20.196", time)


def bet_at(time: str, total_bet: str) -> str:
    """A bet line at a given time carrying a given total."""
    return BET.replace("19:46:20.196", time).replace(
        "TotalBetValue:100.000", f"TotalBetValue:{total_bet}"
    )


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def game_log(tmp_path: Path) -> Path:
    """A log file standing in for the one the game writes."""
    log = tmp_path / "FortuneOx_Client.log"
    log.write_text("", encoding="utf-8")
    return log


@pytest.fixture
def capture_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the screenshot root at a temp directory.

    ``obs_screenshot_dir`` is a property, so it is patched on the class rather
    than the instance.
    """
    root = tmp_path / "obs-captured-files"
    root.mkdir()
    monkeypatch.setattr(
        type(settings), "obs_screenshot_dir", property(lambda _self: root)
    )
    return root / settings.EVENT_CAPTURE_DIR_NAME


@pytest.fixture
def active_game(
    tmp_path: Path, game_log: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """A game config directory holding one game that points at ``game_log``."""
    directory = tmp_path / "games"
    directory.mkdir()
    (directory / "FortuneOx.json").write_text(
        json.dumps(
            {"name": "FortuneOx", "process": "FortuneOx.exe", "log": str(game_log)}
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


@pytest.fixture
def fake_obs(monkeypatch: pytest.MonkeyPatch) -> list[ScreenshotRequest]:
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
async def running(
    active_game: Path, capture_root: Path, fake_obs: list[ScreenshotRequest]
) -> AsyncIterator[None]:
    """A started run, stopped afterwards even if the test fails."""
    await capture_service.start()
    try:
        yield
    finally:
        if capture_service.status().active:
            await capture_service.stop()


# --- helpers --------------------------------------------------------------


def append(log: Path, *lines: str) -> None:
    """Write lines the way the game does: opened, appended, closed."""
    with log.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


async def wait_for_events(count: int, *, timeout: float = 5.0) -> None:
    """Wait until the watcher has captured ``count`` events.

    Polling the real status rather than sleeping a fixed time keeps the tests
    quick without making them depend on how loaded the machine is.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while capture_service.status().event_count < count:
        if asyncio.get_running_loop().time() >= deadline:
            state = capture_service.status()
            raise AssertionError(
                f"expected {count} events, saw {state.event_count} "
                f"(errors: {state.errors})"
            )
        await asyncio.sleep(0.02)


def read_manifest(capture_root: Path, run_id: str) -> dict[str, Any]:
    document: Any = json.loads(
        (capture_root / run_id / capture_service.MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(document, dict)
    return document


# --- starting and stopping ------------------------------------------------


async def test_status_reports_nothing_running_by_default() -> None:
    state = capture_service.status()

    assert state.active is False
    assert state.run_id is None
    assert state.event_count == 0


async def test_start_creates_a_run_directory_and_manifest(
    active_game: Path, capture_root: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    state = await capture_service.start()

    assert state.active is True
    assert state.run_id is not None
    assert state.game == "FortuneOx"
    # The record exists from the first moment, so a crash still leaves one.
    manifest = read_manifest(capture_root, state.run_id)
    assert manifest["status"] == CaptureRunState.RUNNING
    assert manifest["events"] == []

    await capture_service.stop()


async def test_a_second_start_is_refused(running: None) -> None:
    with pytest.raises(Exception) as raised:
        await capture_service.start()

    assert "already in progress" in str(raised.value)


async def test_stop_without_a_run_is_refused() -> None:
    with pytest.raises(Exception) as raised:
        await capture_service.stop()

    assert "nothing to stop" in str(raised.value)


async def test_start_is_refused_when_the_game_declares_no_log(
    tmp_path: Path,
    capture_root: Path,
    fake_obs: list[ScreenshotRequest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run with no log to follow would silently record nothing."""
    directory = tmp_path / "games"
    directory.mkdir()
    (directory / "Bare.json").write_text(json.dumps({"name": "Bare"}), encoding="utf-8")
    monkeypatch.setattr(
        type(settings), "ideck_active_game", property(lambda _self: "Bare")
    )
    monkeypatch.setattr(
        type(settings),
        "ideck_game_config_path_for",
        lambda _self, game: directory / f"{game}.json",
    )

    with pytest.raises(Exception) as raised:
        await capture_service.start()

    assert "does not declare a 'log' path" in str(raised.value)
    assert capture_service.status().active is False


async def test_start_is_refused_when_the_log_is_not_there_yet(
    active_game: Path,
    game_log: Path,
    capture_root: Path,
    fake_obs: list[ScreenshotRequest],
) -> None:
    """Which is what "the game is not running" looks like from here."""
    game_log.unlink()

    with pytest.raises(Exception) as raised:
        await capture_service.start()

    assert "does not exist yet" in str(raised.value)


async def test_start_is_refused_when_obs_cannot_be_reached(
    active_game: Path, capture_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Better a clear failure than a run that records events and no images."""

    async def refuse() -> None:
        raise ObsConnectionError("OBS is not running")

    monkeypatch.setattr(obs_service, "connect", refuse)

    with pytest.raises(ObsConnectionError):
        await capture_service.start()

    assert capture_service.status().active is False


# --- capturing ------------------------------------------------------------


async def test_an_event_produces_a_screenshot_and_a_record(
    running: None, game_log: Path, capture_root: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    append(game_log, SPIN)
    await wait_for_events(1)

    detail = await capture_service.stop()

    assert detail.status == CaptureRunState.COMPLETED
    assert detail.event_count == 1
    event = detail.events[0]
    assert event.sequence == 1
    assert event.event == "spin-started"
    assert event.summary == "Spin requested"
    assert event.log_line == SPIN
    assert event.capture_error is None
    # The file OBS was asked for is really there, under the run directory.
    assert event.screenshot is not None
    assert (capture_root / detail.run_id / event.screenshot).is_file()
    assert (
        fake_obs[0].output_dir == f"{settings.EVENT_CAPTURE_DIR_NAME}/{detail.run_id}"
    )


async def test_the_screenshot_name_carries_the_sequence_and_the_event(
    running: None, game_log: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    """So the folder is readable without opening the manifest."""
    append(game_log, SPIN)
    await wait_for_events(1)

    detail = await capture_service.stop()

    assert detail.events[0].screenshot is not None
    assert detail.events[0].screenshot.startswith("001_spin-started_")
    assert detail.events[0].screenshot.endswith(".png")


async def test_unrecognised_lines_are_ignored(
    running: None, game_log: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    append(game_log, NOISE, SPIN, NOISE)
    await wait_for_events(1)

    detail = await capture_service.stop()

    assert [event.event for event in detail.events] == ["spin-started"]


async def test_the_echo_of_an_event_does_not_capture_twice(
    running: None, game_log: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    """The 'not handled by state' lines are the noisiest thing in the log."""
    append(game_log, SPIN, SPIN_ECHO)
    await wait_for_events(1)

    detail = await capture_service.stop()

    assert len(detail.events) == 1


async def test_the_same_event_twice_in_a_moment_is_debounced(
    running: None, game_log: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    """One event is often logged twice, milliseconds apart, in two shapes."""
    append(game_log, spin_at("19:46:20.196"), spin_at("19:46:20.201"))
    await wait_for_events(1)

    detail = await capture_service.stop()

    assert len(detail.events) == 1


async def test_the_same_event_later_is_a_new_event(
    running: None, game_log: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    """The debounce must not eat a second spin that really happened."""
    append(game_log, spin_at("19:46:20.196"), spin_at("19:46:25.400"))
    await wait_for_events(2)

    detail = await capture_service.stop()

    assert [event.sequence for event in detail.events] == [1, 2]


async def test_lines_written_before_the_run_started_are_not_captured(
    active_game: Path,
    game_log: Path,
    capture_root: Path,
    fake_obs: list[ScreenshotRequest],
) -> None:
    """The run covers this session, not the game's whole history."""
    append(game_log, SPIN, DENOM)

    await capture_service.start()
    append(game_log, spin_at("19:48:00.000"))
    await wait_for_events(1)
    detail = await capture_service.stop()

    assert len(detail.events) == 1
    assert detail.events[0].at is not None
    assert detail.events[0].at.hour == 19
    assert detail.events[0].at.minute == 48


async def test_a_rotated_log_is_followed_into_its_replacement(
    running: None, game_log: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    """The game caps its logs around 20 MB and starts over.

    The noise matters: a rotation is recognised by the file being *shorter* than
    the cursor, so the log has to be meaningfully long before it is truncated,
    the way a real one is.
    """
    append(game_log, SPIN, *([NOISE] * 50))
    await wait_for_events(1)

    game_log.write_text("", encoding="utf-8")  # rotation: the file starts over
    append(game_log, DENOM)
    await wait_for_events(2)

    detail = await capture_service.stop()

    assert [event.event for event in detail.events] == [
        "spin-started",
        "denomination-changed",
    ]


async def test_a_failed_screenshot_is_recorded_without_ending_the_run(
    running: None, game_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OBS dropping its socket mid-run is exactly this case."""

    async def refuse(_payload: ScreenshotRequest) -> ScreenshotResult:
        raise ObsConnectionError("OBS went away")

    monkeypatch.setattr(obs_service, "take_screenshot", refuse)

    append(game_log, SPIN)
    await wait_for_events(1)

    state = capture_service.status()
    assert state.active is True, "one bad screenshot must not end the run"

    detail = await capture_service.stop()
    assert detail.events[0].screenshot is None
    assert detail.events[0].capture_error == "OBS went away"
    assert detail.errors  # surfaced, not swallowed


async def test_status_reports_progress_while_running(
    running: None, game_log: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    append(game_log, SPIN, DENOM)
    await wait_for_events(2)

    state = capture_service.status()

    assert state.active is True
    assert state.event_count == 2
    assert [event.event for event in state.recent_events] == [
        "spin-started",
        "denomination-changed",
    ]
    assert state.duration_ms >= 0


async def test_the_manifest_is_written_as_the_run_goes(
    running: None, game_log: Path, capture_root: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    """So a crash at minute 58 does not cost the whole session."""
    append(game_log, SPIN)
    await wait_for_events(1)

    run_id = capture_service.status().run_id
    assert run_id is not None
    manifest = read_manifest(capture_root, run_id)

    assert manifest["status"] == CaptureRunState.RUNNING
    assert len(manifest["events"]) == 1


async def test_a_game_rule_disabled_in_config_does_not_fire(
    tmp_path: Path,
    game_log: Path,
    capture_root: Path,
    fake_obs: list[ScreenshotRequest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "games"
    directory.mkdir()
    (directory / "FortuneOx.json").write_text(
        json.dumps(
            {
                "name": "FortuneOx",
                "log": str(game_log),
                "events": {"disable": ["spin-started"]},
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

    await capture_service.start()
    append(game_log, SPIN, DENOM)
    await wait_for_events(1)
    detail = await capture_service.stop()

    assert [event.event for event in detail.events] == ["denomination-changed"]


async def test_a_rule_that_only_fires_on_a_change_ignores_the_same_values_again(
    tmp_path: Path,
    game_log: Path,
    capture_root: Path,
    fake_obs: list[ScreenshotRequest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HuffNPuffLink re-logs its bet several times a round without it changing.

    The debounce cannot help: those lines are seconds apart and genuinely
    separate. What makes them a non-event is that nothing in them moved.
    """
    directory = tmp_path / "games"
    directory.mkdir()
    (directory / "FortuneOx.json").write_text(
        json.dumps(
            {
                "name": "FortuneOx",
                "log": str(game_log),
                "events": {
                    "rules": [
                        {
                            "event": "bet-changed",
                            "pattern": r"TotalBetValue:(?P<total_bet>[\d.]+)",
                            "summary": "Bet is {total_bet}",
                            "only_on_change": True,
                        }
                    ]
                },
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

    await capture_service.start()
    append(
        game_log,
        bet_at("19:46:20.196", "100.000"),
        bet_at("19:46:31.400", "100.000"),
        bet_at("19:46:44.900", "200.000"),
    )
    await wait_for_events(2)
    detail = await capture_service.stop()

    assert [event.fields["total_bet"] for event in detail.events] == [
        "100.000",
        "200.000",
    ]


# --- shutdown -------------------------------------------------------------


async def test_abort_seals_the_record_as_interrupted(
    running: None, game_log: Path, capture_root: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    """Nobody pressed stop, and a reader should be able to tell."""
    append(game_log, SPIN)
    await wait_for_events(1)
    run_id = capture_service.status().run_id
    assert run_id is not None

    await capture_service.abort()

    assert capture_service.status().active is False
    assert read_manifest(capture_root, run_id)["status"] == (
        CaptureRunState.INTERRUPTED
    )


async def test_abort_without_a_run_does_nothing() -> None:
    await capture_service.abort()

    assert capture_service.status().active is False


# --- reading runs back ----------------------------------------------------


async def test_list_runs_is_empty_before_anything_has_run(capture_root: Path) -> None:
    assert capture_service.list_runs() == []


async def test_a_finished_run_can_be_read_back(
    running: None, game_log: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    append(game_log, SPIN)
    await wait_for_events(1)
    stopped = await capture_service.stop()

    listed = capture_service.list_runs()
    fetched = capture_service.get_run(stopped.run_id)

    assert [summary.run_id for summary in listed] == [stopped.run_id]
    assert listed[0].event_count == 1
    assert fetched.events[0].event == "spin-started"
    assert fetched.status == CaptureRunState.COMPLETED


async def test_an_unreadable_manifest_drops_out_of_the_listing(
    running: None, capture_root: Path, fake_obs: list[ScreenshotRequest]
) -> None:
    """One hand-edited file should not break the captures page."""
    run_id = capture_service.status().run_id
    assert run_id is not None
    await capture_service.stop()
    (capture_root / run_id / capture_service.MANIFEST_NAME).write_text(
        "{ not json", encoding="utf-8"
    )

    assert capture_service.list_runs() == []


@pytest.mark.parametrize(
    "run_id",
    [
        pytest.param("nope", id="unknown"),
        pytest.param("../secrets", id="traversal"),
        pytest.param("..", id="parent"),
    ],
)
def test_an_unknown_or_unsafe_run_id_is_rejected(
    capture_root: Path, run_id: str
) -> None:
    with pytest.raises(Exception) as raised:
        capture_service.get_run(run_id)

    assert "EventCaptureRunNotFound" in type(raised.value).__name__


# --- endpoints ------------------------------------------------------------


async def test_status_endpoint_reports_idle(client: AsyncClient) -> None:
    response = await client.get(f"{API}/status")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["active"] is False


async def test_start_and_stop_over_http(
    client: AsyncClient,
    active_game: Path,
    game_log: Path,
    capture_root: Path,
    fake_obs: list[ScreenshotRequest],
) -> None:
    started = assert_success((await client.post(f"{API}/start")).json())
    assert started["active"] is True

    append(game_log, SPIN)
    await wait_for_events(1)

    stopped = assert_success((await client.post(f"{API}/stop")).json())

    assert stopped["status"] == CaptureRunState.COMPLETED
    assert stopped["event_count"] == 1
    assert stopped["events"][0]["event"] == "spin-started"


async def test_stopping_when_idle_is_a_conflict(client: AsyncClient) -> None:
    response = await client.post(f"{API}/stop")

    assert response.status_code == 409
    assert_failure(response.json(), code="EVENT_CAPTURE_NOT_RUNNING")


async def test_starting_twice_is_a_conflict(client: AsyncClient, running: None) -> None:
    response = await client.post(f"{API}/start")

    assert response.status_code == 409
    assert_failure(response.json(), code="EVENT_CAPTURE_ALREADY_RUNNING")


async def test_runs_endpoint_lists_finished_runs(
    client: AsyncClient,
    running: None,
    game_log: Path,
    fake_obs: list[ScreenshotRequest],
) -> None:
    append(game_log, SPIN)
    await wait_for_events(1)
    stopped = await capture_service.stop()

    data = assert_success((await client.get(f"{API}/runs")).json())

    assert [run["run_id"] for run in data] == [stopped.run_id]


async def test_a_screenshot_is_served_as_a_file(
    client: AsyncClient,
    running: None,
    game_log: Path,
    fake_obs: list[ScreenshotRequest],
) -> None:
    """The one endpoint that deliberately does not return the envelope."""
    append(game_log, SPIN)
    await wait_for_events(1)
    stopped = await capture_service.stop()
    name = stopped.events[0].screenshot
    assert name is not None

    response = await client.get(f"{API}/runs/{stopped.run_id}/screenshots/{name}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


async def test_an_unknown_run_is_a_404(client: AsyncClient, capture_root: Path) -> None:
    response = await client.get(f"{API}/runs/2020-01-01_00-00-00")

    assert response.status_code == 404
    assert_failure(response.json(), code="EVENT_CAPTURE_RUN_NOT_FOUND")


async def test_a_screenshot_name_cannot_escape_its_run(
    client: AsyncClient,
    running: None,
    game_log: Path,
    fake_obs: list[ScreenshotRequest],
) -> None:
    """Both path segments come off the wire, so both are guarded."""
    run_id = capture_service.status().run_id
    assert run_id is not None

    response = await client.get(f"{API}/runs/{run_id}/screenshots/..%2F..%2Frun.json")

    assert response.status_code == 404
