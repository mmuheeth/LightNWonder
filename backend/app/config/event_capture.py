"""Event Based Capture runtime settings.

As with :mod:`app.config.obs` and :mod:`app.config.ideck`, the environment
variable names stay flat (``EVENT_CAPTURE_*``) and
:class:`app.config.runtime.Settings` inherits this model, so callers keep the
usual ``settings.EVENT_CAPTURE_*`` access while the defaults live beside the
feature they configure.

Runs are written below the OBS screenshot root rather than getting a root of
their own: the screenshots are taken by OBS, and OBS will only write inside a
directory it has been given, so a second root would just be a second thing to
keep in sync.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings

__all__ = ["EventCaptureSettings"]


class EventCaptureSettings(BaseSettings):
    """Runtime options for log-driven event capture."""

    # Subdirectory of the OBS screenshot root that holds one folder per run.
    EVENT_CAPTURE_DIR_NAME: str = "event-capture"

    # How often the watcher reads whatever the game appended. The game writes
    # continuously, so this is the latency between an event and its screenshot,
    # not a throughput limit.
    EVENT_CAPTURE_POLL_SECONDS: float = 0.25

    # One logical event is often logged twice -- once as the message being
    # published and once as the state transition it caused, milliseconds apart.
    # Repeats of the same event closer together than this are treated as one.
    #
    # Measured against real logs: at 0.3s every genuine spin survives and the
    # duplicates collapse. Raising it to 0.75s starts eating real spins that
    # followed each other quickly.
    EVENT_CAPTURE_DEBOUNCE_SECONDS: float = 0.3

    # Screenshots are evidence, not masters. Full resolution runs to several MB
    # each and a long session would produce hundreds.
    EVENT_CAPTURE_SCREENSHOT_WIDTH: int = 1280

    # A run left going overnight should not fill the disk. Past the cap events
    # are still recorded, but they stop taking screenshots.
    EVENT_CAPTURE_MAX_EVENTS: int = 2000

    # How many recent events the status endpoint reports, for the dashboard card.
    EVENT_CAPTURE_RECENT_EVENTS: int = 5
