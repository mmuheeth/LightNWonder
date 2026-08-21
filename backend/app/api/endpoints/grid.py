"""Reel grid endpoints.

Thin wrappers over :mod:`app.services.grid`. What gets split is the active
game's ``roi.reels`` region divided by its ``reel_bounds`` block; the request
never names a rectangle, only which screenshot to use.

The failure shapes:

- **404 ``GRID_NOT_CONFIGURED``** -- the active game declares no ``roi.reels``,
  or no ``reel_bounds`` to divide it by. Fixed by picking a different game or by
  adding the block, never by retrying. ``GET /api/grid/layout`` reports this as
  ``error`` on a 200 instead, because choosing a game that has no reels is a
  state the panel renders rather than a request that went wrong.
- **404 ``ROI_FRAME_NOT_FOUND``** and **502 ``ROI_EXTRACT_FAILED``** -- ROI's
  own, because the frame is ROI's own: the same directory, the same newest-first
  rule, the same message pointing at the OBS panel.
- **502 ``GRID_SPLIT_FAILED``** -- the tiles could not be written. The crop
  worked and the disk did not.

A region or a bounds block declared with unusable numbers is none of these: it is
a **500 ``GAME_CONFIG_INVALID``**, because the config is wrong rather than the
request. The same numbers sent as a request's own ``inset`` are a **400
``BAD_REQUEST``** instead -- only one of the two is something the caller can fix
by asking differently.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.grid import GridLayout, GridSplitRequest, GridSplitResult
from app.schemas.response import ApiResponse
from app.services import grid as grid_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

NOT_FOUND: ResponseSpec = {
    404: {"description": "No reel grid is configured, or no such frame"}
}
BAD_FRAME: ResponseSpec = {
    502: {"description": "The frame is unreadable, or the tiles could not be written"}
}
BAD_CONFIG: ResponseSpec = {500: {"description": "The game config is unreadable"}}


@router.get(
    "/layout",
    response_model=ApiResponse[GridLayout],
    summary="Reel grid of the active game",
    responses={**BAD_FRAME, **BAD_CONFIG},
)
async def get_layout() -> ApiResponse[GridLayout]:
    """The shape of the active game's reel grid, and the frame it would come from.

    This is what the panel renders before anything is split: how many reels and
    how many symbol rows, the tile names laid out as a matrix, and the screenshot
    a split with no ``file_name`` would use -- null until one has been taken.

    A game that declares no ``roi.reels`` or no ``reel_bounds`` comes back with
    ``error`` set and a 200, not a 404. Only half the shipped games have reels,
    and selecting one of the others is not a failure to recover from.

    ``inset`` is the border trim every tile will be shrunk by, so the panel can
    show what a split is about to do before it does it.
    """
    layout = await grid_service.layout()
    message = (
        f"{layout.rows}x{layout.columns} reel grid in {layout.game}"
        if layout.error is None
        else f"No reel grid in {layout.game}"
    )
    return ApiResponse[GridLayout].ok(data=layout, message=message)


@router.post(
    "/split",
    response_model=ApiResponse[GridSplitResult],
    summary="Split the reels of a frame into tiles",
    responses={
        **NOT_FOUND,
        **BAD_FRAME,
        **BAD_CONFIG,
        400: {"description": "file_name is not a bare filename"},
    },
)
async def split(payload: GridSplitRequest) -> ApiResponse[GridSplitResult]:
    """Crop the reels out of one screenshot and divide them into tiles.

    With no ``file_name`` the newest screenshot is used, which is what makes
    "take a shot, then split it" two clicks. The crop and every tile are written
    under ``obs-captured-files/grid/<frame stem>/`` -- one directory per source
    frame, so splitting the same shot twice replaces its own record rather than
    adding another beside it -- and come back as data URIs unless
    ``include_images`` is false.

    Each tile carries its 1-indexed ``row`` and ``column``, so the matrix
    position is on the tile rather than implied by its place in the list.

    ``inset`` overrides the config's border trim for this one split, which is how
    the right number gets found before it is written into the game config. Every
    tile's ``roi`` and ``box`` already have it applied, and the effective trim is
    reported back so a tile's size never needs explaining from elsewhere.
    """
    result = await grid_service.split(payload)
    return ApiResponse[GridSplitResult].ok(
        data=result,
        message=(
            f"Split roi.{result.region} of {result.source.file_name} into "
            f"{result.rows}x{result.columns} tiles in {result.output_dir}"
        ),
    )
