"""Aggregates every endpoint module into one router.

Mounted by the app factory under ``settings.API_PREFIX``. Register new endpoint
modules here.

Health is *not* included: it mounts at the application root instead, so probes
never depend on the API prefix.
"""

from fastapi import APIRouter

from app.api.endpoints import items

api_router = APIRouter()

# --- Example resource; delete once you have real endpoints. ---------------
api_router.include_router(items.router, prefix="/items", tags=["items"])
