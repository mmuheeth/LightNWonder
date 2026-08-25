"""Aggregates every endpoint module into one router, mounted by the app
factory under ``settings.API_PREFIX``. Health mounts separately at the root."""

from fastapi import APIRouter

from app.api.endpoints import (
    analyze_spin,
    event_capture,
    game_input,
    games,
    grid,
    ideck,
    obs,
    ocr,
    paylines,
    paytable,
    roi,
)

api_router = APIRouter()

api_router.include_router(games.router, prefix="/games", tags=["games"])
api_router.include_router(obs.router, prefix="/obs", tags=["obs"])
api_router.include_router(ideck.router, prefix="/ideck", tags=["ideck"])
api_router.include_router(
    event_capture.router, prefix="/event-capture", tags=["event-capture"]
)
api_router.include_router(game_input.router, prefix="/game-input", tags=["game-input"])
api_router.include_router(ocr.router, prefix="/ocr", tags=["ocr"])
api_router.include_router(roi.router, prefix="/roi", tags=["roi"])
api_router.include_router(grid.router, prefix="/grid", tags=["grid"])
api_router.include_router(paylines.router, prefix="/paylines", tags=["paylines"])
api_router.include_router(paytable.router, prefix="/paytable", tags=["paytable"])
api_router.include_router(
    analyze_spin.router, prefix="/analyze-spin", tags=["analyze-spin"]
)
