"""OBS Studio control endpoints.

Thin wrappers over :mod:`app.services.obs`. Connection settings come from the
environment, never from the request body, so the OBS password never travels to
or from the browser.

Two failure shapes recur across this module:

- **409 ``OBS_NOT_CONNECTED``** -- nothing is connected yet. The caller fixes
  this by connecting, not by retrying.
- **502 ``OBS_CONNECTION_FAILED`` / ``OBS_REQUEST_FAILED``** -- OBS is
  unreachable, or refused the request. Worth retrying.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.obs import (
    ObsGameWindowSelection,
    ObsRecordStatus,
    ObsStatus,
    RecordStartRequest,
    ScreenshotRequest,
    ScreenshotResult,
)
from app.schemas.response import ApiResponse
from app.services import obs as obs_service

router = APIRouter()

# Shared across nine routes, so declared once. The alias matches the signature
# FastAPI expects for `responses=`.
ResponseSpec = dict[int | str, dict[str, Any]]

NOT_CONNECTED: ResponseSpec = {409: {"description": "Not connected to OBS"}}
OBS_UNREACHABLE: ResponseSpec = {
    502: {"description": "OBS is unreachable or refused the request"}
}
OBS_ERRORS: ResponseSpec = {**NOT_CONNECTED, **OBS_UNREACHABLE}


@router.get(
    "/status",
    response_model=ApiResponse[ObsStatus],
    summary="OBS connection status",
)
async def get_status() -> ApiResponse[ObsStatus]:
    """Report the OBS connection state.

    Always 200: a closed OBS is a state to report, not a failed request. Check
    ``data.state`` rather than the status code.
    """
    state = await obs_service.status()
    return ApiResponse[ObsStatus].ok(data=state, message=f"OBS is {state.state.value}")


@router.post(
    "/connect",
    response_model=ApiResponse[ObsStatus],
    summary="Connect to OBS",
    responses=OBS_UNREACHABLE,
)
async def connect() -> ApiResponse[ObsStatus]:
    """Open a session with OBS. Idempotent: a live session is reused."""
    return ApiResponse[ObsStatus].ok(
        data=await obs_service.connect(), message="Connected to OBS"
    )


@router.post(
    "/disconnect",
    response_model=ApiResponse[ObsStatus],
    summary="Disconnect from OBS",
)
async def disconnect() -> ApiResponse[ObsStatus]:
    """Close the OBS session. Idempotent, and never fails."""
    return ApiResponse[ObsStatus].ok(
        data=await obs_service.disconnect(), message="Disconnected from OBS"
    )


@router.post(
    "/select-game-window",
    response_model=ApiResponse[ObsGameWindowSelection],
    summary="Select the active game's OBS window",
    responses=OBS_ERRORS,
)
async def select_game_window() -> ApiResponse[ObsGameWindowSelection]:
    """Point the active scene's window-capture source at the active game process."""
    selection = await obs_service.select_current_game_window()
    return ApiResponse[ObsGameWindowSelection].ok(
        data=selection, message="OBS window source selected for the active game"
    )


@router.post(
    "/screenshot",
    response_model=ApiResponse[ScreenshotResult],
    summary="Capture a screenshot",
    responses={
        **OBS_ERRORS,
        400: {"description": "file_name or output_dir is outside its allowed root"},
    },
)
async def take_screenshot(payload: ScreenshotRequest) -> ApiResponse[ScreenshotResult]:
    """Capture a source or scene.

    Omit ``file_name`` for a base64 data URI in the response; supply one to have
    OBS write the file into the configured screenshot directory instead. Use
    ``output_dir`` for a relative use-case subdirectory.
    """
    result = await obs_service.take_screenshot(payload)
    return ApiResponse[ScreenshotResult].ok(
        data=result, message="Screenshot captured successfully"
    )


@router.get(
    "/recording",
    response_model=ApiResponse[ObsRecordStatus],
    summary="Recording status",
    responses=OBS_ERRORS,
)
async def get_recording() -> ApiResponse[ObsRecordStatus]:
    """Report whether OBS is recording, and for how long."""
    return ApiResponse[ObsRecordStatus].ok(
        data=await obs_service.record_status(),
        message="Recording status retrieved successfully",
    )


@router.post(
    "/recording/start",
    response_model=ApiResponse[ObsRecordStatus],
    summary="Start recording",
    responses={
        **OBS_ERRORS,
        400: {"description": "output_dir is outside the allowed root"},
    },
)
async def start_recording(
    payload: RecordStartRequest | None = None,
) -> ApiResponse[ObsRecordStatus]:
    """Start recording, optionally in a relative use-case subdirectory."""
    return ApiResponse[ObsRecordStatus].ok(
        data=await obs_service.start_recording(
            output_dir=payload.output_dir if payload else None
        ),
        message="Recording started",
    )


@router.post(
    "/recording/stop",
    response_model=ApiResponse[ObsRecordStatus],
    summary="Stop recording",
    responses=OBS_ERRORS,
)
async def stop_recording() -> ApiResponse[ObsRecordStatus]:
    """Stop recording and report the output path when OBS supplies one."""
    return ApiResponse[ObsRecordStatus].ok(
        data=await obs_service.stop_recording(), message="Recording stopped"
    )


@router.post(
    "/recording/pause",
    response_model=ApiResponse[ObsRecordStatus],
    summary="Pause recording",
    responses=OBS_ERRORS,
)
async def pause_recording() -> ApiResponse[ObsRecordStatus]:
    """Pause the running recording."""
    return ApiResponse[ObsRecordStatus].ok(
        data=await obs_service.pause_recording(), message="Recording paused"
    )


@router.post(
    "/recording/resume",
    response_model=ApiResponse[ObsRecordStatus],
    summary="Resume recording",
    responses=OBS_ERRORS,
)
async def resume_recording() -> ApiResponse[ObsRecordStatus]:
    """Resume a paused recording."""
    return ApiResponse[ObsRecordStatus].ok(
        data=await obs_service.resume_recording(), message="Recording resumed"
    )
