"""GAF automation payloads: driving the running game by calling its own
methods rather than by aiming clicks at it."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class GafState(StrEnum):
    """What the service can see of the automation chain right now.

    Ordered by how far along the chain the problem is, which is also the
    order they are checked in: nothing to drive, nothing to drive it with,
    nothing to name its controls, then simply not connected yet.
    """

    NOT_CONFIGURED = "not_configured"
    """The active game declares no ``gaf`` block, so it cannot be driven."""

    UNREACHABLE = "unreachable"
    """No answer from the NRobot server. It is a separate process this repo
    neither starts nor supervises -- ``NRobotStartUpScript.bat`` does."""

    FILES_MISSING = "files_missing"
    """The server is up, but the object-query files are not where the game
    config says. Usually an unsynced Perforce workspace."""

    DISCONNECTED = "disconnected"
    """Everything is in place; no session is open yet. Actions open one."""

    READY = "ready"
    """A session is open and the game is answering."""


class GafQueryFile(BaseModel):
    """One object-query file, and whether it is really there."""

    path: str = Field(description="Absolute path the config resolved to.")
    group: str = Field(
        description="Which client call it feeds: 'general' or 'generic'."
    )
    present: bool = Field(description="Whether a readable file exists at that path.")


class GafStatus(BaseModel):
    """What the service can see of the chain, and how it is configured.

    Always answered with 200 -- check ``state``, not the status code. Reading
    it never opens a session, so a dashboard can poll it without connecting
    to the game as a side effect.
    """

    state: GafState = Field(description="Whether the game can be driven.")
    game: str = Field(description="Game currently selected for this backend.")
    server_url: str = Field(description="NRobot server this backend calls.")
    host: str = Field(description="Host the game's automation service is on.")
    port: int = Field(description="Port the game's automation service is on.")
    game_type: str = Field(description="Theme wrapper the client is built with.")
    gdk_version: str = Field(description="GDK client wrapper version in use.")
    connected: bool = Field(description="Whether a session is currently held.")
    query_files: list[GafQueryFile] = Field(
        default_factory=list,
        description="Object-query files in merge order, game-specific last.",
    )
    object_count: int | None = Field(
        default=None,
        description="Named controls in the merged dictionary; null until connected.",
    )
    idle_state: str | None = Field(
        default=None,
        description="IdleStateMachine's current state; null unless connected.",
    )
    detail: str = Field(description="Human-readable summary, safe to show in UI.")


class GafMeters(BaseModel):
    """The three meters, exactly as the game reports them.

    Strings, not numbers, and deliberately: the game answers ``"$995.80"`` or
    ``"1,250"`` depending on whether the strip is drawn in cash or credits,
    and parsing that here would throw away the one thing this reading has
    over OCR -- that it is the game's own text.
    """

    credit: str | None = Field(default=None, description="CreditMeter value.")
    bet: str | None = Field(default=None, description="BetMeter value.")
    win: str | None = Field(
        default=None,
        description=(
            "WinMeter value. Read wherever the count-up had got to, so only a "
            "post-collect reading is the real figure."
        ),
    )


class SpinOutcome(StrEnum):
    """How a spin ended."""

    IDLE = "idle"
    """The game returned to an idle state on its own: no win to collect."""

    WIN_OFFERED = "win_offered"
    """The game is holding in play with a win waiting to be taken.

    A win *holds* the machine in ``statePlaying`` until it is collected, so
    waiting only for idle would time out on exactly the spins worth having.
    """

    TIMEOUT = "timeout"
    """Neither happened within the settle timeout. Reported rather than
    raised: the state it was stuck in is the diagnosis."""


class SpinRequest(BaseModel):
    """Request body for one spin."""

    force: bool = Field(
        default=False,
        description=(
            "Press even while the game is still playing. Off, a game holding "
            "an uncollected win or running a bonus is refused rather than "
            "pressed into -- the result of that press belongs to the spin "
            "that has not finished."
        ),
    )
    settle: bool = Field(
        default=True,
        description=(
            "Wait for the spin to finish before answering. Off returns as soon "
            "as the button press is acknowledged, which is a press and not a "
            "result."
        ),
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0.0,
        le=600.0,
        description="Override GAF_SETTLE_TIMEOUT_SECONDS for this spin.",
    )
    read_meters: bool | None = Field(
        default=None, description="Override GAF_READ_METERS for this spin."
    )


class SpinResult(BaseModel):
    """Outcome of one spin."""

    game: str = Field(description="Game that was spun.")
    pressed: bool = Field(description="Whether the spin button press was accepted.")
    outcome: SpinOutcome = Field(description="How the spin ended.")
    settled: bool = Field(description="Whether the result was waited for at all.")
    idle_state: str | None = Field(
        default=None, description="IdleStateMachine's state when the wait ended."
    )
    game_state: str | None = Field(
        default=None, description="SlotGameStateMachine's state when the wait ended."
    )
    win_offered: bool = Field(
        default=False,
        description="Whether a win is waiting to be collected. Call take-win.",
    )
    meters: GafMeters | None = Field(
        default=None,
        description="Meters after the spin; null when the read was off or failed.",
    )
    elapsed_ms: int = Field(ge=0, description="Time spent on this spin.")
    detail: str = Field(description="Human-readable summary, safe to show in UI.")


class TakeWinRequest(BaseModel):
    """Request body for collecting a win."""

    force: bool = Field(
        default=False,
        description=(
            "Press even when the game reports the button is not interactable. "
            "Off, an uncollectable win is reported as skipped rather than "
            "pressed into the void."
        ),
    )
    settle: bool = Field(
        default=True, description="Wait for the game to return to idle afterwards."
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0.0,
        le=600.0,
        description="Override GAF_SETTLE_TIMEOUT_SECONDS for this collect.",
    )
    read_meters: bool | None = Field(
        default=None, description="Override GAF_READ_METERS for this collect."
    )


class TakeWinResult(BaseModel):
    """Outcome of one take-win."""

    game: str = Field(description="Game the win was collected on.")
    button: str = Field(description="Non-wager button that was driven.")
    interactable: bool = Field(
        description="Whether the game said the button could be pressed."
    )
    pressed: bool = Field(
        description=(
            "Whether the press was sent. False with no error means there was "
            "nothing to collect -- a different fact from a failed press."
        )
    )
    settled: bool = Field(description="Whether the result was waited for at all.")
    idle_state: str | None = Field(
        default=None, description="IdleStateMachine's state when the wait ended."
    )
    meters: GafMeters | None = Field(
        default=None,
        description=(
            "Meters after collecting. Unlike a spin's, this win figure is "
            "post-collect and so is the real one."
        ),
    )
    elapsed_ms: int = Field(ge=0, description="Time spent on this collect.")
    detail: str = Field(description="Human-readable summary, safe to show in UI.")
