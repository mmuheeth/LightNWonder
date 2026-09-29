"""Scatter Value Validation endpoint, a thin wrapper over
:mod:`app.services.scatter_validation`. One route, like Evaluate Screen: a
single request that answers when done, with no run to have a status."""

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
    400: {"description": "Not a bare filename"},
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
    """Capture the screen (or read a named screenshot), name grid symbols with
    the trained CNN, OCR each scatter's figure, and check it against the maths'
    value range for the bet the game's log last reported. **A 200 doesn't mean
    everything matched** -- check ``data.errors`` and each ``data.checks`` entry."""
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
