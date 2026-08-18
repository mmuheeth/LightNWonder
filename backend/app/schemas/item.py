"""Schemas for the example ``items`` resource.

Reference for the request/response split; delete along with the rest of the
example. Note the three-model pattern: a create model, a partial update model,
and an output model.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ItemBase(BaseModel):
    """Fields shared by input and output models."""

    name: str = Field(min_length=1, max_length=120, description="Display name.")
    description: str | None = Field(
        default=None, max_length=1000, description="Optional long-form text."
    )
    quantity: int = Field(default=0, ge=0, description="Units in stock.")


class ItemCreate(ItemBase):
    """Request body for creating an item."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"name": "Table lamp", "description": "Brass, dimmable", "quantity": 12}
            ]
        }
    )


class ItemUpdate(BaseModel):
    """Request body for a partial update; unset fields are left untouched."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    quantity: int | None = Field(default=None, ge=0)


class ItemOut(ItemBase):
    """Item as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Unique identifier.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last modification timestamp (UTC).")
