"""OCR endpoints, thin wrappers over :mod:`app.services.ocr`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.ocr import (
    OcrReadRequest,
    OcrReadResult,
    OcrRegionCatalog,
    OcrStatus,
)
from app.schemas.response import ApiResponse
from app.services import ocr as ocr_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

NO_ENGINE: ResponseSpec = {409: {"description": "No usable Tesseract install"}}
NO_REGION: ResponseSpec = {404: {"description": "No such region, or no run/frame"}}
NO_FRAME: ResponseSpec = {
    502: {"description": "The frame to read could not be obtained"}
}


@router.get(
    "/status",
    response_model=ApiResponse[OcrStatus],
    summary="OCR engine status",
)
async def get_status() -> ApiResponse[OcrStatus]:
    """Report whether text can be read, and with what; always 200 -- check
    ``data.state``, and ``data.detail`` when it is not ``ready``."""
    state = await ocr_service.status()
    return ApiResponse[OcrStatus].ok(
        data=state, message=f"The OCR engine is {state.state.value}"
    )


@router.get(
    "/regions",
    response_model=ApiResponse[OcrRegionCatalog],
    summary="Readable regions of the active game",
    responses={500: {"description": "The game config is unreadable"}},
)
async def get_regions() -> ApiResponse[OcrRegionCatalog]:
    """Every region the active game declares, with its effective read options
    -- environment defaults plus the config's per-region overrides."""
    catalog = ocr_service.regions()
    return ApiResponse[OcrRegionCatalog].ok(
        data=catalog,
        message=f"{len(catalog.regions)} readable regions in {catalog.game}",
    )


@router.post(
    "/read",
    response_model=ApiResponse[OcrReadResult],
    summary="Read regions off a frame",
    responses={**NO_ENGINE, **NO_REGION, **NO_FRAME},
)
async def read(payload: OcrReadRequest) -> ApiResponse[OcrReadResult]:
    """Read the named regions, or all of them, off one frame -- from OBS now with no
    ``run_id``, or a capture run's screenshot with one."""
    result = await ocr_service.read(payload)
    read_count = sum(1 for reading in result.readings if reading.error is None)
    return ApiResponse[OcrReadResult].ok(
        data=result,
        message=f"Read {read_count} of {len(result.readings)} regions",
    )
