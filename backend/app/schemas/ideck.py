"""Virtual OLED i-deck payloads.

Two coordinate spaces appear here and are kept apart deliberately. *Panel*
coordinates come from the layout file and never change; *client* coordinates are
where a key currently sits inside the live window, and shift whenever the panel
is resized. Presses are addressed in client coordinates.

Buttons are named twice, too: ``name`` is the friendly alias from the game
config, while ``xml_id`` is what the panel layout calls the same key. Several
aliases may point at one key.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class IDeckWindowState(StrEnum):
    """Whether the panel window can be pressed right now."""

    UNSUPPORTED = "unsupported"
    """This build is not on Windows, so no window can be reached at all."""

    NOT_FOUND = "not_found"
    """No matching window; the panel service is probably not running."""

    MINIMIZED = "minimized"
    """Found, but iconic. It has no client area until restored."""

    ACCESS_DENIED = "access_denied"
    """Found, but Windows refuses us input to it -- see IDeckAccessDeniedError."""

    READY = "ready"


class IDeckButton(BaseModel):
    """One key on the deck, in both coordinate spaces."""

    name: str = Field(description="Friendly alias from the game config.")
    xml_id: str = Field(description="Key name in the panel layout.")
    button_id: int = Field(
        ge=0, description="Hardware switch number; the panel logs it in hex."
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Every configured name for this key; several may share one.",
    )
    panel_x: int = Field(description="Left edge in panel coordinates.")
    panel_y: int = Field(description="Top edge in panel coordinates.")
    width: int = Field(gt=0, description="Key width in panel coordinates.")
    height: int = Field(gt=0, description="Key height in panel coordinates.")
    client_x: int | None = Field(
        default=None,
        description="Centre X in the live window; null when no window is ready.",
    )
    client_y: int | None = Field(
        default=None,
        description="Centre Y in the live window; null when no window is ready.",
    )


class IDeckStatus(BaseModel):
    """What the service can see of the panel, and how it is configured."""

    state: IDeckWindowState = Field(description="Whether the panel can be pressed.")
    window_title: str = Field(description="Window title the service searches for.")
    window_class: str = Field(description="Window class the search is pinned to.")
    hwnd: int | None = Field(
        default=None, description="Handle of the matched window; null when absent."
    )
    client_width: int = Field(default=0, ge=0, description="Live client width.")
    client_height: int = Field(default=0, ge=0, description="Live client height.")
    panel_id: str | None = Field(
        default=None, description="Panel name from the layout file."
    )
    panel_width: int | None = Field(
        default=None, description="Width the layout declares."
    )
    panel_height: int | None = Field(
        default=None, description="Height the layout declares."
    )
    game: str = Field(description="Game config supplying the button aliases.")
    button_count: int = Field(
        default=0, ge=0, description="Number of aliases currently resolvable."
    )
    panel_xml: str = Field(description="Layout file the geometry was read from.")
    log_path: str = Field(description="Panel log used to confirm presses.")
    verify_presses: bool = Field(
        description="Whether presses are confirmed against the log before returning."
    )


class PressRequest(BaseModel):
    """Request body for a single press."""

    button: str = Field(
        min_length=1,
        max_length=64,
        description="Alias from the game config, or a layout key name.",
    )
    verify: bool | None = Field(
        default=None,
        description="Override IDECK_VERIFY_PRESSES for this press.",
    )
    hold_seconds: float | None = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="Override the button-down hold for this press.",
    )


class SequenceRequest(BaseModel):
    """Request body for pressing several keys in order."""

    buttons: list[str] = Field(
        min_length=1,
        max_length=32,
        description="Aliases to press, in order. The run stops at the first failure.",
    )
    delay_seconds: float = Field(
        default=0.5, ge=0.0, le=30.0, description="Pause between presses."
    )
    verify: bool | None = Field(
        default=None, description="Override IDECK_VERIFY_PRESSES for every press."
    )


class PressResult(BaseModel):
    """Outcome of one press.

    ``confirmed`` is the field to trust. A press that was posted but never
    appeared in the panel log comes back with ``confirmed`` false, and the
    endpoint fails, rather than reporting a success that did not happen.
    """

    button: str = Field(description="Alias that was pressed.")
    xml_id: str = Field(description="Layout key the alias resolved to.")
    button_id: int = Field(description="Hardware switch number that was driven.")
    client_x: int = Field(description="Client X the press was aimed at.")
    client_y: int = Field(description="Client Y the press was aimed at.")
    confirmed: bool = Field(
        description="Whether the panel log recorded the press landing."
    )
    verified: bool = Field(
        description="Whether confirmation was attempted; false means unchecked."
    )
    evidence: str | None = Field(
        default=None, description="The panel log line that confirmed the press."
    )
    restored: bool = Field(
        default=False, description="Whether the window had to be un-minimized first."
    )
    refocused: bool = Field(
        default=False,
        description="Whether the press had to be retried with the window focused.",
    )
    elapsed_ms: int = Field(ge=0, description="Time spent on this press.")


class ProbeResult(BaseModel):
    """Outcome of the side-effect-free capability check.

    Posts a mouse *move* and nothing else, so it proves the panel processes our
    synthetic input without touching game state.
    """

    supported: bool = Field(description="Whether Win32 interop is available here.")
    window_found: bool = Field(description="Whether the panel window was located.")
    posted: bool = Field(description="Whether the move message was posted.")
    observed: bool = Field(
        description="Whether the panel logged a reaction to the move."
    )
    evidence: str | None = Field(
        default=None, description="The panel log line that showed the reaction."
    )
    detail: str = Field(description="Human-readable summary, safe to show in UI.")
