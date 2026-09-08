"""Game-window click endpoints, thin wrappers over :mod:`app.services.game_input`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.game_input import (
    ClickRequest,
    ClickResult,
    ClickTargetInfo,
    GameInputStatus,
)
from app.schemas.response import ApiResponse
from app.services import game_input as game_input_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]  # matches FastAPI's `responses=`

NO_WINDOW: ResponseSpec = {409: {"description": "The game window is not usable"}}
NO_TARGET: ResponseSpec = {
    404: {"description": "The active game configures no target by that name"}
}
NOT_CONFIRMED: ResponseSpec = {
    502: {"description": "The click was sent but the game never registered it"}
}
BAD_CONFIG: ResponseSpec = {
    500: {"description": "The game config or one of its targets is unreadable"}
}
CLICK_ERRORS: ResponseSpec = {
    **NO_WINDOW,
    **NO_TARGET,
    **NOT_CONFIRMED,
    **BAD_CONFIG,
}


@router.get(
    "/status",
    response_model=ApiResponse[GameInputStatus],
    summary="Game window status",
)
async def get_status() -> ApiResponse[GameInputStatus]:
    """Report whether the game window can be clicked; always 200 -- check
    ``data.state``, not the status code."""
    state = await game_input_service.status()
    return ApiResponse[GameInputStatus].ok(
        data=state, message=f"The game window is {state.state.value}"
    )


@router.get(
    "/targets",
    response_model=ApiResponse[list[ClickTargetInfo]],
    summary="List the active game's click targets",
    responses=BAD_CONFIG,
)
async def get_targets() -> ApiResponse[list[ClickTargetInfo]]:
    """Every target the active game declares, in name order. Client
    coordinates populate only while the game window is open and restored."""
    found = await game_input_service.targets()
    return ApiResponse[list[ClickTargetInfo]].ok(
        data=found, message=f"{len(found)} click targets configured"
    )


@router.post(
    "/click",
    response_model=ApiResponse[ClickResult],
    summary="Click one configured target",
    responses=CLICK_ERRORS,
)
async def click(payload: ClickRequest) -> ApiResponse[ClickResult]:
    """Click a target by name; unless verification is off, only succeeds once
    the game's log shows it reacting (``data.confirmed_by`` says how strongly)."""
    result = await game_input_service.click(
        payload.target, verify=payload.verify, hold_seconds=payload.hold_seconds
    )
    return ApiResponse[ClickResult].ok(
        data=result, message=f"Clicked {result.target} on {result.game}"
    )
