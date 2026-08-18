"""Example resource -- doubles as a test of the paginated envelope."""

from __future__ import annotations

from httpx import AsyncClient

from tests.asserts import assert_failure, assert_success


async def test_create_then_read(client: AsyncClient) -> None:
    created = await client.post(
        "/api/items",
        json={"name": "Table lamp", "description": "Brass", "quantity": 12},
    )

    assert created.status_code == 201
    item = assert_success(created.json())
    assert item["id"] == 1
    assert item["name"] == "Table lamp"
    assert item["created_at"] and item["updated_at"]

    fetched = await client.get(f"/api/items/{item['id']}")
    assert fetched.status_code == 200
    assert assert_success(fetched.json()) == item


async def test_get_missing_item(client: AsyncClient) -> None:
    response = await client.get("/api/items/999")

    assert response.status_code == 404
    assert_failure(response.json(), code="NOT_FOUND")
    assert response.json()["message"] == "Item 999 was not found"


async def test_list_is_paginated(client: AsyncClient) -> None:
    for index in range(5):
        await client.post("/api/items", json={"name": f"Item {index}"})

    response = await client.get("/api/items", params={"page": 2, "page_size": 2})

    assert response.status_code == 200
    payload = response.json()
    items = assert_success(payload)
    assert [item["name"] for item in items] == ["Item 2", "Item 3"]

    meta = payload["meta"]
    assert meta["page"] == 2
    assert meta["page_size"] == 2
    assert meta["total_items"] == 5
    assert meta["total_pages"] == 3
    assert meta["has_next"] is True
    assert meta["has_previous"] is True
    # The pagination block must not lose the base envelope metadata.
    assert meta["request_id"] and meta["timestamp"]


async def test_page_size_is_capped(client: AsyncClient) -> None:
    response = await client.get("/api/items", params={"page_size": 1000})

    assert response.status_code == 422
    error = assert_failure(response.json(), code="VALIDATION_ERROR")
    assert error["details"][0]["field"] == "query.page_size"


async def test_partial_update_leaves_other_fields_alone(client: AsyncClient) -> None:
    created = await client.post(
        "/api/items", json={"name": "Sconce", "description": "Wall", "quantity": 3}
    )
    item_id = created.json()["data"]["id"]

    updated = await client.patch(f"/api/items/{item_id}", json={"quantity": 9})

    assert updated.status_code == 200
    item = assert_success(updated.json())
    assert item["quantity"] == 9
    assert item["name"] == "Sconce"
    assert item["description"] == "Wall"


async def test_duplicate_name_conflicts(client: AsyncClient) -> None:
    await client.post("/api/items", json={"name": "Pendant"})
    response = await client.post("/api/items", json={"name": "Pendant"})

    assert response.status_code == 409
    assert_failure(response.json(), code="CONFLICT")


async def test_delete_then_missing(client: AsyncClient) -> None:
    created = await client.post("/api/items", json={"name": "Chandelier"})
    item_id = created.json()["data"]["id"]

    deleted = await client.delete(f"/api/items/{item_id}")
    assert deleted.status_code == 200
    assert assert_success(deleted.json()) is None

    again = await client.delete(f"/api/items/{item_id}")
    assert again.status_code == 404
    assert_failure(again.json(), code="NOT_FOUND")
