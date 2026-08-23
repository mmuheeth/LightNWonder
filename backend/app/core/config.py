"""Backward-compatible re-export shim; implementation lives in :mod:`app.config.runtime`."""

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
