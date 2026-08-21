"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services import event_capture as event_capture_service
from app.services import game_input as game_input_service
from app.services import ideck as ideck_service
from app.services import meter as meter_service
from app.services import obs as obs_service
from app.services import ocr as ocr_service


@pytest.fixture
def app():
    """A fresh application instance per test."""
    return create_app()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    """An async HTTP client wired to the app, exercising the full middleware stack."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def _clean_obs_state() -> Iterator[None]:
    """Drop any faked OBS session, and the lock bound to this test's loop."""
    obs_service.reset()
    yield
    obs_service.reset()


@pytest.fixture(autouse=True)
def _clean_ideck_state() -> Iterator[None]:
    """Drop the cached panel layout, aliases, and this test's lock."""
    ideck_service.reset()
    yield
    ideck_service.reset()


@pytest.fixture(autouse=True)
def _clean_game_input_state() -> Iterator[None]:
    """Drop the cached game config, and this test's lock."""
    game_input_service.reset()
    yield
    game_input_service.reset()


@pytest.fixture(autouse=True)
def _clean_ocr_state() -> Iterator[None]:
    """Drop the cached OCR engine, so one test's fake is not another's answer."""
    ocr_service.reset()
    yield
    ocr_service.reset()


@pytest.fixture(autouse=True)
def _clean_meter_state() -> Iterator[None]:
    """Drop the fitted meter row bands, so one skin's band is not another's."""
    meter_service.reset()
    yield
    meter_service.reset()


@pytest.fixture(autouse=True)
async def _clean_event_capture_state() -> AsyncIterator[None]:
    """Cancel any watcher a test left running, and drop this test's lock.

    Async, unlike its OBS and i-deck siblings, because cancelling the watcher
    task means awaiting it -- and a task cancelled but never awaited is the
    pending-task warning that ``filterwarnings = error`` turns into a failure.
    """
    await event_capture_service.reset()
    yield
    await event_capture_service.reset()
