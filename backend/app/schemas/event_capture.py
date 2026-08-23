"""Event capture payloads; also the shape of the ``run.json`` manifest
written to disk, deliberately shared so the UI and the folder never disagree."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CaptureRunState(StrEnum):
    """How a run ended, or that it has not."""

    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"  # backend shut down while the run was going


class CapturedEvent(BaseModel):
    """One recognised log event and the screenshot taken for it."""

    sequence: int = Field(ge=1, description="1-based position within the run.")
    event: str = Field(description="Rule name, e.g. 'spin-started'.")
    at: datetime | None = Field(
        default=None, description="Game's own timestamp for the line."
    )
    summary: str = Field(description="One-line description of what happened.")
    fields: dict[str, str] = Field(
        default_factory=dict,
        description="Values the rule extracted, e.g. {'denom': '100.000'}.",
    )
    screenshot: str | None = Field(
        default=None,
        description="Image filename inside the run directory; null if it failed.",
    )
    capture_error: str | None = Field(
        default=None, description="Why the screenshot failed, when it did."
    )
    log_line: str = Field(description="The log line the rule matched, verbatim.")


class CaptureRunSummary(BaseModel):
    """A run as it appears in the list, without its events."""

    run_id: str = Field(description="Directory name, 'YYYY-MM-DD_HH-MM-SS'.")
    game: str = Field(description="Game that was being played.")
    status: CaptureRunState = Field(description="Whether and how the run ended.")
    started_at: datetime = Field(description="When tracking started.")
    stopped_at: datetime | None = Field(
        default=None, description="When tracking stopped; null while running."
    )
    event_count: int = Field(ge=0, description="Number of events captured.")
    log_path: str = Field(description="Game log that was followed.")


class CaptureRunDetail(CaptureRunSummary):
    """A whole run, including every event. This is the ``run.json`` shape."""

    events: list[CapturedEvent] = Field(
        default_factory=list, description="Events in the order they happened."
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Problems hit during the run that did not stop it.",
    )


class CaptureStatus(BaseModel):
    """What the dashboard card polls; always returned, running or not."""

    active: bool = Field(description="Whether a run is in progress.")
    run_id: str | None = Field(default=None, description="Active run, if any.")
    game: str | None = Field(
        default=None,
        description=(
            "Game the active run is following. A run keeps its game even if the "
            "active selection changes, so this can differ from the selected game."
        ),
    )
    started_at: datetime | None = Field(default=None, description="When it started.")
    duration_ms: int = Field(default=0, ge=0, description="Elapsed run time.")
    event_count: int = Field(default=0, ge=0, description="Events captured so far.")
    recent_events: list[CapturedEvent] = Field(
        default_factory=list, description="The most recent events, newest last."
    )
    errors: list[str] = Field(
        default_factory=list, description="Non-fatal problems so far."
    )
