"""Paytable endpoints, thin wrappers over :mod:`app.services.paytable`. Reads
the maths the running game loaded: which paytable folder its log named, and the
symbols, reel strips, combos and payline geometry in that folder's XML."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.schemas.paytable import PaytableView
from app.schemas.response import ApiResponse
from app.services import paytable as paytable_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

UNAVAILABLE: ResponseSpec = {
    409: {
        "description": "The game is not installed, or declares no GameConfig directory"
    }
}
NOT_FOUND: ResponseSpec = {
    404: {"description": "No paytable folder matches the requested (or logged) id"}
}
BAD_MATH: ResponseSpec = {502: {"description": "The game's maths files are unreadable"}}
BAD_CONFIG: ResponseSpec = {500: {"description": "The game config is unreadable"}}


@router.get(
    "/",
    response_model=ApiResponse[PaytableView],
    summary="Maths of the paytable the active game has loaded",
    responses={**UNAVAILABLE, **NOT_FOUND, **BAD_MATH, **BAD_CONFIG},
)
async def get_paytable(
    paytable_id: str | None = Query(
        default=None,
        max_length=128,
        description=(
            "Inspect a specific paytable folder of the active game instead of "
            "the one its log last named. Must be one of the ids in 'available'."
        ),
    ),
) -> ApiResponse[PaytableView]:
    """Everything the loaded paytable declares: symbols and their roles, every
    reel strip, the line and scatter combos, and the payline set in play.

    Which folder is read comes from the game's own log by default, and the
    answer says so in ``source`` -- the join between a running game and a
    directory on disk is the part worth being able to check."""
    view = await paytable_service.view(paytable_id)
    lines = view.win_geometry.line_count
    return ApiResponse[PaytableView].ok(
        data=view,
        message=(
            f"Paytable {view.paytable_id} ({view.source.origin}): "
            f"{len(view.math.symbols)} symbols, {len(view.math.reel_strips)} reel "
            f"strips, {len(view.math.payline_combos)} line combos"
            + (f", {lines} paylines" if lines else "")
        ),
    )
