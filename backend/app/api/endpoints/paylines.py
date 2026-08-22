"""Payline check endpoints.

Thin wrappers over :mod:`app.services.paylines`. What gets checked is a split the
reel grid already wrote against one of the bet configurations the active game
declares; the request never names a pattern, only which split, which set, and how
strict to be.

The failure shapes:

- **404 ``PAYLINES_NOT_CONFIGURED``** -- the active game declares no ``paylines``
  block, or no set by the requested name. Fixed by picking a different game or
  adding the block, never by retrying. ``GET /api/paylines/layout`` reports the
  first of those as ``error`` on a 200 instead, because choosing a game that has
  no paylines is a state the panel renders.
- **404 ``GRID_NOT_CONFIGURED``** -- the game declares no ``reel_bounds``, so
  there is no grid to place the coordinates on. The grid feature's own error,
  because it is the grid's own block.
- **404 ``PAYLINE_SOURCE_NOT_FOUND``** -- nothing has been split yet, or the
  named split is not there or is missing tiles. Fixed from the Reel grid panel.
- **409 ``PAYLINE_SOURCE_STALE``** -- the split on disk is a different shape from
  the grid the game now declares, so its tiles are not what the coordinates
  describe. Fixed by splitting the frame again.
- **502 ``PAYLINE_CHECK_FAILED``** -- a line runs through a tile the split does
  not hold, or the annotated picture could not be written.

A ``paylines`` block declared with unusable numbers is none of these: it is a
**500 ``GAME_CONFIG_INVALID``**, because the config is wrong rather than the
request -- the same split the grid endpoints draw.
"""

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
    """The bet configurations the active game declares, and the split to check.

    This is what the panel renders before anything is checked: the sets to choose
    between, which one a check with no ``set`` would use, the similarity
    threshold it would apply, and the newest split -- null until one has been
    made.

    A game with no ``paylines``, a game with no ``reel_bounds``, and a checkout
    that has split nothing all come back with ``error`` set and a 200. None of
    the three is a request that went wrong.
    """
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
    """Evaluate every line of one set against the tiles of one split.

    With no ``split`` the newest one is used, which is what makes "split it,
    check it" two clicks. Each line is read from the left one adjacent pair at a
    time and stops at the first pair whose tiles are not the same symbol, so
    ``pays`` is the length of the leading run -- 0 when the first two reels
    differ, otherwise 2 or more.

    Every adjacent pair's similarity comes back, including the pairs after a run
    broke, because the numbers are what make the verdict checkable. So does the
    annotated reels picture, with each paying line drawn in the colour reported
    beside it, written to ``<split>/paylines/<set>.png``.

    ``threshold`` is the number worth tuning -- ``stats.matched_min`` and
    ``stats.rejected_max`` are the two numbers a working cut sits between, and
    the scores cluster much higher than a naive reading of "0.7 means similar"
    expects.
    """
    result = await paylines_service.check(payload)
    return ApiResponse[PaylineCheckResult].ok(
        data=result,
        message=(
            f"{result.summary} on the {result.set}-line set of "
            f"{result.source.split} at threshold {result.threshold}"
        ),
    )
