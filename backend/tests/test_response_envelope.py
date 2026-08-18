"""Envelope and request-context invariants."""

from __future__ import annotations

from httpx import AsyncClient

from app.core.context import get_request_id
from app.schemas.response import ApiResponse, PaginatedResponse
from tests.asserts import assert_success


async def test_root_endpoint_is_wrapped(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["api_prefix"] == "/api"


async def test_request_id_is_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/")

    generated = response.headers["X-Request-ID"]
    assert generated
    assert response.json()["meta"]["request_id"] == generated


async def test_inbound_request_id_is_reused(client: AsyncClient) -> None:
    response = await client.get("/", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"
    assert response.json()["meta"]["request_id"] == "abc-123"


async def test_request_ids_differ_between_requests(client: AsyncClient) -> None:
    first = await client.get("/")
    second = await client.get("/")

    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


async def test_process_time_header_is_present(client: AsyncClient) -> None:
    response = await client.get("/")

    assert float(response.headers["X-Process-Time"]) >= 0


async def test_context_is_cleared_after_the_request(client: AsyncClient) -> None:
    await client.get("/")

    assert get_request_id() is None


def test_ok_builds_a_success_envelope() -> None:
    envelope = ApiResponse[int].ok(data=7, message="Seven")

    assert envelope.success is True
    assert envelope.data == 7
    assert envelope.error is None
    assert envelope.message == "Seven"


def test_fail_builds_a_failure_envelope() -> None:
    envelope = ApiResponse[int].fail("Nope", code="TEAPOT")

    assert envelope.success is False
    assert envelope.data is None
    assert envelope.error is not None
    assert envelope.error.code == "TEAPOT"


def test_pagination_meta_is_derived_correctly() -> None:
    envelope = PaginatedResponse[int].paginate(
        [1, 2], page=2, page_size=2, total_items=5
    )

    assert envelope.meta.total_pages == 3
    assert envelope.meta.has_next is True
    assert envelope.meta.has_previous is True


def test_pagination_meta_on_an_empty_result() -> None:
    envelope = PaginatedResponse[int].paginate([], page=1, page_size=20, total_items=0)

    assert envelope.meta.total_pages == 0
    assert envelope.meta.has_next is False
    assert envelope.meta.has_previous is False
