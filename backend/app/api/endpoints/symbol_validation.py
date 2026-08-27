"""Symbol validation endpoints, thin wrappers over
:mod:`app.services.symbol_validation`. One picture in, every reference symbol
under a folder scored against it, in the order the folder was read."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.response import ApiResponse
from app.schemas.symbol_validation import (
    SymbolValidationRequest,
    SymbolValidationResult,
)
from app.services import symbol_validation as symbol_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

NOT_FOUND: ResponseSpec = {
    404: {"description": "The picture, or the folder of symbols, is not there"}
}
BAD_PICTURE: ResponseSpec = {
    502: {"description": "The candidate could not be read as an image"}
}
BAD_REQUEST: ResponseSpec = {
    400: {"description": "A path is empty, or the folder holds too many pictures"}
}


@router.post(
    "/compare",
    response_model=ApiResponse[SymbolValidationResult],
    summary="Score one picture against a folder of symbols",
    responses={**NOT_FOUND, **BAD_PICTURE, **BAD_REQUEST},
)
async def compare(
    payload: SymbolValidationRequest,
) -> ApiResponse[SymbolValidationResult]:
    """Compare one candidate picture -- a tile of a reel split, say -- against
    every picture under ``source_dir`` (recursively; omit it for
    ``SYMBOL_VALIDATION_SOURCE_DIR``).

    Each source is trimmed of its padding and resized to the candidate's own
    resolution before it is scored, so the comparison is artwork against artwork
    rather than artwork against a canvas of black. The candidate is never
    resized. Scores are cosine similarity on the same measure the payline check
    uses, so they are not zero-based: unrelated pictures already score well
    above 0, and the useful reading is the gap between the best folder and the
    runner-up (``stats.margin``), not the absolute number.

    ``comparisons`` and ``groups`` come back in **source order** -- the order
    the folder was read -- so a symbol's animation frames stay in sequence and a
    chart drawn over the list has a meaningful x-axis. Score order is on each
    comparison's ``rank``; the winner is ``stats.best_source``.
    """
    result = await symbol_service.compare(payload)
    return ApiResponse[SymbolValidationResult].ok(
        data=result,
        message=(
            f"Compared {result.candidate.name} against "
            f"{result.stats.sources} symbols: {result.summary}"
        ),
    )
