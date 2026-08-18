"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services import ideck as ideck_service
from app.services import item as item_service
from app.services import obs as obs_service


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
def _clean_item_store() -> Iterator[None]:
    """Keep the in-memory example store from leaking between tests."""
    item_service.reset()
    yield
    item_service.reset()


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
