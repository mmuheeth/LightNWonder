"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services import analyze_spin as analyze_spin_service
from app.services import cyclic_messages as cyclic_messages_service
from app.services import event_capture as event_capture_service
from app.services import game_input as game_input_service
from app.services import ideck as ideck_service
from app.services import image_classifier as image_classifier_service
from app.services import meter as meter_service
from app.services import obs as obs_service
from app.services import ocr as ocr_service
from app.services import paytable as paytable_service


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
    """Drop the cached panel layout and this test's lock."""
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
def _clean_paytable_state() -> Iterator[None]:
    """Drop parsed maths files, so one test's math.xml is not another's answer."""
    paytable_service.reset()
    yield
    paytable_service.reset()


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


@pytest.fixture(autouse=True)
async def _clean_cyclic_messages_state() -> AsyncIterator[None]:
    """Cancel any cyclic message watcher a test left running, and drop its lock.
    Async for the same reason as its event-capture sibling above."""
    await cyclic_messages_service.reset()
    yield
    await cyclic_messages_service.reset()


@pytest.fixture(autouse=True)
async def _clean_analyze_spin_state() -> AsyncIterator[None]:
    """Cancel any spin a test left running, drop its subscribers and its lock.

    Async for the same reason as its event-capture sibling: ending the run means
    awaiting its cancellation, and a task cancelled but never awaited is the
    pending-task warning that ``filterwarnings = error`` turns into a failure.
    """
    await analyze_spin_service.reset()
    yield
    await analyze_spin_service.reset()


@pytest.fixture(autouse=True)
async def _clean_image_classifier_state() -> AsyncIterator[None]:
    """Stop any training run, drop the cached model and the dataset summary.

    Async like its event-capture and analyze-spin siblings: a run owns a task, and
    a task cancelled but never awaited is the pending-task warning that
    ``filterwarnings = error`` turns into a failure. The cached checkpoint has to
    go too -- one test's stub model must not be another test's answer.
    """
    await image_classifier_service.abort()
    yield
    await image_classifier_service.abort()
