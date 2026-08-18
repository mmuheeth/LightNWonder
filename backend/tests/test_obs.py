"""OBS Studio endpoints.

The real socket never opens here. ``FakeClient`` stands in for
``simpleobsws.WebSocketClient`` and is installed over the service's private
``_client`` global, matching how ``test_health.py`` swaps ``health._PROBES``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.services import obs as obs_service
from tests.asserts import assert_failure, assert_success

PNG_DATA_URI = "data:image/png;base64,iVBORw0KGgo="

VERSION_DATA = {
    "obsVersion": "30.2.3",
    "obsWebSocketVersion": "5.5.4",
    "platform": "windows",
}
SCENE_DATA = {"sceneName": "Main", "sceneUuid": "abc-123"}
SCENE_ITEMS_DATA = {"sceneItems": [{"sceneItemId": 7, "sourceName": "Game Window"}]}
INPUTS_DATA = {"inputs": [{"inputName": "Game Window", "inputKind": "window_capture"}]}
IDLE_RECORD_DATA = {
    "outputActive": False,
    "outputPaused": False,
    "outputTimecode": "00:00:00.000",
    "outputDuration": 0,
    "outputBytes": 0,
}
ACTIVE_RECORD_DATA = {
    "outputActive": True,
    "outputPaused": False,
    "outputTimecode": "00:00:12.500",
    "outputDuration": 12500,
    "outputBytes": 2048,
}
PAUSED_RECORD_DATA = {**ACTIVE_RECORD_DATA, "outputPaused": True}

# Enough to satisfy every request the status endpoint makes.
CONNECTED_RESPONSES: dict[str, dict[str, Any]] = {
    "GetVersion": VERSION_DATA,
    "GetCurrentProgramScene": SCENE_DATA,
    "GetRecordStatus": IDLE_RECORD_DATA,
}


class FakeStatus:
    """Stands in for ``simpleobsws.RequestStatus``."""

    def __init__(self, *, result: bool, code: int = 100, comment: str | None = None):
        self.result = result
        self.code = code
        self.comment = comment


class FakeResponse:
    """Stands in for ``simpleobsws.RequestResponse``."""

    def __init__(self, data: dict[str, Any] | None, status: FakeStatus):
        self.responseData = data
        self.requestStatus = status

    def ok(self) -> bool:
        return self.requestStatus.result


class FakeClient:
    """Stands in for ``simpleobsws.WebSocketClient``.

    ``responses`` maps a request type to the data OBS would return. A request
    type absent from the map is answered as a failure, which is how the
    "OBS rejected it" path gets exercised.
    """

    def __init__(
        self,
        *,
        responses: dict[str, dict[str, Any]] | None = None,
        identified: bool = True,
        reconnects: bool = True,
    ):
        self.responses = responses if responses is not None else CONNECTED_RESPONSES
        self.identified = identified
        self.reconnects = reconnects
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.disconnected = False

    def is_identified(self) -> bool:
        return self.identified

    async def connect(self) -> bool:
        return True

    # `timeout` is unused but must keep its name on both of these: the
    # service passes it by keyword.
    async def wait_until_identified(self, timeout: float = 10) -> bool:  # noqa: ARG002
        self.identified = self.reconnects
        return self.identified

    async def call(
        self,
        request: Any,
        timeout: float | None = None,  # noqa: ARG002
    ) -> FakeResponse:
        self.calls.append((request.requestType, request.requestData))
        if request.requestType not in self.responses:
            return FakeResponse(
                None,
                FakeStatus(result=False, code=604, comment="Request not supported"),
            )
        return FakeResponse(
            dict(self.responses[request.requestType]), FakeStatus(result=True)
        )

    async def disconnect(self) -> bool:
        self.disconnected = True
        self.identified = False
        return True

    def request_types(self) -> list[str]:
        return [name for name, _ in self.calls]

    def data_for(self, request_type: str) -> dict[str, Any] | None:
        for name, data in self.calls:
            if name == request_type:
                return data
        return None


def install(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> FakeClient:
    """Make ``client`` the service's live session."""
    monkeypatch.setattr(obs_service, "_client", client)
    return client


