"""Aggregates every endpoint module into one router.

Mounted by the app factory under ``settings.API_PREFIX``. Register new endpoint
modules here.

Health is *not* included: it mounts at the application root instead, so probes
never depend on the API prefix.
"""

from fastapi import APIRouter

from app.api.endpoints import event_capture, game_input, games, ideck, obs, ocr

api_router = APIRouter()

api_router.include_router(games.router, prefix="/games", tags=["games"])
api_router.include_router(obs.router, prefix="/obs", tags=["obs"])
api_router.include_router(ideck.router, prefix="/ideck", tags=["ideck"])
api_router.include_router(
    event_capture.router, prefix="/event-capture", tags=["event-capture"]
)
api_router.include_router(game_input.router, prefix="/game-input", tags=["game-input"])
api_router.include_router(ocr.router, prefix="/ocr", tags=["ocr"])
