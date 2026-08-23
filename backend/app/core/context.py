"""Per-request context: the request id lives in a ContextVar so response
builders, loggers and exception handlers can reach it without an extra argument.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

#: Key the middleware also stores the id under on the ASGI scope, which
#: outlives the ContextVar reset -- the reliable source for error responses.
REQUEST_ID_SCOPE_KEY = "request_id"


def get_request_id() -> str | None:
    """Return the current request id, or ``None`` outside a request."""
    return _request_id.get()


def set_request_id(request_id: str) -> Token[str | None]:
    """Bind ``request_id`` to the current context."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the request id that was set before :func:`set_request_id`."""
    _request_id.reset(token)


def new_request_id() -> str:
    """Generate an id for an inbound request that did not supply one."""
    return uuid.uuid4().hex
