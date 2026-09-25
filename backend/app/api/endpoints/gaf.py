"""GAF automation endpoints, thin wrappers over :mod:`app.services.gaf`.

Which game, where its automation service listens and what its controls are
called all come from the active game's config, not from the request -- so a
spin is a request with no required body at all, and the same call drives any
game that declares a ``gaf`` block.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.gaf import (
    GafStatus,
    SpinRequest,
    SpinResult,
    TakeWinRequest,
    TakeWinResult,
)
from app.schemas.response import ApiResponse
from app.services import gaf as gaf_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]  # matches FastAPI's `responses=`

NOT_READY: ResponseSpec = {
    409: {
        "description": (
            "The active game declares no GAF config, the NRobot server is not "
            "running, the object-query files are missing, or the game is "
            "still playing"
        )
    }
}
NOT_DRIVEN: ResponseSpec = {
    502: {"description": "The session could not be opened, or the game said no"}
}
ACTION_ERRORS: ResponseSpec = {**NOT_READY, **NOT_DRIVEN}


@router.get(
    "/status",
    response_model=ApiResponse[GafStatus],
    summary="GAF automation status",
)
async def get_status() -> ApiResponse[GafStatus]:
    """Report whether the game can be driven; always 200 -- check
    ``data.state``, not the status code. Reading this never opens a session."""
    state = await gaf_service.status()
    return ApiResponse[GafStatus].ok(data=state, message=state.detail)


@router.post(
    "/connect",
    response_model=ApiResponse[GafStatus],
    summary="Open an automation session",
    responses=ACTION_ERRORS,
)
async def connect() -> ApiResponse[GafStatus]:
    """Open the session now rather than on the first action, so the ~2.4s
    cold start is not charged to a spin."""
    state = await gaf_service.connect()
    return ApiResponse[GafStatus].ok(data=state, message=state.detail)


@router.post(
    "/disconnect",
    response_model=ApiResponse[GafStatus],
    summary="Close the automation session",
)
async def disconnect() -> ApiResponse[GafStatus]:
    """Release the game. A session left open blocks the next client."""
    state = await gaf_service.disconnect()
    return ApiResponse[GafStatus].ok(data=state, message=state.detail)


@router.post(
    "/spin",
    response_model=ApiResponse[SpinResult],
    summary="Spin the reels",
    responses=ACTION_ERRORS,
)
async def spin(payload: SpinRequest | None = None) -> ApiResponse[SpinResult]:
    """Press the mechanical spin button and wait for the spin to finish.

    Opens a session if there isn't one. A win holds the game in play, so
    ``outcome`` of ``win_offered`` is a finished spin with money on the
    table, not a failure -- collect it with ``/take-win``. A game that is
    *already* held that way answers 409 rather than spinning on top of it.
    """
    request = payload or SpinRequest()
    result = await gaf_service.spin(
        settle=request.settle,
        force=request.force,
        timeout_seconds=request.timeout_seconds,
        read_meters=request.read_meters,
    )
    return ApiResponse[SpinResult].ok(data=result, message=result.detail)


@router.post(
    "/take-win",
    response_model=ApiResponse[TakeWinResult],
    summary="Collect a win",
    responses=ACTION_ERRORS,
)
async def take_win(payload: TakeWinRequest | None = None) -> ApiResponse[TakeWinResult]:
    """Press the take-win button and wait for the game to return to idle.

    Nothing to collect answers 200 with ``pressed: false`` -- a real state,
    and a different fact from a press that failed.
    """
    request = payload or TakeWinRequest()
    result = await gaf_service.take_win(
        force=request.force,
        settle=request.settle,
        timeout_seconds=request.timeout_seconds,
        read_meters=request.read_meters,
    )
    return ApiResponse[TakeWinResult].ok(data=result, message=result.detail)
