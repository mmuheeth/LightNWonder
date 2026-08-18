"""OBS Studio payloads.

Field names here are snake_case even though obs-websocket speaks camelCase on
the wire; :mod:`app.services.obs` does the translation so the HTTP contract
matches the rest of the API.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ObsConnectionState(StrEnum):
    """Whether the service currently holds an identified OBS session."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"


class ObsRecordStatus(BaseModel):
    """State of the OBS recording output."""

    active: bool = Field(description="Whether a recording is in progress.")
    paused: bool = Field(description="Whether the active recording is paused.")
    timecode: str | None = Field(
        default=None, description="Elapsed recording time as HH:MM:SS.mmm."
    )
    duration_ms: int = Field(
        default=0, ge=0, description="Elapsed recording time in milliseconds."
    )
    bytes_written: int = Field(
        default=0, ge=0, description="Bytes written to the recording so far."
    )
    output_path: str | None = Field(
        default=None,
        description="Path of the finished file; only set by the stop endpoint.",
    )


class ObsStatus(BaseModel):
    """Connection state and, when connected, what OBS reports about itself."""

    state: ObsConnectionState = Field(description="Current connection state.")
    url: str = Field(description="WebSocket URL the service connects to.")
    obs_version: str | None = Field(
        default=None, description="OBS Studio version; null while disconnected."
    )
    obs_websocket_version: str | None = Field(
        default=None, description="obs-websocket plugin version."
    )
    platform: str | None = Field(
        default=None, description="Platform OBS runs on, e.g. 'windows'."
    )
    current_scene: str | None = Field(
        default=None, description="Name of the active program scene."
    )
    recording: ObsRecordStatus | None = Field(
        default=None, description="Recording state; null while disconnected."
    )


class ScreenshotRequest(BaseModel):
    """Request body for capturing a screenshot.

    Omit ``file_name`` to receive the image inline as a base64 data URI. Provide
    it to have OBS write the file into the configured capture directory
    instead.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"image_format": "png", "width": 1280, "quality": -1}]
        }
    )

    source_name: str | None = Field(
        default=None,
        description="Source or scene to capture; defaults to the program scene.",
    )
    image_format: str = Field(
        default="png",
        min_length=1,
        max_length=10,
        description="Image format, e.g. 'png', 'jpeg' or 'bmp'.",
    )
    width: int | None = Field(
        default=None, ge=8, le=4096, description="Output width in pixels."
    )
    height: int | None = Field(
        default=None, ge=8, le=4096, description="Output height in pixels."
    )
    quality: int = Field(
        default=-1,
        ge=-1,
        le=100,
        description="Compression quality; -1 uses the format default.",
    )
    file_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Bare filename to write into the capture directory. Must not "
            "contain a path separator. Omit to receive base64 instead."
        ),
    )


class ScreenshotResult(BaseModel):
    """Outcome of a screenshot request.

    Exactly one of ``image_data`` and ``file_path`` is populated, depending on
    whether the request asked for a file.
    """

    source_name: str = Field(description="Source or scene that was captured.")
    image_format: str = Field(description="Format the image was encoded in.")
    image_data: str | None = Field(
        default=None,
        description="Base64 data URI, ready to assign to an <img> src.",
    )
    file_path: str | None = Field(
        default=None, description="Absolute path OBS wrote the file to."
    )
