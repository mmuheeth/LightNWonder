"""Virtual OLED i-deck control endpoints.

Thin wrappers over :mod:`app.services.ideck`. Which window to drive, where the
layout lives and how presses are confirmed all come from the environment, never
from the request body, so a caller cannot aim this at an arbitrary window.

Three failure shapes recur:

- **409 ``IDECK_WINDOW_NOT_FOUND``** -- the panel is not open, or is minimized
  with restoring disabled. Fixed by launching it, not by retrying.
- **404 ``IDECK_BUTTON_NOT_FOUND``** -- no alias or layout key by that name.
- **502 ``IDECK_PRESS_NOT_CONFIRMED``** -- the press was posted but the panel
  never logged it. Worth retrying.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.ideck import (
    IDeckButton,
    IDeckStatus,
    PressRequest,
    PressResult,
    ProbeResult,
    SequenceRequest,
)
from app.schemas.response import ApiResponse
from app.services import ideck as ideck_service

router = APIRouter()

# Shared across the press routes, so declared once. The alias matches the
# signature FastAPI expects for `responses=`.
ResponseSpec = dict[int | str, dict[str, Any]]

NO_WINDOW: ResponseSpec = {
    409: {"description": "The Virtual OLED window is not usable"}
}
NO_BUTTON: ResponseSpec = {404: {"description": "No i-deck button by that name"}}
NOT_CONFIRMED: ResponseSpec = {
    502: {"description": "The press was sent but the panel never registered it"}
}
PRESS_ERRORS: ResponseSpec = {**NO_WINDOW, **NO_BUTTON, **NOT_CONFIRMED}


@router.get(
    "/status",
    response_model=ApiResponse[IDeckStatus],
    summary="i-deck panel status",
)
async def get_status() -> ApiResponse[IDeckStatus]:
    """Report whether the panel can be pressed.

    Always 200: a closed panel is a state to report, not a failed request. Check
    ``data.state`` rather than the status code.
    """
    state = await ideck_service.status()
    return ApiResponse[IDeckStatus].ok(
        data=state, message=f"The i-deck is {state.state.value}"
    )


@router.get(
    "/buttons",
    response_model=ApiResponse[list[IDeckButton]],
    summary="List i-deck buttons",
    responses={500: {"description": "The layout or game config is unreadable"}},
)
async def get_buttons() -> ApiResponse[list[IDeckButton]]:
    """Every key on the deck, in layout order.

    Client coordinates are populated only while the panel is open and restored.
    """
    keys = await ideck_service.buttons()
    return ApiResponse[list[IDeckButton]].ok(
        data=keys, message=f"{len(keys)} i-deck buttons available"
    )


@router.post(
    "/press",
    response_model=ApiResponse[PressResult],
    summary="Press one button",
    responses=PRESS_ERRORS,
)
async def press(payload: PressRequest) -> ApiResponse[PressResult]:
    """Press a key by configured alias or by layout name.

    Unless verification is turned off, this only succeeds once the panel's own
    log shows the press landing.
    """
    result = await ideck_service.press(
        payload.button, verify=payload.verify, hold_seconds=payload.hold_seconds
    )
    return ApiResponse[PressResult].ok(data=result, message=f"Pressed {result.button}")


@router.post(
    "/sequence",
    response_model=ApiResponse[list[PressResult]],
    summary="Press several buttons in order",
    responses=PRESS_ERRORS,
)
async def press_sequence(payload: SequenceRequest) -> ApiResponse[list[PressResult]]:
    """Press keys in order, pausing between them.

    Stops at the first failure, so a partially completed run reports the error
    rather than a success.
    """
    results = await ideck_service.press_sequence(
        payload.buttons, delay_seconds=payload.delay_seconds, verify=payload.verify
    )
    return ApiResponse[list[PressResult]].ok(
        data=results, message=f"Pressed {len(results)} buttons"
    )


@router.post(
    "/probe",
    response_model=ApiResponse[ProbeResult],
    summary="Check the panel accepts posted input",
)
async def probe() -> ApiResponse[ProbeResult]:
    """Post a mouse move and nothing else, to prove input reaches the panel.

    Always 200, and always free of game side effects -- run this before any real
    press when setting the integration up.
    """
    result = await ideck_service.probe()
    return ApiResponse[ProbeResult].ok(data=result, message=result.detail)
