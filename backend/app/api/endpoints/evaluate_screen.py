"""Evaluate Screen endpoint, a thin wrapper over :mod:`app.services.evaluate_screen`.
One route, no stream and no status endpoint -- reading a screen is a single
request that answers when done, and each request is its own reading with nothing
to track."""

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
    400: {"description": "Not a bare filename"},
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
    """Capture the screen (or read a named screenshot), name grid symbols with
    the trained CNN, OCR each scatter, and read the cash meter with PaddleOCR.
    **A 200 does not mean everything read** -- grid and meter are independent
    readings, so check ``data.errors`` and ``data.reels``/``data.meter``; only
    failing to get a frame at all fails the request itself."""
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
