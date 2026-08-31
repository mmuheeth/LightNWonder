"""Similarity check endpoint, a thin wrapper over :mod:`app.services.similarity`.
One source picture against every image in a folder; the request may name both
paths or lean on the configured defaults."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.response import ApiResponse
from app.schemas.similarity import SimilarityCompareRequest, SimilarityCompareResult
from app.services import similarity as similarity_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

NOT_FOUND: ResponseSpec = {
    404: {"description": "No source image, no candidates folder, or nothing in it"}
}
BAD_SOURCE: ResponseSpec = {
    502: {"description": "The source file is not a readable image"}
}


@router.post(
    "/compare",
    response_model=ApiResponse[SimilarityCompareResult],
    summary="Score one image against a folder of candidates",
    responses={**NOT_FOUND, **BAD_SOURCE},
)
async def compare(
    payload: SimilarityCompareRequest,
) -> ApiResponse[SimilarityCompareResult]:
    """Cosine-similarity score of the source image against every image under
    the folder (searched recursively), one row per candidate, best first. A
    candidate of a different pixel size is resized to the source's and flagged;
    one that cannot be read fails alone on its own row. Each row carries the
    picture as compared, as a data URI; ``include_images: false`` strips them."""
    result = await similarity_service.compare(payload)
    return ApiResponse[SimilarityCompareResult].ok(
        data=result,
        message=(
            f"Scored {result.stats.compared} of {result.stats.candidates} images "
            f"against {result.source.file_name}"
        ),
    )
