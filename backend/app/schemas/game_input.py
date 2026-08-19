"""Game-window input payloads.

Two coordinate spaces appear here and are kept apart deliberately. *Fractional*
coordinates come from the game config and never change; *client* coordinates are
where a target currently sits inside the live window, and shift whenever the
simulator is resized. Clicks are addressed in client coordinates.

The field to trust on a result is ``confirmed``, and ``confirmed_by`` says how
strong that proof is: the game naming the button it hit is a different claim from
the game merely admitting it felt a touch.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class GameWindowState(StrEnum):
    """Whether the game window can be clicked right now."""

    UNSUPPORTED = "unsupported"
    """This build is not on Windows, so no window can be reached at all."""

    NOT_FOUND = "not_found"
    """No matching window; the game is probably not running."""

    MINIMIZED = "minimized"
    """Found, but iconic. It has no client area until restored."""

    ACCESS_DENIED = "access_denied"
    """Found, but Windows refuses us input -- see GameInputAccessDeniedError."""

    READY = "ready"


class ClickConfirmation(StrEnum):
    """How a click was proven to have landed."""

    TARGET_EVENT = "target-event"
    """The game published the event this target is meant to cause.

    The strong proof: it names the button that was hit, so the coordinates are
    known to have been right and not merely to have hit *something*.
    """

    TOUCH = "touch"
    """The game registered a touch, but nothing said which button it hit.

    All that is available for a target whose own event is not configured. It
    still rules out the failures that actually happen -- a blocked or ignored
    message, the wrong window, no client area.
    """


class ClickTargetInfo(BaseModel):
    """One configured target, in both coordinate spaces."""

    name: str = Field(description="Target name from the game config.")
    fraction_x: float = Field(
        ge=0.0, le=1.0, description="Horizontal position, as a fraction of the window."
    )
    fraction_y: float = Field(
        ge=0.0, le=1.0, description="Vertical position, as a fraction of the window."
    )
    confirm_event: str | None = Field(
        default=None,
        description=(
            "Game-log event that proves a click here landed; null when none is "
            "known, in which case only a generic touch can be confirmed."
        ),
    )
    client_x: int | None = Field(
        default=None,
        description="Position in the live window; null when no window is ready.",
    )
    client_y: int | None = Field(
        default=None,
        description="Position in the live window; null when no window is ready.",
    )


class GameInputStatus(BaseModel):
    """What the service can see of the game window, and how it is configured."""

    state: GameWindowState = Field(description="Whether the window can be clicked.")
    window_title: str = Field(description="Window title the service searches for.")
    window_class: str = Field(description="Window class the search is pinned to.")
    hwnd: int | None = Field(
        default=None, description="Handle of the matched window; null when absent."
    )
    client_width: int = Field(default=0, ge=0, description="Live client width.")
    client_height: int = Field(default=0, ge=0, description="Live client height.")
    game: str = Field(description="Game config supplying the button targets.")
    target_count: int = Field(
        default=0, ge=0, description="Number of targets currently resolvable."
    )
    log_path: str = Field(description="Game log used to confirm clicks.")
    verify_clicks: bool = Field(
        description="Whether clicks are confirmed against the log before returning."
    )


class ClickRequest(BaseModel):
    """Request body for a single click."""

    target: str = Field(
        min_length=1,
        max_length=64,
        description="Target name from the active game's button_targets block.",
    )
    verify: bool | None = Field(
        default=None, description="Override GAME_INPUT_VERIFY_CLICKS for this click."
    )
    hold_seconds: float | None = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="Override the button-down hold for this click.",
    )


class ClickResult(BaseModel):
    """Outcome of one click.

    ``confirmed`` is the field to trust. A click that was posted but that the
    game never reacted to comes back with ``confirmed`` false, and the endpoint
    fails, rather than reporting a success that did not happen.
    """

    target: str = Field(description="Target that was clicked.")
    game: str = Field(description="Game config the target was resolved against.")
    fraction_x: float = Field(description="Configured horizontal fraction.")
    fraction_y: float = Field(description="Configured vertical fraction.")
    client_x: int = Field(description="Client X the click was aimed at.")
    client_y: int = Field(description="Client Y the click was aimed at.")
    confirmed: bool = Field(
        description="Whether the game log recorded the click landing."
    )
    confirmed_by: ClickConfirmation | None = Field(
        default=None,
        description="How it was proven; null when unconfirmed or unverified.",
    )
    verified: bool = Field(
        description="Whether confirmation was attempted; false means unchecked."
    )
    expected_event: str | None = Field(
        default=None,
        description="Game-log event that was waited for, when the target names one.",
    )
    evidence: str | None = Field(
        default=None, description="The game log line that confirmed the click."
    )
    restored: bool = Field(
        default=False, description="Whether the window had to be un-minimized first."
    )
    refocused: bool = Field(
        default=False,
        description="Whether the click had to be retried with the window focused.",
    )
    elapsed_ms: int = Field(ge=0, description="Time spent on this click.")
