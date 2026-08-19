"""Game-window click endpoints.

Thin wrappers over :mod:`app.services.game_input`. Which window to drive comes
from the environment and the active game config, and *where* to click comes only
from that game's ``button_targets`` block -- never from the request body -- so a
caller can reach the buttons a game declares and no other pixel.

Four failure shapes recur:

- **409 ``GAME_WINDOW_NOT_FOUND``** -- the game is not running, or is minimized
  with restoring disabled. Fixed by launching it, not by retrying.
- **409 ``GAME_INPUT_ACCESS_DENIED``** -- Windows is refusing us input because the
  game outranks this backend. Fixed by running the backend elevated.
- **404 ``GAME_TARGET_NOT_FOUND``** -- the active game configures no target by
  that name. ``GET /targets`` lists the ones it does.
- **502 ``GAME_CLICK_NOT_CONFIRMED``** -- the click was posted but the game never
  reacted. The message distinguishes the two causes: a touch the game felt but
  no target event means the coordinates need re-measuring, while no touch at all
  means the input never arrived.
"""

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

# Shared across the routes below, so declared once. The alias matches the
# signature FastAPI expects for `responses=`.
ResponseSpec = dict[int | str, dict[str, Any]]

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
    """Report whether the game window can be clicked.

    Always 200: a closed game is a state to report, not a failed request. Check
    ``data.state`` rather than the status code.
    """
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
    """Every target the active game declares, in name order.

    Client coordinates are populated only while the game window is open and
    restored, which makes this the thing to read when a coordinate needs checking
    against a screenshot.
    """
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
    """Click a target by the name the active game's config gives it.

    Unless verification is turned off, this only succeeds once the game's own log
    shows it reacting. ``data.confirmed_by`` says how strong that proof was:
    ``target-event`` means the game named the button it hit, ``touch`` means it
    only admitted feeling a touch somewhere.
    """
    result = await game_input_service.click(
        payload.target, verify=payload.verify, hold_seconds=payload.hold_seconds
    )
    return ApiResponse[ClickResult].ok(
        data=result, message=f"Clicked {result.target} on {result.game}"
    )
