"""Request correlation, timing and access logging. Raw ASGI (not
``BaseHTTPMiddleware``) so headers can be injected without buffering the body.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import (
    REQUEST_ID_SCOPE_KEY,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from app.core.logging import get_logger

logger = get_logger("access")

PROCESS_TIME_HEADER = "X-Process-Time"


class RequestContextMiddleware:
    """Assign a request id (reusing an inbound ``X-Request-ID`` if present),
    time the request, and emit one access log line."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        header_name: str = "X-Request-ID",
        quiet_paths: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.header_name = header_name
        self.quiet_paths = quiet_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = Headers(scope=scope).get(self.header_name) or new_request_id()
        # Both: the context var serves loggers and response builders downstream,
        # the scope entry survives the unwind for the 500 handler above us.
        scope[REQUEST_ID_SCOPE_KEY] = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()
        # Defaults cover the case where the app raises before sending anything.
        response: dict[str, Any] = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response["status"] = message["status"]
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                headers[self.header_name] = request_id
                headers[PROCESS_TIME_HEADER] = f"{_elapsed_ms(started):.2f}"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            emit = logger.debug if path in self.quiet_paths else logger.info
            emit(
                "%s %s -> %s (%.2fms)",
                scope.get("method", "-"),
                path,
                response["status"],
                _elapsed_ms(started),
            )
            reset_request_id(token)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
