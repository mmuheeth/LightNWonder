"""Event Based Capture endpoints.

Thin wrappers over :mod:`app.services.event_capture`. One run exists at a time
for the whole backend process, so these are process-wide controls rather than
per-caller ones -- two browsers pointed at the same backend share a run.

Recurring failures:

- **409 ``EVENT_CAPTURE_ALREADY_RUNNING`` / ``EVENT_CAPTURE_NOT_RUNNING``** --
  the caller is out of step with the run; refreshing status fixes it.
- **409 ``EVENT_CAPTURE_LOG_UNAVAILABLE``** -- the selected game declares no log,
  or the game is not running yet.
- **409 / 502 ``OBS_*``** -- starting connects to OBS, so its failures surface
  here too.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.schemas.event_capture import (
    CaptureRunDetail,
    CaptureRunSummary,
    CaptureStatus,
)
from app.schemas.response import ApiResponse
from app.services import event_capture as capture_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

RUN_CONFLICT: ResponseSpec = {
    409: {"description": "The run is not in the required state"}
}
RUN_NOT_FOUND: ResponseSpec = {404: {"description": "No such capture run"}}


@router.get(
    "/status",
    response_model=ApiResponse[CaptureStatus],
    summary="Event capture status",
)
async def get_status() -> ApiResponse[CaptureStatus]:
    """Report the run in progress.

    Always 200: not tracking is a state to report, not a failed request. Check
    ``data.active`` rather than the status code.
    """
    state = capture_service.status()
    return ApiResponse[CaptureStatus].ok(
        data=state,
        message=(
            f"Capture run {state.run_id} is in progress"
            if state.active
            else "No capture run is in progress"
        ),
    )


@router.post(
    "/start",
    response_model=ApiResponse[CaptureStatus],
    summary="Start tracking the active game's log",
    responses={
        **RUN_CONFLICT,
        502: {"description": "OBS is unreachable or refused the request"},
    },
)
async def start() -> ApiResponse[CaptureStatus]:
    """Begin a run against the active game.

    Connects to OBS first and fails if it cannot, so a run never ends up as a
    list of events with no screenshots.
    """
    return ApiResponse[CaptureStatus].ok(
        data=await capture_service.start(), message="Event capture started"
    )


@router.post(
    "/stop",
    response_model=ApiResponse[CaptureRunDetail],
    summary="Stop tracking and seal the record",
    responses=RUN_CONFLICT,
)
async def stop() -> ApiResponse[CaptureRunDetail]:
    """End the run and return the finished record, events included."""
    detail = await capture_service.stop()
    return ApiResponse[CaptureRunDetail].ok(
        data=detail,
        message=f"Event capture stopped with {detail.event_count} events",
    )


@router.get(
    "/runs",
    response_model=ApiResponse[list[CaptureRunSummary]],
    summary="List capture runs",
)
async def list_runs() -> ApiResponse[list[CaptureRunSummary]]:
    """Every run on disk, newest first.

    Deliberately unpaginated: runs are created by hand, one per session, so the
    list stays small enough that paging would be ceremony.
    """
    runs = capture_service.list_runs()
    return ApiResponse[list[CaptureRunSummary]].ok(
        data=runs, message=f"Found {len(runs)} capture runs"
    )


@router.get(
    "/runs/{run_id}",
    response_model=ApiResponse[CaptureRunDetail],
    summary="One capture run",
    responses=RUN_NOT_FOUND,
)
async def get_run(run_id: str) -> ApiResponse[CaptureRunDetail]:
    """One run and all of its events, read back from its manifest."""
    return ApiResponse[CaptureRunDetail].ok(
        data=capture_service.get_run(run_id),
        message="Capture run retrieved successfully",
    )


@router.get(
    "/runs/{run_id}/screenshots/{file_name}",
    response_class=FileResponse,
    # The one endpoint in the service that does not return the envelope. An
    # <img> src cannot unwrap JSON, and base64-ing every screenshot into a list
    # response would make the captures page tens of megabytes. Both path
    # segments come off the wire, so both are resolved through the path guards
    # in `app.utils.paths` before anything is opened.
    response_model=None,
    summary="One screenshot from a run",
    responses={
        **RUN_NOT_FOUND,
        200: {"content": {"image/png": {}}, "description": "The image"},
    },
)
async def get_screenshot(run_id: str, file_name: str) -> FileResponse:
    """Serve one captured image."""
    return FileResponse(capture_service.screenshot_path(run_id, file_name))
