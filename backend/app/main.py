"""Application factory.

``create_app`` builds a fully configured :class:`~fastapi.FastAPI` instance.
Keeping construction in a function (rather than at import time) lets tests build
isolated apps with overridden settings.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from app.api.health import HEALTH_PATHS
from app.api.health import router as health_router
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.exceptions import register_exception_handlers
from app.exceptions.base import AppException
from app.middleware import RequestContextMiddleware
from app.schemas.response import ApiResponse
from app.schemas.system import ServiceInfo
from app.services import analyze_spin as analyze_spin_service
from app.services import database as database_service
from app.services import event_capture as event_capture_service
from app.services import image_classifier as image_classifier_service
from app.services import obs as obs_service

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect the database and (optionally) OBS on startup; tear both down on shutdown."""
    settings: Settings = app.state.settings
    logger.info(
        "Starting %s v%s (env=%s, debug=%s)",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.ENVIRONMENT,
        settings.DEBUG,
    )
    app.state.db = await database_service.connect(settings)

    # OBS is optional, so a missing one must never stop the app booting. It is
    # also deliberately not a health probe: a closed screen recorder should not
    # make /health/ready report the whole service unavailable.
    if settings.OBS_AUTO_CONNECT:
        try:
            await obs_service.connect()
        except AppException as exc:
            logger.warning("OBS auto-connect failed: %s", exc.message)

    try:
        yield
    finally:
        # Before OBS goes: a run still going needs its manifest sealed, and
        # sealing it takes one last screenshot-free read, not a live socket.
        await event_capture_service.abort()
        # This one needs the socket, not just the absence of it: a spin
        # interrupted mid-run would otherwise leave OBS still recording.
        await analyze_spin_service.abort()
        # A training run holds no external device, so it only needs to stop
        # being awaited -- but it does need awaiting, or the pending task
        # outlives the loop.
        await image_classifier_service.abort()
        await database_service.disconnect(app.state.db)
        app.state.db = None
        await obs_service.disconnect()
        logger.info("Shutdown complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.APP_NAME,
        description=settings.APP_DESCRIPTION,
        version=settings.APP_VERSION,
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
        openapi_url=settings.openapi_url,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Middleware runs outermost-last-added-first, so request context is added last
    # to see every request first, including CORS preflights.
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[settings.REQUEST_ID_HEADER, "X-Process-Time"],
    )
    app.add_middleware(
        RequestContextMiddleware,
        header_name=settings.REQUEST_ID_HEADER,
        quiet_paths=HEALTH_PATHS,
    )

    register_exception_handlers(app)

    # Health lives at the root, deliberately outside the API prefix.
    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.API_PREFIX)

    @app.get("/", response_model=ApiResponse[ServiceInfo], tags=["meta"])
    async def root() -> ApiResponse[ServiceInfo]:
        """Identify the service and point at its docs."""
        return ApiResponse[ServiceInfo].ok(
            data=ServiceInfo(
                service=settings.APP_NAME,
                version=settings.APP_VERSION,
                environment=settings.ENVIRONMENT,
                docs_url=settings.docs_url,
                api_prefix=settings.API_PREFIX,
            ),
            message="Service is running",
        )

    return app


app = create_app()
