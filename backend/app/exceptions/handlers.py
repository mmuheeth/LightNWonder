"""Exception handlers: the only place that builds an error response, covering
our own AppException, validation errors, framework HTTPExceptions, and anything
else (logged and reported as an opaque 500).
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.context import REQUEST_ID_SCOPE_KEY, get_request_id
from app.core.logging import get_logger
from app.exceptions.base import AppException
from app.schemas.response import ApiResponse, ErrorDetail, ResponseMeta

logger = get_logger("exceptions")


def _error_code_for_status(status_code: int) -> str:
    """Derive a stable error code from an HTTP status, e.g. 404 -> NOT_FOUND."""
    try:
        return HTTPStatus(status_code).name
    except ValueError:
        return f"HTTP_{status_code}"


def _resolve_request_id(request: Request) -> str | None:
    """Read the correlation id from the scope (authoritative once the request
    has unwound and the context var is reset), falling back to the context var."""
    scoped = request.scope.get(REQUEST_ID_SCOPE_KEY)
    return scoped if isinstance(scoped, str) else get_request_id()


def _render(
    request: Request,
    *,
    status_code: int,
    message: str,
    code: str,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Serialise a failure into the standard envelope."""
    envelope = ApiResponse[None].fail(
        message,
        code=code,
        details=details,
        meta=ResponseMeta.build(_resolve_request_id(request)),
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers=headers,
    )


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Handle deliberate, expected failures raised by application code."""
    log = logger.warning if exc.status_code < 500 else logger.error
    log(
        "%s %s -> %s %s: %s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.error_code,
        exc.message,
        exc_info=exc.status_code >= 500,
    )
    return _render(
        request,
        status_code=exc.status_code,
        message=exc.message,
        code=exc.error_code,
        details=exc.details,
        headers=exc.headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Flatten pydantic validation errors into ``error.details``."""
    details = [
        ErrorDetail(
            # loc is like ("body", "items", 0, "name"); drop the leading
            # location marker only when a field path follows it.
            field=".".join(str(part) for part in error.get("loc", ())) or None,
            message=error.get("msg", "Invalid value"),
            type=error.get("type"),
        )
        for error in exc.errors()
    ]
    logger.info(
        "%s %s -> 422 VALIDATION_ERROR (%d problem(s))",
        request.method,
        request.url.path,
        len(details),
    )
    return _render(
        request,
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        message="Request validation failed",
        code="VALIDATION_ERROR",
        details=details,
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Wrap framework-raised HTTP errors in the standard envelope."""
    detail = exc.detail if isinstance(exc.detail, str) else None
    status = HTTPStatus(exc.status_code) if exc.status_code in _KNOWN_STATUSES else None
    message = detail or (status.phrase if status else "Request failed")
    headers = dict(exc.headers) if exc.headers else None
    return _render(
        request,
        status_code=exc.status_code,
        message=message,
        code=_error_code_for_status(exc.status_code),
        headers=headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defence: log the traceback, return an opaque 500."""
    logger.exception(
        "Unhandled %s on %s %s",
        type(exc).__name__,
        request.method,
        request.url.path,
    )
    # Only echo the exception text when debugging; production stays opaque.
    details = (
        [ErrorDetail(message=f"{type(exc).__name__}: {exc}", type="unhandled")]
        if settings.DEBUG
        else []
    )
    return _render(
        request,
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        message="An unexpected error occurred",
        code="INTERNAL_SERVER_ERROR",
        details=details,
    )


_KNOWN_STATUSES = frozenset(status.value for status in HTTPStatus)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every handler to ``app``. Called once from the app factory."""
    app.add_exception_handler(AppException, app_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, unhandled_exception_handler)
