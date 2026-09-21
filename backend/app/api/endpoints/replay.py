"""Replay endpoints, thin wrappers over :mod:`app.services.replay`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.schemas.replay import ReplayRun, ReplayStatus
from app.schemas.response import ApiResponse
from app.services import replay as replay_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]  # matches FastAPI's `responses=`

RUN_ERRORS: ResponseSpec = {
    409: {"description": "A replay sequence is already in progress"},
    503: {"description": "This host has no Windows API to drive windows with"},
}

SHOT_NOT_FOUND: ResponseSpec = {
    404: {"description": "No replay screenshot by that name has been written"}
}


@router.get(
    "/status",
    response_model=ApiResponse[ReplayStatus],
    summary="Replay readiness, and the run in progress",
)
async def get_status() -> ApiResponse[ReplayStatus]:
    """Report what a replay would find if it ran now, plus the run in progress
    or the last one this process finished; always 200 -- check ``data.windows``
    and ``data.run``, not the status code.

    **This is also the progress endpoint.** ``data.run`` carries every step
    filling in, the run's own log, and the screenshot from the moment it is
    taken, so following a sequence is polling one thing rather than subscribing
    to another."""
    state = await replay_service.status()
    ready = sum(1 for window in state.windows if window.state == "ready")
    message = (
        state.run.message
        if state.running and state.run is not None
        else f"{ready} of {len(state.windows)} windows are ready"
    )
    return ApiResponse[ReplayStatus].ok(data=state, message=message)


@router.post(
    "/run",
    response_model=ApiResponse[ReplayRun],
    summary="Replay the latest game play",
    responses=RUN_ERRORS,
)
async def run() -> ApiResponse[ReplayRun]:
    """Start the whole sequence: open DevTool, connect it, press the attendant
    key, bring the attendant menu forward, open Events / History, open the Game
    Play tab and wait for its records, view the newest one, bring the game
    forward, photograph the replay, then exit the replay and the menu.

    **Returns as soon as it has started**, with every step listed and pending.
    The sequence takes tens of seconds -- most of them waiting on the menu's
    server or on a window coming forward -- so holding the request open for it
    would mean a caller could only ever show the outcome. Follow it on
    ``GET /status``, whose ``data.run`` is this same record filling in.

    A step that fails is **not** an HTTP failure: it lands on that record with
    ``state`` of ``failed`` and the error on the step that stopped it, because
    the record of how far the sequence got is the useful part and the envelope
    carries no data on a failure. The only HTTP failures are refusals to start
    -- 409 for a run already in progress, 503 for a host with no Windows API.
    """
    started = await replay_service.start()
    return ApiResponse[ReplayRun].ok(data=started, message=started.message)


@router.get(
    "/screenshot/{file_name}",
    response_class=FileResponse,
    # Raw image, not the envelope: an <img> src can't unwrap JSON.
    response_model=None,
    summary="The picture a run took of the replay",
    responses={
        **SHOT_NOT_FOUND,
        200: {"content": {"image/png": {}}, "description": "The image"},
    },
)
async def get_screenshot(file_name: str) -> FileResponse:
    """Serve one replay screenshot by the name on the run's record.

    Served as a file rather than carried on the record as a data URI because
    that record is polled while the run walks on: these are portrait cabinet
    canvases, and a base64 copy of one costs more per poll than the sequence
    being reported.
    """
    return FileResponse(replay_service.screenshot_path(file_name))
