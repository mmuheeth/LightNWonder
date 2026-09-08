"""Event Based Capture runtime settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings

__all__ = ["EventCaptureSettings"]


class EventCaptureSettings(BaseSettings):
    """Runtime options for log-driven event capture."""

    # Subdirectory of the OBS screenshot root that holds one folder per run.
    EVENT_CAPTURE_DIR_NAME: str = "event-capture"

    # Latency between an event and its screenshot, not a throughput limit.
    EVENT_CAPTURE_POLL_SECONDS: float = 0.25

    # Collapses a logical event logged twice (message + state transition) into
    # one. Tuned against real logs: 0.3s keeps all genuine spins, 0.75s starts
    # eating fast repeats.
    EVENT_CAPTURE_DEBOUNCE_SECONDS: float = 0.3

    # Screenshots are evidence, not masters, so kept well under full resolution.
    EVENT_CAPTURE_SCREENSHOT_WIDTH: int = 1280

    # Past the cap, events still record but stop taking screenshots.
    EVENT_CAPTURE_MAX_EVENTS: int = 2000

    # How many recent events the status endpoint reports, for the dashboard card.
    EVENT_CAPTURE_RECENT_EVENTS: int = 5
