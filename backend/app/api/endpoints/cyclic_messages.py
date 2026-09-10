"""Cyclic message endpoints, thin wrappers over :mod:`app.services.cyclic_messages`.
One run exists process-wide, so two callers pointed at the same backend share it.

Independent of event capture's run: the two follow the same log with different
rules, and either may run without the other.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.schemas.cyclic_messages import (
    CyclicRunDetail,
    CyclicRunSummary,
    CyclicStatus,
    CyclicTextReading,
)
from app.schemas.response import ApiResponse
from app.services import cyclic_messages as cyclic_service
from app.services import cyclic_text as cyclic_text_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

RUN_CONFLICT: ResponseSpec = {
    409: {"description": "The run is not in the required state"}
}
RUN_NOT_FOUND: ResponseSpec = {404: {"description": "No such cyclic message run"}}


@router.get(
    "/status",
    response_model=ApiResponse[CyclicStatus],
    summary="Cyclic message capture status",
)
async def get_status() -> ApiResponse[CyclicStatus]:
    """Report the run in progress; always 200 -- check ``data.active``, not
    the status code."""
    state = cyclic_service.status()
    return ApiResponse[CyclicStatus].ok(
        data=state,
        message=(
            f"Cyclic message run {state.run_id} is in progress"
            if state.active
            else "No cyclic message run is in progress"
        ),
    )


@router.post(
    "/start",
    response_model=ApiResponse[CyclicStatus],
    summary="Start tracking the active game's cyclic messages",
    responses={
        **RUN_CONFLICT,
        502: {"description": "OBS is unreachable or refused the request"},
    },
)
async def start() -> ApiResponse[CyclicStatus]:
    """Begin a run against the active game; connects to OBS first so a run
    never ends up with events but no screenshots. Recording does *not* start
    here -- a clip is one win presentation, so it opens when the game pays."""
    return ApiResponse[CyclicStatus].ok(
        data=await cyclic_service.start(), message="Cyclic message capture started"
    )


@router.post(
    "/stop",
    response_model=ApiResponse[CyclicRunDetail],
    summary="Stop tracking and seal the record",
    responses=RUN_CONFLICT,
)
async def stop() -> ApiResponse[CyclicRunDetail]:
    """End the run and return the finished record, events and clips included."""
    detail = await cyclic_service.stop()
    return ApiResponse[CyclicRunDetail].ok(
        data=detail,
        message=(
            f"Cyclic message capture stopped with {detail.message_count} messages "
            f"over {detail.cycle_count} loops"
        ),
    )


@router.get(
    "/runs",
    response_model=ApiResponse[list[CyclicRunSummary]],
    summary="List cyclic message runs",
)
async def list_runs() -> ApiResponse[list[CyclicRunSummary]]:
    """Every run on disk, newest first; unpaginated since runs are created by
    hand and the list stays small."""
    runs = cyclic_service.list_runs()
    return ApiResponse[list[CyclicRunSummary]].ok(
        data=runs, message=f"Found {len(runs)} cyclic message runs"
    )


@router.get(
    "/runs/{run_id}",
    response_model=ApiResponse[CyclicRunDetail],
    summary="One cyclic message run",
    responses=RUN_NOT_FOUND,
)
async def get_run(run_id: str) -> ApiResponse[CyclicRunDetail]:
    """One run and all of its events, read back from its manifest."""
    return ApiResponse[CyclicRunDetail].ok(
        data=cyclic_service.get_run(run_id),
        message="Cyclic message run retrieved successfully",
    )


@router.get(
    "/runs/{run_id}/text",
    response_model=ApiResponse[CyclicTextReading],
    summary="Read the messages out of a run's clip",
    responses={
        **RUN_NOT_FOUND,
        400: {"description": "The run has several clips and none was named"},
        409: {"description": "Tesseract is not installed or OCR is disabled"},
    },
)
async def read_text(
    run_id: str,
    cycle: int | None = Query(
        default=None,
        ge=1,
        description=(
            "Which win presentation's clip to read. Required only when the run "
            "recorded more than one, since reading the wrong win would give a "
            "complete-looking answer about the wrong spin."
        ),
    ),
    interval_seconds: float | None = Query(
        default=None,
        gt=0,
        le=10,
        description=(
            "Gap between sampled frames. Defaults to "
            "CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS, which is deliberately "
            "shorter than a message dwells -- raising it past ~1.3s starts "
            "dropping messages without saying so."
        ),
    ),
) -> ApiResponse[CyclicTextReading]:
    """Cut the clip into frames, crop the caption out of each and read it.

    Slow by nature -- a 90s clip is ~180 frames and as many Tesseract calls --
    so this is a request that takes tens of seconds rather than milliseconds.
    """
    reading = await cyclic_text_service.read_run(
        run_id, cycle=cycle, interval_seconds=interval_seconds
    )
    return ApiResponse[CyclicTextReading].ok(
        data=reading,
        message=(
            f"Read {len(reading.messages)} messages from "
            f"{reading.frames_sampled} frames of {reading.file_name}"
        ),
    )


@router.get(
    "/runs/{run_id}/files/{file_name}",
    response_class=FileResponse,
    # Raw file, not the envelope: an <img> src and a <video> src can't unwrap
    # JSON. One route for both, since a run directory holds both.
    response_model=None,
    summary="One screenshot or one clip from a run",
    responses={
        **RUN_NOT_FOUND,
        200: {
            "content": {"image/png": {}, "video/mp4": {}},
            "description": "The file",
        },
    },
)
async def get_file(run_id: str, file_name: str) -> FileResponse:
    """Serve one captured image, or one of the run's win-presentation clips."""
    return FileResponse(cyclic_service.file_path(run_id, file_name))
