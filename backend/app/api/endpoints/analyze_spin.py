"""Analyze Spin endpoints, thin wrappers over :mod:`app.services.analyze_spin`.

One run exists process-wide, like event capture, so two browsers pointed at the
same backend watch the same spin.

``/start`` returns as soon as the run is under way rather than when it finishes:
a spin takes tens of seconds and the point of the feature is watching it happen.
Progress arrives on ``/stream``, which is the one place in this API that does
*not* speak the response envelope -- a WebSocket frame is not a response, has no
request id, and cannot carry a status code. Each frame is a whole
:class:`~app.schemas.analyze_spin.SpinAnalysisState`, not a delta, so a client
that joins late or drops a frame is still correct. Images are left out of every
frame; ``GET /status?include_images=true`` is the report.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, Query, WebSocket
from fastapi.responses import FileResponse

from app.core.logging import get_logger
from app.schemas.analyze_spin import SpinAnalysisState, SpinStartRequest
from app.schemas.response import ApiResponse
from app.services import analyze_spin as analyze_spin_service

logger = get_logger("analyze_spin.api")

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

RUN_CONFLICT: ResponseSpec = {
    409: {
        "description": (
            "A run is already going, or none is; or the active game has no log "
            "to follow a spin through"
        )
    }
}
BAD_CONFIG: ResponseSpec = {500: {"description": "The game config is unreadable"}}


def _frame(state: SpinAnalysisState) -> dict[str, Any]:
    """One stream frame. Deliberately the payload itself and not the envelope --
    see the module docstring."""
    return state.model_dump(mode="json", by_alias=True)


@router.get(
    "/status",
    response_model=ApiResponse[SpinAnalysisState],
    summary="The spin in progress, or the last one",
)
async def get_status(
    include_images: bool = Query(
        default=False,
        description=(
            "Also return the meter crops, the annotated reels and the paying "
            "lines' own pictures as data URIs. Off by default: this is the same "
            "endpoint a page polls, and the pictures are the report."
        ),
    ),
) -> ApiResponse[SpinAnalysisState]:
    """Always 200 -- check ``data.active``, not the status code. ``data.run``
    outlives its own run, so a page that reloads after a spin still shows it."""
    state = analyze_spin_service.state(images=include_images)
    run = state.run
    if run is None:
        message = "No spin has been analysed yet"
    elif state.active:
        message = f"Spin {run.run_id} is in progress: {run.message}"
    else:
        message = f"Spin {run.run_id} {run.state.value}: {run.message}"
    return ApiResponse[SpinAnalysisState].ok(data=state, message=message)


@router.post(
    "/start",
    response_model=ApiResponse[SpinAnalysisState],
    summary="Spin once, and validate it",
    responses={**RUN_CONFLICT, **BAD_CONFIG},
)
async def start(body: SpinStartRequest | None = None) -> ApiResponse[SpinAnalysisState]:
    """Press spin on the active game, follow it to its result, and run the cash
    meter and payline validations over the screenshots it took.

    Returns immediately with the run's opening state; follow ``/stream`` (or
    poll ``/status``) for the rest. Only the preconditions this process can
    check without touching the machine -- the game config parsing, its log
    existing -- refuse the request; everything else fails on its own step, with
    the error the equivalent direct request would have given.

    An empty body (or none at all) uses ``ANALYZE_SPIN_RECORD``; ``record`` in
    the body overrides it for this run only."""
    state = await analyze_spin_service.start(record=body.record if body else None)
    run = state.run
    return ApiResponse[SpinAnalysisState].ok(
        data=state,
        message=(f"Spin {run.run_id} started" if run is not None else "Spin started"),
    )


@router.post(
    "/cancel",
    response_model=ApiResponse[SpinAnalysisState],
    summary="Ask the run in progress to stop",
    responses=RUN_CONFLICT,
)
async def cancel() -> ApiResponse[SpinAnalysisState]:
    """Cooperative: the run stops itself at its next check, tidying up its
    recording and sealing its record on the way, so this returns before the run
    has actually ended. Watch ``state`` for ``cancelled``."""
    return ApiResponse[SpinAnalysisState].ok(
        data=await analyze_spin_service.cancel(),
        message="The spin analysis was asked to stop",
    )


@router.get(
    "/frames/{file_name}",
    response_class=FileResponse,
    # Raw image, not the envelope: an <img> src can't unwrap JSON. Same
    # exception, and same reason, as a capture run's screenshots.
    response_model=None,
    summary="One screenshot a run took",
    responses={
        404: {"description": "No screenshot of that name"},
        400: {"description": "Not a bare filename"},
        200: {"content": {"image/png": {}}, "description": "The image"},
    },
)
async def get_frame(file_name: str) -> FileResponse:
    """Serve one of the frames named in ``run.frames``. They live in the
    dashboard's screenshot directory rather than a per-run one, because that is
    where the ROI and reel-grid services read a frame by name."""
    return FileResponse(analyze_spin_service.frame_path(file_name))


async def _push(websocket: WebSocket, queue: asyncio.Queue[SpinAnalysisState]) -> None:
    """Forward every snapshot the service publishes."""
    while True:
        await websocket.send_json(_frame(await queue.get()))


async def _watch(websocket: WebSocket) -> None:
    """Consume whatever the client sends, and return when it goes away.

    Reading is not optional even though this stream is one-way: without it a
    closed browser tab is only noticed on the next send, which for an idle
    service may be never.
    """
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


@router.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    """Follow a run as it happens.

    Sends the current state on connect -- so a client that opens the socket
    mid-spin, or after one finished, renders immediately without also having to
    fetch ``/status`` -- and then one frame per change.
    """
    await websocket.accept()
    with analyze_spin_service.subscribe() as queue:
        pusher = asyncio.create_task(_push(websocket, queue), name="spin-stream-push")
        watcher = asyncio.create_task(_watch(websocket), name="spin-stream-watch")
        try:
            await websocket.send_json(_frame(analyze_spin_service.state()))
            done, pending = await asyncio.wait(
                {pusher, watcher}, return_when=asyncio.FIRST_COMPLETED
            )
        except Exception:
            done, pending = set(), {pusher, watcher}
            logger.debug("Spin progress stream ended before it began", exc_info=True)

        for task in pending:
            task.cancel()
        # Awaited, not merely cancelled: an unawaited cancellation is a pending
        # task warning, and a finished task's exception has to be retrieved.
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            with contextlib.suppress(Exception):
                task.result()

    with contextlib.suppress(RuntimeError):
        await websocket.close()
