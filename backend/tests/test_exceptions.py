"""Every error path must produce the standard envelope."""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.exceptions.base import ConflictError, ForbiddenError
from tests.asserts import assert_failure


async def test_unknown_route_is_wrapped(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert_failure(response.json(), code="NOT_FOUND")


async def test_method_not_allowed_is_wrapped(client: AsyncClient) -> None:
    response = await client.post("/health")

    assert response.status_code == 405
    assert_failure(response.json(), code="METHOD_NOT_ALLOWED")


async def test_validation_error_lists_field_paths(client: AsyncClient) -> None:
    response = await client.post(
        "/api/items", json={"description": "no name given", "quantity": -5}
    )

    assert response.status_code == 422
    error = assert_failure(response.json(), code="VALIDATION_ERROR")
    fields = {detail["field"] for detail in error["details"]}
    assert fields == {"body.name", "body.quantity"}
    assert all(detail["message"] for detail in error["details"])
    assert all(detail["type"] for detail in error["details"])


async def test_path_param_validation_error(client: AsyncClient) -> None:
    response = await client.get("/api/items/0")  # ge=1

    assert response.status_code == 422
    error = assert_failure(response.json(), code="VALIDATION_ERROR")
    assert error["details"][0]["field"] == "path.item_id"


async def test_app_exception_uses_its_own_status_and_code(
    client: AsyncClient,
) -> None:
    await client.post("/api/items", json={"name": "Lamp"})
    response = await client.post("/api/items", json={"name": "Lamp"})

    assert response.status_code == 409
    error = assert_failure(response.json(), code="CONFLICT")
    assert error["details"] == []
    assert "already exists" in response.json()["message"]


async def test_unhandled_exception_becomes_a_wrapped_500(app: FastAPI) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("unexpected failure")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    error = assert_failure(response.json(), code="INTERNAL_SERVER_ERROR")
    # DEBUG is on in tests, so the cause is echoed for local debugging.
    assert "RuntimeError: unexpected failure" in error["details"][0]["message"]


async def test_custom_error_code_and_details_survive(app: FastAPI) -> None:
    @app.get("/forbidden")
    async def forbidden() -> None:
        raise ForbiddenError(
            "Editing is restricted to owners", error_code="NOT_ITEM_OWNER"
        )

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/forbidden")

    assert response.status_code == 403
    assert_failure(response.json(), code="NOT_ITEM_OWNER")
    assert response.json()["message"] == "Editing is restricted to owners"


async def test_app_exception_headers_are_forwarded(app: FastAPI) -> None:
    @app.get("/retry")
    async def retry() -> None:
        raise ConflictError("Try again", headers={"Retry-After": "30"})

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/retry")

    assert response.status_code == 409
    assert response.headers["Retry-After"] == "30"


async def test_errors_still_carry_the_request_id_header(client: AsyncClient) -> None:
    response = await client.get(
        "/does-not-exist", headers={"X-Request-ID": "trace-me-123"}
    )

    assert response.headers["X-Request-ID"] == "trace-me-123"
    assert response.json()["meta"]["request_id"] == "trace-me-123"
