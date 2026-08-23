"""The single response envelope used by every endpoint and every error:
``{success, message, data, error, meta}``, with ``data`` null on failure and
``error`` null on success -- both keys always present."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.context import get_request_id

T = TypeVar("T")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ErrorDetail(BaseModel):
    """A single field-level or resource-level problem."""

    model_config = ConfigDict(populate_by_name=True)

    field: str | None = Field(
        default=None,
        description="Dotted path to the offending input, e.g. 'body.email'.",
    )
    message: str = Field(description="Human-readable description of the problem.")
    type: str | None = Field(
        default=None, description="Machine-readable problem type, e.g. 'missing'."
    )


class ApiError(BaseModel):
    """Machine-readable error information."""

    code: str = Field(
        description="Stable, screaming-snake-case error code, e.g. 'NOT_FOUND'."
    )
    details: list[ErrorDetail] = Field(
        default_factory=list,
        description="Per-field problems; empty for errors that are not field-scoped.",
    )


class ResponseMeta(BaseModel):
    """Envelope metadata attached to every response."""

    request_id: str | None = Field(
        default=None, description="Correlation id, echoed in the X-Request-ID header."
    )
    timestamp: datetime = Field(
        default_factory=_utc_now, description="Server time the response was built."
    )

    @classmethod
    def build(cls, request_id: str | None = None) -> ResponseMeta:
        """Build metadata; handlers pass ``request_id`` explicitly since the
        context var may already be reset by the time they run."""
        return cls(request_id=request_id or get_request_id())


class PaginationMeta(ResponseMeta):
    """Envelope metadata for list endpoints."""

    page: int = Field(ge=1, description="Current 1-based page number.")
    page_size: int = Field(ge=1, description="Maximum items per page.")
    total_items: int = Field(ge=0, description="Total matching items.")
    total_pages: int = Field(ge=0, description="Total number of pages.")
    has_next: bool = Field(description="Whether a following page exists.")
    has_previous: bool = Field(description="Whether a preceding page exists.")

    @classmethod
    def build_page(
        cls, *, page: int, page_size: int, total_items: int
    ) -> PaginationMeta:
        """Derive the pagination block from the raw counts."""
        total_pages = math.ceil(total_items / page_size) if page_size else 0
        return cls(
            request_id=get_request_id(),
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1,
        )


class ApiResponse(BaseModel, Generic[T]):
    """Standard envelope wrapping a single payload."""

    success: bool = Field(description="True for 2xx responses, False otherwise.")
    message: str = Field(description="Human-readable summary, safe to surface in UI.")
    data: T | None = Field(default=None, description="Payload; null on failure.")
    error: ApiError | None = Field(
        default=None, description="Error information; null on success."
    )
    meta: ResponseMeta = Field(default_factory=ResponseMeta.build)

    @classmethod
    def ok(
        cls,
        data: T | None = None,
        message: str = "Request completed successfully",
        *,
        meta: ResponseMeta | None = None,
    ) -> ApiResponse[T]:
        """Build a success envelope."""
        return cls(
            success=True,
            message=message,
            data=data,
            error=None,
            meta=meta or ResponseMeta.build(),
        )

    @classmethod
    def fail(
        cls,
        message: str,
        *,
        code: str,
        details: list[ErrorDetail] | None = None,
        meta: ResponseMeta | None = None,
    ) -> ApiResponse[T]:
        """Build a failure envelope; endpoints should raise an
        :class:`~app.exceptions.base.AppException` subclass instead."""
        return cls(
            success=False,
            message=message,
            data=None,
            error=ApiError(code=code, details=details or []),
            meta=meta or ResponseMeta.build(),
        )


class PaginatedResponse(ApiResponse[list[T]], Generic[T]):
    """Envelope for list endpoints; ``meta`` carries the pagination block."""

    meta: PaginationMeta

    @classmethod
    def paginate(
        cls,
        items: list[T],
        *,
        page: int,
        page_size: int,
        total_items: int,
        message: str = "Request completed successfully",
    ) -> PaginatedResponse[T]:
        """Build a paginated success envelope."""
        return cls(
            success=True,
            message=message,
            data=items,
            error=None,
            meta=PaginationMeta.build_page(
                page=page, page_size=page_size, total_items=total_items
            ),
        )
