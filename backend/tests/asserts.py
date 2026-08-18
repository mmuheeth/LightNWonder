"""Reusable assertions about the response envelope."""

from __future__ import annotations

from typing import Any

ENVELOPE_KEYS = {"success", "message", "data", "error", "meta"}


def assert_envelope(payload: dict[str, Any]) -> None:
    """Assert the invariants that hold for *every* response."""
    assert set(payload) == ENVELOPE_KEYS, f"unexpected envelope keys: {set(payload)}"
    assert isinstance(payload["success"], bool)
    assert isinstance(payload["message"], str) and payload["message"]
    meta = payload["meta"]
    assert isinstance(meta, dict)
    assert meta["request_id"], "request_id must be populated by the middleware"
    assert meta["timestamp"]


def assert_success(payload: dict[str, Any]) -> Any:
    """Assert a success envelope and return its ``data``."""
    assert_envelope(payload)
    assert payload["success"] is True
    assert payload["error"] is None
    return payload["data"]


def assert_failure(payload: dict[str, Any], *, code: str) -> dict[str, Any]:
    """Assert a failure envelope carrying ``code`` and return its ``error``."""
    assert_envelope(payload)
    assert payload["success"] is False
    assert payload["data"] is None
    error = payload["error"]
    assert error is not None, "failure responses must populate 'error'"
    assert error["code"] == code, f"expected code {code!r}, got {error['code']!r}"
    assert isinstance(error["details"], list)
    return error
