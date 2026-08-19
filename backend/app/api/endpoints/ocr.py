"""OCR endpoints.

Thin wrappers over :mod:`app.services.ocr`. Which regions exist comes from the
active game's config and never from the request; what a request may say is which
of them to read, off which frame, and -- for tuning -- which engine options to
change for that one read.

Three failure shapes recur:

- **409 ``OCR_ENGINE_UNAVAILABLE``** -- no Tesseract install was found, or OCR is
  switched off. Fixed by installing the engine or setting ``OCR_TESSERACT_CMD``,
  not by retrying. ``GET /api/ocr/status`` says which, and where it looked.
- **404 ``OCR_REGION_NOT_FOUND``** -- the active game declares no region by that
  name. A region that exists but could not be read is *not* this: it comes back
  200 with ``error`` set on its reading.
- **502 ``OBS_*`` / ``OCR_READ_FAILED``** -- a live read has to get a frame from
  OBS first, so OBS's failures surface here too.
"""

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
    """Report whether text can be read, and with what.

    Always 200: a machine with no Tesseract is a state to report, not a failed
    request -- the same treatment OBS gets. Check ``data.state`` rather than the
    status code, and read ``data.detail`` when it is not ``ready``.
    """
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
    """Every region the active game declares, with the options it will be read with.

    The options shown are the effective ones -- environment defaults with the
    game config's per-region overrides applied -- so "why is this meter read at
    psm 11" is answerable without opening the config file.
    """
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
    """Read the named regions, or all of them, off one frame.

    With no ``run_id`` the frame is taken from OBS now. With one, the named
    screenshot from that capture run is read instead, which is what makes a
    reading reproducible while an option is being tuned. ``include_crop`` returns
    what the engine actually saw, which is the thing to look at when a reading is
    wrong.

    One unreadable region does not fail the request: its reading carries the
    reason and the others are still returned.
    """
    result = await ocr_service.read(payload)
    read_count = sum(1 for reading in result.readings if reading.error is None)
    return ApiResponse[OcrReadResult].ok(
        data=result,
        message=f"Read {read_count} of {len(result.readings)} regions",
    )
