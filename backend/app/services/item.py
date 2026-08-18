"""In-memory store backing the example ``items`` resource.

Stands in for a repository/database layer so the endpoint module can show the
response and exception wrappers end to end. Swap for real persistence, or
delete along with the rest of the example.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from threading import Lock

from app.exceptions.base import ConflictError, NotFoundError
from app.schemas.item import ItemCreate, ItemOut, ItemUpdate

_items: dict[int, ItemOut] = {}
_ids = count(1)
_lock = Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def list_items(*, offset: int, limit: int) -> tuple[list[ItemOut], int]:
    """Return one page of items plus the total count."""
    with _lock:
        ordered = sorted(_items.values(), key=lambda item: item.id)
    return ordered[offset : offset + limit], len(ordered)


def get_item(item_id: int) -> ItemOut:
    """Fetch one item.

    Raises:
        NotFoundError: if no item has that id.
    """
    with _lock:
        item = _items.get(item_id)
    if item is None:
        raise NotFoundError(f"Item {item_id} was not found")
    return item


def create_item(payload: ItemCreate) -> ItemOut:
    """Create an item.

    Raises:
        ConflictError: if an item with the same name already exists.
    """
    with _lock:
        if any(item.name == payload.name for item in _items.values()):
            raise ConflictError(f"An item named {payload.name!r} already exists")
        timestamp = _now()
        item = ItemOut(
            id=next(_ids),
            created_at=timestamp,
            updated_at=timestamp,
            **payload.model_dump(),
        )
        _items[item.id] = item
    return item


def update_item(item_id: int, payload: ItemUpdate) -> ItemOut:
    """Apply a partial update.

    Raises:
        NotFoundError: if no item has that id.
        ConflictError: if the new name collides with another item.
    """
    changes = payload.model_dump(exclude_unset=True)
    with _lock:
        existing = _items.get(item_id)
        if existing is None:
            raise NotFoundError(f"Item {item_id} was not found")
        new_name = changes.get("name")
        if new_name is not None and any(
            other.name == new_name and other.id != item_id for other in _items.values()
        ):
            raise ConflictError(f"An item named {new_name!r} already exists")
        updated = existing.model_copy(update={**changes, "updated_at": _now()})
        _items[item_id] = updated
    return updated


def delete_item(item_id: int) -> None:
    """Delete an item.

    Raises:
        NotFoundError: if no item has that id.
    """
    with _lock:
        if _items.pop(item_id, None) is None:
            raise NotFoundError(f"Item {item_id} was not found")


def reset() -> None:
    """Clear the store. Used by tests."""
    global _ids
    with _lock:
        _items.clear()
        _ids = count(1)
