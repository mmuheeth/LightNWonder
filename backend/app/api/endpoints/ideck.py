"""Virtual OLED i-deck control endpoints, thin wrappers over
:mod:`app.services.ideck`. Window, layout and confirmation all come from the
environment, never the request body."""

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

ResponseSpec = dict[int | str, dict[str, Any]]  # matches FastAPI's `responses=`

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
    """Report whether the panel can be pressed; always 200 -- check
    ``data.state``, not the status code."""
    state = await ideck_service.status()
    return ApiResponse[IDeckStatus].ok(
        data=state, message=f"The i-deck is {state.state.value}"
    )


@router.get(
    "/buttons",
    response_model=ApiResponse[list[IDeckButton]],
    summary="List i-deck buttons",
    responses={500: {"description": "The panel layout is unreadable"}},
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
    """Press a key by its layout name, matched case-insensitively.

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
