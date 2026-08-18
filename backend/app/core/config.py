"""Backward-compatible import location for runtime settings.

The implementation now lives in :mod:`app.config.runtime`; this module stays
as a small compatibility shim for existing imports and integrations.
"""

from app.config.runtime import (
    PACKAGE_ROOT,
    CsvList,
    Environment,
    Settings,
    get_settings,
    settings,
)

__all__ = [
    "PACKAGE_ROOT",
    "CsvList",
    "Environment",
    "Settings",
    "get_settings",
    "settings",
]
