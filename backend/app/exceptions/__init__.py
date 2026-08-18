"""Application exception types and their HTTP translation layer."""

from app.exceptions.base import (
    AppException,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    IDeckAccessDeniedError,
    IDeckButtonNotFoundError,
    IDeckConfigError,
    IDeckPressNotConfirmedError,
    IDeckWindowNotFoundError,
    NotFoundError,
    ObsConnectionError,
    ObsNotConnectedError,
    ObsRequestError,
    RateLimitError,
    ServiceUnavailableError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from app.exceptions.handlers import register_exception_handlers

__all__ = [
    "AppException",
    "BadRequestError",
    "ConflictError",
    "ForbiddenError",
    "IDeckAccessDeniedError",
    "IDeckButtonNotFoundError",
    "IDeckConfigError",
    "IDeckPressNotConfirmedError",
    "IDeckWindowNotFoundError",
    "NotFoundError",
    "ObsConnectionError",
    "ObsNotConnectedError",
    "ObsRequestError",
    "RateLimitError",
    "ServiceUnavailableError",
    "UnauthorizedError",
    "UnprocessableEntityError",
    "register_exception_handlers",
]
