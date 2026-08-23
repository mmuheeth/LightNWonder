"""Payline check endpoints, thin wrappers over
:mod:`app.services.paylines`. Checks a split the reel grid already wrote
against one of the active game's bet configurations; the request only names
which split, which set, and how strict to be."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.paylines import (
    PaylineCheckRequest,
    PaylineCheckResult,
    PaylineLayout,
)
from app.schemas.response import ApiResponse
from app.services import paylines as paylines_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

NOT_FOUND: ResponseSpec = {
    404: {"description": "No paylines are configured, or nothing has been split"}
}
STALE: ResponseSpec = {
    409: {"description": "The split does not match the grid the game declares"}
}
FAILED: ResponseSpec = {
    502: {"description": "A tile is missing, or the overlay could not be written"}
}
BAD_CONFIG: ResponseSpec = {500: {"description": "The game config is unreadable"}}


@router.get(
    "/layout",
    response_model=ApiResponse[PaylineLayout],
    summary="Payline sets of the active game",
    responses={**BAD_CONFIG},
)
async def get_layout() -> ApiResponse[PaylineLayout]:
    """The active game's bet configurations and the split to check them
    against. A game with no ``paylines``/``reel_bounds``, or nothing split
    yet, comes back with ``error`` set on a 200."""
    layout = await paylines_service.layout()
    message = (
        f"{len(layout.sets)} payline sets in {layout.game}"
        if layout.error is None
        else f"No payline check available for {layout.game}"
    )
    return ApiResponse[PaylineLayout].ok(data=layout, message=message)


@router.post(
    "/check",
    response_model=ApiResponse[PaylineCheckResult],
    summary="Check one payline set against a split reel grid",
    responses={
        **NOT_FOUND,
        **STALE,
        **FAILED,
        **BAD_CONFIG,
        400: {"description": "split is not a bare directory name"},
    },
)
async def check(payload: PaylineCheckRequest) -> ApiResponse[PaylineCheckResult]:
    """Evaluate every line of one set against one split (newest if
    ``split`` omitted). ``pays`` is the length of the leading matching run;
    tune ``threshold`` against ``stats.matched_min``/``rejected_max``."""
    result = await paylines_service.check(payload)
    return ApiResponse[PaylineCheckResult].ok(
        data=result,
        message=(
            f"{result.summary} on the {result.set}-line set of "
            f"{result.source.split} at threshold {result.threshold}"
        ),
    )
