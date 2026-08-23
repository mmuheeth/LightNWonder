"""Reel grid endpoints, thin wrappers over :mod:`app.services.grid`. Splits
the active game's ``roi.reels`` region by its ``reel_bounds`` block; the
request only names which screenshot to use, never a rectangle."""

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
    """Shape of the active game's reel grid and the frame a split would use.
    A game with no ``roi.reels``/``reel_bounds`` comes back with ``error`` set
    on a 200, not a 404 -- not every game has reels."""
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
    """Crop the reels out of one screenshot (newest if ``file_name`` omitted)
    and divide them into tiles, written under
    ``obs-captured-files/grid/<frame stem>/``. ``inset`` overrides the
    config's border trim for this one split."""
    result = await grid_service.split(payload)
    return ApiResponse[GridSplitResult].ok(
        data=result,
        message=(
            f"Split roi.{result.region} of {result.source.file_name} into "
            f"{result.rows}x{result.columns} tiles in {result.output_dir}"
        ),
    )
