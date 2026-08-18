"""Domain exception hierarchy.

Business code raises these; :mod:`app.exceptions.handlers` turns them into the
standard response envelope. Nothing outside the handlers should build an HTTP
response for an error.

Add a new error type by subclassing :class:`AppException` and setting the three
class attributes::

    class PaymentRequiredError(AppException):
        status_code = 402
        error_code = "PAYMENT_REQUIRED"
        message = "Payment is required to continue"
"""

from __future__ import annotations

from http import HTTPStatus

from app.schemas.response import ErrorDetail


class AppException(Exception):
    """Base class for every expected, client-facing failure.

    Attributes:
        status_code: HTTP status to respond with.
        error_code: Stable code clients can branch on.
        message: Default human-readable message.
    """

    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    error_code: str = "INTERNAL_SERVER_ERROR"
    message: str = "An unexpected error occurred"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: list[ErrorDetail] | None = None,
        error_code: str | None = None,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or []
        self.error_code = error_code or self.error_code
        self.status_code = status_code or self.status_code
        self.headers = headers
        super().__init__(self.message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status_code={self.status_code}, "
            f"error_code={self.error_code!r}, message={self.message!r})"
        )


class BadRequestError(AppException):
    status_code = HTTPStatus.BAD_REQUEST
    error_code = "BAD_REQUEST"
    message = "The request could not be understood"


class UnauthorizedError(AppException):
    status_code = HTTPStatus.UNAUTHORIZED
    error_code = "UNAUTHORIZED"
    message = "Authentication is required"


class ForbiddenError(AppException):
    status_code = HTTPStatus.FORBIDDEN
    error_code = "FORBIDDEN"
    message = "You do not have permission to perform this action"


class NotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "NOT_FOUND"
    message = "The requested resource was not found"


class ConflictError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "CONFLICT"
    message = "The request conflicts with the current state of the resource"


class UnprocessableEntityError(AppException):
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    error_code = "UNPROCESSABLE_ENTITY"
    message = "The request was well-formed but could not be processed"


class RateLimitError(AppException):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    error_code = "RATE_LIMIT_EXCEEDED"
    message = "Too many requests; please retry later"


class ServiceUnavailableError(AppException):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    error_code = "SERVICE_UNAVAILABLE"
    message = "The service is temporarily unavailable"
