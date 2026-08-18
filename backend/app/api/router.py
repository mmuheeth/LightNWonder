"""Aggregates every endpoint module into one router.

Mounted by the app factory under ``settings.API_PREFIX``. Register new endpoint
modules here.

Health is *not* included: it mounts at the application root instead, so probes
never depend on the API prefix.
"""

from fastapi import APIRouter

from app.api.endpoints import games, ideck, items, obs

api_router = APIRouter()

# --- Example resource; delete once you have real endpoints. ---------------
api_router.include_router(items.router, prefix="/items", tags=["items"])

api_router.include_router(games.router, prefix="/games", tags=["games"])
api_router.include_router(obs.router, prefix="/obs", tags=["obs"])
api_router.include_router(ideck.router, prefix="/ideck", tags=["ideck"])
