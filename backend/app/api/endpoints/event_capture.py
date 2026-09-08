"""Event capture endpoints, thin wrappers over :mod:`app.services.event_capture`. One
run exists process-wide, so two callers pointed at the same backend share it."""

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
    """Report the run in progress; always 200 -- check ``data.active``, not
    the status code."""
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
    """Begin a run against the active game; connects to OBS first so a run
    never ends up with events but no screenshots."""
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
    """Every run on disk, newest first; unpaginated since runs are created by
    hand and the list stays small."""
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
    # Raw image, not the envelope: an <img> src can't unwrap JSON.
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
