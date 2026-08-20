"""ROI extraction endpoints.

Thin wrappers over :mod:`app.services.roi`. Which regions exist comes from the
active game's config and never from the request; what a request may say is which
one to cut out, and off which frame.

Two failure shapes recur:

- **404 ``ROI_REGION_NOT_FOUND`` / ``ROI_FRAME_NOT_FOUND``** -- the active game
  declares no region by that name, or there is no such screenshot to crop. Both
  are a wrong name, fixed by picking a different one rather than by retrying.
  "No screenshot at all yet" is the second of these, and its message says where
  they are written.
- **502 ``ROI_EXTRACT_FAILED``** -- the frame is on disk and is not a readable
  image, which usually means it was caught mid-write. Worth retrying, or worth
  taking another screenshot.

A region that is declared with unusable numbers is neither: it is a **500
``GAME_CONFIG_INVALID``**, because the config is wrong rather than the request.
"""

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
    """Every region the active game declares, and the frame they would be cut from.

    This is what fills the dropdown. ``latest_frame`` is the screenshot an
    extraction with no ``file_name`` will use, so the panel can name it before
    anyone presses Extract -- and is null when none has been taken yet.

    A region declared with unusable numbers is listed with its ``error`` set
    rather than omitted, so a typo in the config is visible where someone is
    already looking.
    """
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
    """Cut one region out of one screenshot and return the crop.

    With no ``file_name`` the newest screenshot is used, which is what makes
    "take a shot, then check the meter" two clicks. The crop comes back as a
    data URI beside the pixel box the fractions resolved to on that frame --
    the box is the thing to read when a crop looks off by a few pixels.
    """
    result = await roi_service.extract(payload)
    return ApiResponse[RoiExtractResult].ok(
        data=result,
        message=(
            f"Extracted roi.{result.region} from {result.source.file_name} "
            f"({result.width}x{result.height})"
        ),
    )
