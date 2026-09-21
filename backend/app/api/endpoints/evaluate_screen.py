"""Evaluate Screen endpoint, a thin wrapper over
:mod:`app.services.evaluate_screen`.

One route, and no stream: reading a screen is a single request that answers when
it is done, unlike a spin analysis, which is a sequence a page follows. There is
also no status endpoint, because there is no run to have a status -- each request
is its own reading.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger
from app.schemas.evaluate_screen import EvaluateScreenRequest, EvaluateScreenResult
from app.schemas.response import ApiResponse
from app.services import evaluate_screen as evaluate_screen_service

logger = get_logger("evaluate_screen.api")

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

FAILURES: ResponseSpec = {
    400: {"description": "Not a bare filename, or no such classifier architecture"},
    404: {"description": "No screenshot of that name, or the grid is not configured"},
    409: {"description": "No model has been trained to name the tiles with"},
    500: {"description": "The game config is unreadable"},
    502: {"description": "OBS could not produce a frame to read"},
}


@router.post(
    "/analyze",
    response_model=ApiResponse[EvaluateScreenResult],
    summary="Read the game's current screen",
    responses=FAILURES,
)
async def analyze(
    request: EvaluateScreenRequest | None = None,
) -> ApiResponse[EvaluateScreenResult]:
    """Capture the screen (or read a screenshot by name), name every symbol on the
    grid with the trained CNN, OCR the figure on each scatter, and read the cash
    meter with PaddleOCR.

    **A 200 does not mean everything read.** The grid and the meter are
    independent readings of one picture, so either can fail with the other still
    returned -- check ``data.errors``, and ``data.reels`` / ``data.meter`` for
    what is actually there. Only being unable to get a frame at all is a failure
    of the request itself.
    """
    result = await evaluate_screen_service.evaluate(request)
    parts = [
        "no grid reading" if result.reels is None else result.reels.summary,
        "no meter reading"
        if result.meter is None
        else f"meter {result.meter.mode.value}",
    ]
    return ApiResponse[EvaluateScreenResult].ok(
        data=result,
        message=(
            f"Read the screen of {result.label} from {result.source.file_name}: "
            + ", ".join(parts)
            + (f" ({len(result.errors)} error(s))" if result.errors else "")
        ),
    )
