"""Example CRUD resource -- the reference for writing new endpoints.

Three conventions to copy:

1. Declare ``response_model=ApiResponse[X]`` (or ``PaginatedResponse[X]``) so
   the envelope is what OpenAPI advertises, and return ``ApiResponse.ok(...)``.
2. Never build an error response. Raise an
   :class:`~app.exceptions.base.AppException` subclass and let the registered
   handlers render it.
3. Keep the endpoint thin -- validation via schemas, work in the service layer.

Delete this module (and its service/schema counterparts) once real endpoints
exist, then drop the include in :mod:`app.api.router`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import PaginationDep
from app.schemas.item import ItemCreate, ItemOut, ItemUpdate
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services import item as item_service

router = APIRouter()

ItemId = Annotated[int, Path(ge=1, description="Item identifier.")]


@router.get(
    "",
    response_model=PaginatedResponse[ItemOut],
    summary="List items",
)
async def list_items(pagination: PaginationDep) -> PaginatedResponse[ItemOut]:
    """Return a page of items, newest ids last."""
    items, total = item_service.list_items(
        offset=pagination.offset, limit=pagination.limit
    )
    return PaginatedResponse[ItemOut].paginate(
        items,
        page=pagination.page,
        page_size=pagination.page_size,
        total_items=total,
        message=f"Retrieved {len(items)} of {total} item(s)",
    )


@router.get(
    "/{item_id}",
    response_model=ApiResponse[ItemOut],
    summary="Get an item",
    responses={404: {"description": "No item with that id"}},
)
async def get_item(item_id: ItemId) -> ApiResponse[ItemOut]:
    """Return a single item."""
    return ApiResponse[ItemOut].ok(
        data=item_service.get_item(item_id),
        message="Item retrieved successfully",
    )


@router.post(
    "",
    response_model=ApiResponse[ItemOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create an item",
    responses={409: {"description": "An item with that name already exists"}},
)
async def create_item(payload: ItemCreate) -> ApiResponse[ItemOut]:
    """Create an item and return it."""
    return ApiResponse[ItemOut].ok(
        data=item_service.create_item(payload),
        message="Item created successfully",
    )


@router.patch(
    "/{item_id}",
    response_model=ApiResponse[ItemOut],
    summary="Update an item",
    responses={
        404: {"description": "No item with that id"},
        409: {"description": "An item with that name already exists"},
    },
)
async def update_item(item_id: ItemId, payload: ItemUpdate) -> ApiResponse[ItemOut]:
    """Apply a partial update and return the result."""
    return ApiResponse[ItemOut].ok(
        data=item_service.update_item(item_id, payload),
        message="Item updated successfully",
    )


@router.delete(
    "/{item_id}",
    response_model=ApiResponse[None],
    summary="Delete an item",
    responses={404: {"description": "No item with that id"}},
)
async def delete_item(item_id: ItemId) -> ApiResponse[None]:
    """Delete an item.

    Returns 200 with an empty payload rather than 204, so that the response
    still carries the standard envelope.
    """
    item_service.delete_item(item_id)
    return ApiResponse[None].ok(message="Item deleted successfully")
