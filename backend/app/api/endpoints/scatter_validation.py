"""Scatter Value Validation endpoint, a thin wrapper over
:mod:`app.services.scatter_validation`.

One route, like Evaluate Screen: reading a screen and judging its scatters is a
single request that answers when it is done, with no run to have a status.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger
from app.schemas.response import ApiResponse
from app.schemas.scatter_validation import (
    ScatterValidationRequest,
    ScatterValidationResult,
)
from app.services import scatter_validation as scatter_validation_service

logger = get_logger("scatter_validation.api")

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
    response_model=ApiResponse[ScatterValidationResult],
    summary="Read the game's current screen and validate every landed scatter's figure",
    responses=FAILURES,
)
async def analyze(
    request: ScatterValidationRequest | None = None,
) -> ApiResponse[ScatterValidationResult]:
    """Capture the screen (or read a screenshot by name), name every symbol on the
    grid with the trained CNN, OCR the figure on each scatter, then check that
    figure against the value range the loaded maths declares for it at the bet
    the game's log last reported.

    **A 200 does not mean everything read or matched.** Check ``data.errors``
    for anything that could not be read, and each entry in ``data.checks`` for
    whether it matched.
    """
    result = await scatter_validation_service.validate(request)
    not_matched = sum(1 for c in result.checks if c.status == "not_matched")
    return ApiResponse[ScatterValidationResult].ok(
        data=result,
        message=(
            f"Read the screen of {result.label} from {result.source.file_name}: "
            f"{len(result.checks)} scatter(s) checked, {not_matched} not matched"
            + (f" ({len(result.errors)} error(s))" if result.errors else "")
        ),
    )
