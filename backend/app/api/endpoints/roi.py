"""ROI extraction endpoints, thin wrappers over :mod:`app.services.roi`.
Regions come from the active game's config; a request only says which one to
cut out, and off which frame."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.response import ApiResponse
from app.schemas.roi import RoiCatalog, RoiExtractRequest, RoiExtractResult
from app.services import roi as roi_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

NOT_FOUND: ResponseSpec = {404: {"description": "No such region, or no such frame"}}
BAD_FRAME: ResponseSpec = {502: {"description": "The frame is not a readable image"}}
BAD_CONFIG: ResponseSpec = {500: {"description": "The game config is unreadable"}}


@router.get(
    "/regions",
    response_model=ApiResponse[RoiCatalog],
    summary="Extractable regions of the active game",
    responses={**NOT_FOUND, **BAD_FRAME, **BAD_CONFIG},
)
async def get_regions() -> ApiResponse[RoiCatalog]:
    """Every region the active game declares, and the frame they'd be cut
    from. A region with unusable numbers is listed with its ``error`` set
    rather than omitted."""
    catalog = await roi_service.catalog()
    return ApiResponse[RoiCatalog].ok(
        data=catalog,
        message=f"{len(catalog.regions)} extractable regions in {catalog.game}",
    )


@router.post(
    "/extract",
    response_model=ApiResponse[RoiExtractResult],
    summary="Extract a region from a frame",
    responses={
        **NOT_FOUND,
        **BAD_FRAME,
        **BAD_CONFIG,
        400: {"description": "file_name is not a bare filename"},
    },
)
async def extract(payload: RoiExtractRequest) -> ApiResponse[RoiExtractResult]:
    """Cut one region out of one screenshot (newest if ``file_name`` omitted)
    and return the crop as a data URI beside the pixel box it resolved to."""
    result = await roi_service.extract(payload)
    return ApiResponse[RoiExtractResult].ok(
        data=result,
        message=(
            f"Extracted roi.{result.region} from {result.source.file_name} "
            f"({result.width}x{result.height})"
        ),
    )
