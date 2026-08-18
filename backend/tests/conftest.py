"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services import item as item_service


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
