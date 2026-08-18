"""Uvicorn entrypoint.

Run with any of::

    python -m app             # uses .env / defaults
    serve                     # console script from pyproject
    uvicorn app.main:app --reload --port 8001
"""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings


def main() -> None:
    """Start the ASGI server."""
    settings = get_settings()
    uvicorn.run(
        # Import string (not the app object) so --reload can re-import.
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD and not settings.is_production,
        # Our RequestContextMiddleware emits the access log with request ids.
        access_log=False,
        log_config=None,
        server_header=False,
        date_header=True,
    )


if __name__ == "__main__":
    main()