# --- status ---------------------------------------------------------------


async def test_status_reports_disconnected_without_a_session(
    client: AsyncClient,
) -> None:
    """No session is a state to report, not an error: still 200."""
    response = await client.get("/api/obs/status")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["state"] == "disconnected"
    assert data["url"] == settings.obs_url
    assert data["recording"] is None
    assert data["obs_version"] is None


async def test_status_reports_what_obs_says_when_connected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeClient())

    response = await client.get("/api/obs/status")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["state"] == "connected"
    assert data["obs_version"] == "30.2.3"
    assert data["obs_websocket_version"] == "5.5.4"
    assert data["platform"] == "windows"
    assert data["current_scene"] == "Main"
    assert data["recording"]["active"] is False


# --- not connected --------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/api/obs/recording"),
        ("post", "/api/obs/recording/start"),
        ("post", "/api/obs/recording/stop"),
        ("post", "/api/obs/recording/pause"),
        ("post", "/api/obs/recording/resume"),
        ("post", "/api/obs/screenshot"),
        ("post", "/api/obs/select-game-window"),
    ],
)
async def test_actions_require_a_connection(
    client: AsyncClient, method: str, url: str
) -> None:
    """409 rather than 503: the caller fixes this by connecting, not retrying."""
    response = await client.request(method, url, json={})

    assert response.status_code == 409
    assert_failure(response.json(), code="OBS_NOT_CONNECTED")


async def test_disconnect_is_idempotent(client: AsyncClient) -> None:
    response = await client.post("/api/obs/disconnect")

    assert response.status_code == 200
    assert assert_success(response.json())["state"] == "disconnected"


async def test_disconnect_closes_a_live_session(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(monkeypatch, FakeClient())

    response = await client.post("/api/obs/disconnect")

    assert response.status_code == 200
    assert fake.disconnected is True
    assert obs_service._client is None


async def test_select_game_window_uses_the_active_config_process(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(
        monkeypatch,
        FakeClient(
            responses={
                **CONNECTED_RESPONSES,
                "GetSceneItemList": SCENE_ITEMS_DATA,
                "GetInputList": INPUTS_DATA,
                "SetInputSettings": {},
            }
        ),
    )

    response = await client.post("/api/obs/select-game-window")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["game"] == "HuffNPuffLink"
    assert data["process"] == "HuffNPuffLink.exe"
    assert data["source_name"] == "Game Window"
    assert fake.data_for("SetInputSettings") == {
        "inputName": "Game Window",
        "inputSettings": {"window": "::HuffNPuffLink.exe", "priority": 2},
        "overlay": True,
    }


# --- screenshots ----------------------------------------------------------


async def test_screenshot_defaults_to_the_current_program_scene(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """obs-websocket has no program-output source, so the scene is resolved."""
    fake = install(
        monkeypatch,
        FakeClient(
            responses={
                "GetCurrentProgramScene": SCENE_DATA,
                "GetSourceScreenshot": {"imageData": PNG_DATA_URI},
            }
        ),
    )

    response = await client.post("/api/obs/screenshot", json={"width": 1280})

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["source_name"] == "Main"
    assert data["image_data"] == PNG_DATA_URI
    assert data["file_path"] is None

    assert "GetCurrentProgramScene" in fake.request_types()
    sent = fake.data_for("GetSourceScreenshot")
    assert sent == {
        "sourceName": "Main",
        "imageFormat": "png",
        "imageCompressionQuality": -1,
        "imageWidth": 1280,
    }


async def test_screenshot_honours_an_explicit_source(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(
        monkeypatch,
        FakeClient(responses={"GetSourceScreenshot": {"imageData": PNG_DATA_URI}}),
    )

    response = await client.post(
        "/api/obs/screenshot", json={"source_name": "Webcam", "image_format": "jpeg"}
    )

    assert response.status_code == 200
    assert assert_success(response.json())["source_name"] == "Webcam"
    # No scene lookup is needed when the caller names the source.
    assert "GetCurrentProgramScene" not in fake.request_types()


async def test_screenshot_to_a_file_returns_a_path_inside_the_screenshot_root(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(monkeypatch, FakeClient(responses={"SaveSourceScreenshot": {}}))

    response = await client.post(
        "/api/obs/screenshot",
        json={"source_name": "Main", "file_name": "shot"},
    )

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["image_data"] is None

    saved = Path(data["file_path"])
    assert saved.is_absolute()
    assert saved.is_relative_to(settings.obs_screenshot_dir)
    # A missing suffix is filled in from image_format.
    assert saved.name == "shot.png"
    assert fake.data_for("SaveSourceScreenshot") == {
        "sourceName": "Main",
        "imageFormat": "png",
        "imageCompressionQuality": -1,
        "imageFilePath": str(saved),
    }


async def test_screenshot_can_use_a_use_case_subdirectory(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(monkeypatch, FakeClient(responses={"SaveSourceScreenshot": {}}))

    response = await client.post(
        "/api/obs/screenshot",
        json={
            "source_name": "Main",
            "file_name": "frame",
            "output_dir": "ir-inspection/session-01",
        },
    )

    assert response.status_code == 200
    saved = Path(assert_success(response.json())["file_path"])
    assert (
        saved
        == settings.obs_screenshot_dir / "ir-inspection" / "session-01" / "frame.png"
    )
    assert fake.data_for("SaveSourceScreenshot")["imageFilePath"] == str(saved)


async def test_screenshot_output_dir_requires_a_file_name(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeClient(responses={"SaveSourceScreenshot": {}}))

    response = await client.post(
        "/api/obs/screenshot", json={"output_dir": "ir-inspection"}
    )

    assert response.status_code == 422
    assert_failure(response.json(), code="VALIDATION_ERROR")


@pytest.mark.parametrize("output_dir", ["../escape", "..\\escape", "C:/escape"])
async def test_screenshot_rejects_output_dirs_outside_the_capture_root(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, output_dir: str
) -> None:
    fake = install(monkeypatch, FakeClient(responses={"SaveSourceScreenshot": {}}))

    response = await client.post(
        "/api/obs/screenshot",
        json={"source_name": "Main", "file_name": "frame", "output_dir": output_dir},
    )

    assert response.status_code == 400
    error = assert_failure(response.json(), code="BAD_REQUEST")
    assert error["details"][0]["field"] == "body.output_dir"
    assert fake.calls == []


@pytest.mark.parametrize(
    "file_name",
    ["../escape.png", "sub/escape.png", "..\\escape.png", "C:/escape.png", ".."],
)
async def test_screenshot_rejects_names_that_escape_the_capture_dir(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, file_name: str
) -> None:
    """The endpoint must not become an arbitrary-file-write primitive."""
    fake = install(monkeypatch, FakeClient(responses={"SaveSourceScreenshot": {}}))

    response = await client.post(
        "/api/obs/screenshot",
        json={"source_name": "Main", "file_name": file_name},
    )

    assert response.status_code == 400
    error = assert_failure(response.json(), code="BAD_REQUEST")
    assert error["details"][0]["field"] == "body.file_name"
    # The rejected name never reaches OBS.
    assert fake.calls == []


@pytest.mark.parametrize(("field", "value"), [("width", 4), ("height", 5000)])
async def test_screenshot_dimensions_are_bounded(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, field: str, value: int
) -> None:
    install(monkeypatch, FakeClient())

    response = await client.post("/api/obs/screenshot", json={field: value})

    assert response.status_code == 422
    error = assert_failure(response.json(), code="VALIDATION_ERROR")
    assert error["details"][0]["field"] == f"body.{field}"


async def test_screenshot_without_image_data_is_an_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(
        monkeypatch,
        FakeClient(
            responses={
                "GetCurrentProgramScene": SCENE_DATA,
                "GetSourceScreenshot": {},
            }
        ),
    )

    response = await client.post("/api/obs/screenshot", json={})

    assert response.status_code == 502
    assert_failure(response.json(), code="OBS_REQUEST_FAILED")


# --- recording ------------------------------------------------------------


async def test_recording_status(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeClient(responses={"GetRecordStatus": ACTIVE_RECORD_DATA}))

    response = await client.get("/api/obs/recording")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["active"] is True
    assert data["paused"] is False
    assert data["duration_ms"] == 12500
    assert data["bytes_written"] == 2048
    assert data["timecode"] == "00:00:12.500"


async def test_start_recording_reports_the_new_state(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(
        monkeypatch,
        FakeClient(
            responses={
                "SetRecordDirectory": {},
                "StartRecord": {},
                "GetRecordStatus": ACTIVE_RECORD_DATA,
            }
        ),
    )

    response = await client.post("/api/obs/recording/start")

    assert response.status_code == 200
    assert assert_success(response.json())["active"] is True
    # StartRecord returns no data, so the state is re-read afterwards.
    assert fake.request_types() == [
        "SetRecordDirectory",
        "StartRecord",
        "GetRecordStatus",
    ]


async def test_start_recording_can_use_a_use_case_subdirectory(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(
        monkeypatch,
        FakeClient(
            responses={
                "SetRecordDirectory": {},
                "StartRecord": {},
                "GetRecordStatus": ACTIVE_RECORD_DATA,
            }
        ),
    )

    response = await client.post(
        "/api/obs/recording/start", json={"output_dir": "video/session-01"}
    )

    assert response.status_code == 200
    assert assert_success(response.json())["active"] is True
    assert fake.data_for("SetRecordDirectory") == {
        "recordDirectory": str(settings.obs_recording_dir / "video" / "session-01")
    }


async def test_stop_recording_returns_the_output_path(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(
        monkeypatch,
        FakeClient(
            responses={
                "StopRecord": {"outputPath": "C:/captures/take-1.mkv"},
                "GetRecordStatus": IDLE_RECORD_DATA,
            }
        ),
    )

    response = await client.post("/api/obs/recording/stop")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["active"] is False
    assert data["output_path"] == "C:/captures/take-1.mkv"


async def test_stop_recording_tolerates_a_missing_output_path(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """obs-websocket documents outputPath on an event, not on StopRecord."""
    install(
        monkeypatch,
        FakeClient(responses={"StopRecord": {}, "GetRecordStatus": IDLE_RECORD_DATA}),
    )

    response = await client.post("/api/obs/recording/stop")

    assert response.status_code == 200
    assert assert_success(response.json())["output_path"] is None


async def test_pause(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(
        monkeypatch,
        FakeClient(
            responses={"PauseRecord": {}, "GetRecordStatus": PAUSED_RECORD_DATA}
        ),
    )

    response = await client.post("/api/obs/recording/pause")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["active"] is True
    assert data["paused"] is True
    assert "PauseRecord" in fake.request_types()


async def test_resume(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(
        monkeypatch,
        FakeClient(
            responses={"ResumeRecord": {}, "GetRecordStatus": ACTIVE_RECORD_DATA}
        ),
    )

    response = await client.post("/api/obs/recording/resume")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["paused"] is False
    assert "ResumeRecord" in fake.request_types()


# --- failure translation --------------------------------------------------


async def test_a_rejected_request_becomes_a_502(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown request type makes FakeClient answer ok() == False."""
    install(monkeypatch, FakeClient(responses={"GetRecordStatus": IDLE_RECORD_DATA}))

    response = await client.post("/api/obs/recording/start")

    assert response.status_code == 502
    assert_failure(response.json(), code="OBS_REQUEST_FAILED")
    assert "604" in response.json()["message"]


async def test_a_dropped_socket_reconnects_on_the_next_call(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lazy reconnect: a dead session heals without an explicit /connect."""
    fake = install(
        monkeypatch,
        FakeClient(responses={"GetRecordStatus": ACTIVE_RECORD_DATA}, identified=False),
    )

    response = await client.get("/api/obs/recording")

    assert response.status_code == 200
    assert assert_success(response.json())["active"] is True
    assert fake.is_identified() is True


async def test_a_socket_that_will_not_reconnect_is_a_502(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeClient(identified=False, reconnects=False))

    response = await client.get("/api/obs/recording")

    assert response.status_code == 502
    assert_failure(response.json(), code="OBS_CONNECTION_FAILED")


async def test_status_falls_back_to_disconnected_when_obs_stops_answering(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/status must not fail; an unreadable OBS is reported as disconnected."""
    install(monkeypatch, FakeClient(responses={}))

    response = await client.get("/api/obs/status")

    assert response.status_code == 200
    assert assert_success(response.json())["state"] == "disconnected"


# --- connect --------------------------------------------------------------


async def test_connect_reuses_a_live_session(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(
        monkeypatch,
        FakeClient(responses={**CONNECTED_RESPONSES, "SetRecordDirectory": {}}),
    )

    response = await client.post("/api/obs/connect")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["state"] == "connected"
    assert data["obs_version"] == "30.2.3"
    # The default recording root is handed to OBS so recordings land beside shots.
    assert fake.data_for("SetRecordDirectory") == {
        "recordDirectory": str(settings.obs_recording_dir)
    }


async def test_connect_survives_obs_refusing_to_set_the_record_directory(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An older OBS lacking the request must not block connecting."""
    install(monkeypatch, FakeClient(responses=CONNECTED_RESPONSES))

    response = await client.post("/api/obs/connect")

    assert response.status_code == 200
    assert assert_success(response.json())["state"] == "connected"


class SettlingClient(FakeClient):
    """Reports the old record state once before flipping, as OBS really does."""

    def __init__(self, *, stale: dict[str, Any], fresh: dict[str, Any]):
        super().__init__(
            responses={
                "SetRecordDirectory": {},
                "StartRecord": {},
                "StopRecord": {},
            }
        )
        self._states = [stale, fresh]

    async def call(
        self,
        request: Any,
        timeout: float | None = None,  # noqa: ARG002
    ) -> FakeResponse:
        if request.requestType == "GetRecordStatus":
            self.calls.append((request.requestType, request.requestData))
            state = self._states[0] if len(self._states) == 1 else self._states.pop(0)
            return FakeResponse(dict(state), FakeStatus(result=True))
        return await super().call(request)


async def test_start_waits_for_obs_to_actually_flip_the_output(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OBS flips asynchronously, so the first status read is stale."""
    fake = install(
        monkeypatch,
        SettlingClient(stale=IDLE_RECORD_DATA, fresh=ACTIVE_RECORD_DATA),
    )

    response = await client.post("/api/obs/recording/start")

    assert response.status_code == 200
    # Reporting the first read would have answered active: false.
    assert assert_success(response.json())["active"] is True
    assert fake.request_types().count("GetRecordStatus") == 2


async def test_stop_waits_for_obs_to_actually_flip_the_output(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(
        monkeypatch,
        SettlingClient(stale=ACTIVE_RECORD_DATA, fresh=IDLE_RECORD_DATA),
    )

    response = await client.post("/api/obs/recording/stop")

    assert response.status_code == 200
    assert assert_success(response.json())["active"] is False


async def test_a_stuck_output_is_reported_rather_than_hanging(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If OBS never flips, answer with what it last said instead of blocking."""
    monkeypatch.setattr(obs_service, "_SETTLE_ATTEMPTS", 2)
    monkeypatch.setattr(obs_service, "_SETTLE_DELAY_SECONDS", 0.0)
    install(
        monkeypatch,
        FakeClient(
            responses={
                "SetRecordDirectory": {},
                "StartRecord": {},
                "GetRecordStatus": IDLE_RECORD_DATA,
            }
        ),
    )

    response = await client.post("/api/obs/recording/start")

    assert response.status_code == 200
    assert assert_success(response.json())["active"] is False
