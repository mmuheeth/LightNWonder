"""Payloads for Replay: one scripted walk through the attendant menu to the
latest game-play record, over the three windows that own the buttons."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

__all__ = [
    "ReplayLogEntry",
    "ReplayLogLevel",
    "ReplayMenuInfo",
    "ReplayMoment",
    "ReplayRun",
    "ReplayRunState",
    "ReplayScreenshot",
    "ReplayStatus",
    "ReplayStep",
    "ReplayStepState",
    "ReplayWindow",
    "ReplayWindowInfo",
    "ReplayWindowState",
]


# --- vocabulary -----------------------------------------------------------


class ReplayWindow(StrEnum):
    """Which window a step clicks in. Three, and each is clicked a different
    way -- which is the whole reason the sequence is not a list of points."""

    DEVTOOL = "devtool"
    """The I/O hub simulator. WinForms, so its buttons are found by caption."""

    SYSTEM_ADMIN = "system-admin"
    """The attendant menu: a React app served to a browser window, so nothing
    inside the *window* to search -- it is driven through the page's own DOM
    instead, and the window is only raised so a person can watch."""

    GAME = "game"
    """The simulator itself, clicked through the active game's own
    ``button_targets`` by :mod:`app.services.game_input`."""


class ReplayStepState(StrEnum):
    """How far one step of the sequence got."""

    PENDING = "pending"
    """Not reached. The sequence is strictly ordered, so every step after a
    failure stays here -- unreached is a different fact from skipped."""

    RUNNING = "running"
    COMPLETED = "completed"

    SKIPPED = "skipped"
    """Deliberately not run: Connect on a DevTool that is already connected."""

    FAILED = "failed"


class ReplayRunState(StrEnum):
    """Where the sequence as a whole has got to."""

    RUNNING = "running"
    """Still going. A run is reported from the moment it starts rather than
    only when it ends: it walks three windows over tens of seconds, so the
    record is worth reading while it is being written."""

    COMPLETED = "completed"
    FAILED = "failed"


class ReplayMoment(StrEnum):
    """Which of a run's two pictures this is.

    A run photographs the replay as *View* left it, spins that replay, and
    photographs it again -- so the pair is one record before and after, and
    which is which has to travel with the picture rather than be inferred from
    its position in a list."""

    BEFORE_SPIN = "before-spin"
    AFTER_SPIN = "after-spin"


class ReplayLogLevel(StrEnum):
    """How much attention one log line deserves."""

    INFO = "info"
    WARNING = "warning"
    """Something worth knowing that did not stop the run -- a foreground change
    Windows refused, a screenshot that had to be retaken."""

    ERROR = "error"


class ReplayWindowState(StrEnum):
    """Whether a window can be driven right now."""

    UNSUPPORTED = "unsupported"
    """Not on Windows, so no window can be reached at all."""

    NOT_FOUND = "not_found"
    """No matching window; the process is probably not running."""

    ACCESS_DENIED = "access_denied"
    """Found, but Windows refuses this process input -- the backend is running
    at a lower integrity level than the cabinet tool it is driving."""

    READY = "ready"


# --- the run's own pieces -------------------------------------------------


class ReplayStep(BaseModel):
    """One click of the sequence, and what happened when it landed."""

    key: str = Field(description="Stable identifier, e.g. 'attendant-key'.")
    label: str = Field(description="How the step reads in the timeline.")
    window: ReplayWindow = Field(description="Which window the step clicks in.")
    state: ReplayStepState = Field(description="How far this step got.")
    target: str | None = Field(
        default=None,
        description=(
            "What was aimed at: a control caption, a measured setting name, or "
            "a game button-target name."
        ),
    )
    screen_x: int | None = Field(
        default=None, description="Screen point clicked; null when none was."
    )
    screen_y: int | None = Field(
        default=None, description="Screen point clicked; null when none was."
    )
    confirmed: bool = Field(
        default=False,
        description=(
            "Whether the step proved its click landed. False is not failure on "
            "a step with nothing observable to wait for -- read `detail`."
        ),
    )
    detail: str | None = Field(
        default=None, description="What the step did or found, in one line."
    )
    started_at: datetime | None = Field(
        default=None, description="Null while the step is still pending."
    )
    finished_at: datetime | None = Field(
        default=None, description="Null until the step leaves 'running'."
    )
    duration_ms: int | None = Field(
        default=None, ge=0, description="How long the step took, once it is over."
    )
    error: str | None = Field(
        default=None, description="Why the step failed, in its own vocabulary."
    )
    error_code: str | None = Field(
        default=None, description="Error code of the failure, for branching on."
    )


class ReplayLogEntry(BaseModel):
    """One line of the run's own commentary, as it happened.

    The steps say *what* the sequence is doing and this says what it found
    while doing it -- a window it had to restore, a record list it waited 40s
    for, a retaken screenshot. Together they are what makes a run that stalls
    diagnosable while it is still stalling, rather than only afterwards from
    the backend's log file, which is on the far side of an elevated window."""

    sequence: int = Field(
        ge=0,
        description=(
            "Position in the run's log, from 0. Monotonic, so a poller can tell "
            "new lines from ones it has already shown without comparing text."
        ),
    )
    at: datetime = Field(description="When the line was written.")
    level: ReplayLogLevel = Field(description="How much attention it deserves.")
    step: str | None = Field(
        default=None,
        description=(
            "Key of the step that wrote it; null for the run's own lines, like "
            "starting and finishing."
        ),
    )
    message: str = Field(description="The line itself, fit to show a user.")


class ReplayScreenshot(BaseModel):
    """One picture the run took of the replayed game.

    Its own block on the run rather than a field on the step that captured it:
    it is one picture of one moment, and it is the thing a reader of a replay
    actually wants -- worth having whether or not the steps after it ran."""

    moment: ReplayMoment = Field(description="Where in the run this was taken.")
    source_name: str = Field(description="OBS source the shot was taken of.")
    file_name: str = Field(description="File OBS wrote, inside its own subdirectory.")
    file_path: str | None = Field(
        default=None, description="Absolute path OBS reported writing to."
    )
    attempts: int = Field(
        default=1, ge=1, description="How many captures it took to get a non-blank one."
    )
    blank: bool = Field(
        default=False,
        description=(
            "Whether the frame came back empty even after retrying. OBS reports "
            "a successful write of a black frame, so this is read back rather "
            "than assumed -- and a blank picture is worse than no picture, "
            "because it looks like a result."
        ),
    )

    # There is deliberately no data URI here. The picture is served as a file
    # from `GET /api/replay/screenshot/{file_name}`, because this record is
    # *polled* while the run walks on -- a 1.8MB base64 copy of a portrait
    # canvas on every poll would cost more than the sequence it is reporting.
    # It also settles which copy is the truth: the file is the one probed for
    # blankness and the one displayed, where two OBS calls can disagree.


class ReplayRun(BaseModel):
    """One whole sequence. Always carries every step, so a failure at step
    four shows the four that ran and the four that were never reached."""

    run_id: str = Field(description="Identifier for this run, for logs.")
    game: str = Field(description="Active game whose Exit target was used.")
    state: ReplayRunState = Field(description="How the sequence ended.")
    message: str = Field(description="One-line summary, fit to show a user.")
    steps: list[ReplayStep] = Field(description="Every step, in order.")
    logs: list[ReplayLogEntry] = Field(
        default_factory=list,
        description=(
            "What the run reported as it went, oldest first, capped at "
            "REPLAY_LOG_LIMIT lines."
        ),
    )
    screenshots: list[ReplayScreenshot] = Field(
        default_factory=list,
        description=(
            "The replayed game, captured through OBS once it was in front: one "
            "before the replay is spun and one after, in the order they were "
            "taken. Each lands here the moment it is taken, so the first is "
            "readable while the spin it precedes is still playing."
        ),
    )
    started_at: datetime = Field(description="When the sequence began.")
    finished_at: datetime | None = Field(
        default=None,
        description="When it stopped, either way; null while it is still running.",
    )
    duration_ms: int = Field(
        ge=0,
        description=("How long the run took, or has taken so far while it is running."),
    )
    error: str | None = Field(
        default=None, description="The failure that stopped the run, if one did."
    )
    error_code: str | None = Field(
        default=None, description="Its error code, for branching on."
    )


# --- status ---------------------------------------------------------------


class ReplayMenuInfo(BaseModel):
    """Whether the attendant menu's page can be driven, and what is on it.

    The menu is a web page rather than a window full of controls, so this is
    the counterpart of :attr:`ReplayWindowInfo.controls`: the labels it is
    showing right now, which is what a step that cannot find its button needs
    to be diagnosed against."""

    reachable: bool = Field(
        description="Whether the browser's debugging port answered."
    )
    probed: bool = Field(
        default=True,
        description=(
            "Whether the page was actually read to answer this. False while a "
            "run is walking it -- reading every label off the DOM once a second "
            "is not worth doing to answer a question the run's own log answers -- "
            "in which case `reachable` only says whether that run has a session "
            "open, and `labels` is empty because nothing re-read them."
        ),
    )
    cdp_url: str = Field(description="DevTools endpoint the menu is driven through.")
    page_url: str | None = Field(
        default=None, description="URL of the page found; null when none was."
    )
    page_title: str | None = Field(default=None, description="Its title.")
    labels: list[str] = Field(
        default_factory=list,
        description="Visible labels on the page, in the order the page lists them.",
    )
    label_count: int = Field(
        default=0, ge=0, description="How many distinct labels were found."
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why the page could not be driven. Not a failure of the request: "
            "the menu is closed for most of the day, and a closed menu has no "
            "page."
        ),
    )


class ReplayWindowInfo(BaseModel):
    """What the service can see of one of the three windows."""

    window: ReplayWindow = Field(description="Which window this describes.")
    state: ReplayWindowState = Field(description="Whether it can be driven.")
    title: str = Field(description="Title searched for.")
    hwnd: int | None = Field(
        default=None, description="Handle of the matched window; null when absent."
    )
    client_width: int = Field(default=0, ge=0, description="Live client width.")
    client_height: int = Field(default=0, ge=0, description="Live client height.")
    controls: list[str] = Field(
        default_factory=list,
        description=(
            "Captions of the controls the window owns, for diagnosing a renamed "
            "button. Empty for a window that draws its own."
        ),
    )


class ReplayStatus(BaseModel):
    """Whether a replay could run right now, and what it would click."""

    running: bool = Field(description="Whether a sequence is in progress.")
    game: str = Field(description="Active game supplying the in-game Exit target.")
    windows: list[ReplayWindowInfo] = Field(
        description="The three windows the sequence drives, in the order it uses them."
    )
    menu: ReplayMenuInfo = Field(
        description="Whether the attendant menu's page can be driven, and its labels."
    )
    run: ReplayRun | None = Field(
        default=None,
        description=(
            "The run in progress, or the last one this process finished; null "
            "until one has been started. This is what makes the sequence "
            "followable: the steps and the log fill in as it walks, and the "
            "screenshot appears on it the moment it is taken rather than when "
            "the run ends."
        ),
    )
    exits_after_screenshot: bool = Field(
        description=(
            "Whether a run will go on to exit the game play view and the menu "
            "after its screenshot. Off by default: the replay is left on screen "
            "to be looked at, and those two steps are then not part of a run at "
            "all -- which is also what makes the in-game Exit target below "
            "irrelevant until it is turned on."
        )
    )
    game_exit_target: str = Field(
        description="Name of the game button-target used to exit the game play view."
    )
    game_exit_configured: bool = Field(
        description="Whether the active game config declares that target."
    )
    game_spin_target: str = Field(
        description=(
            "Name of the game button-target for the replay's own Spin button. "
            "Never pressed by a run -- reported because it is measured in the "
            "same place, off the same frame, and only exists in the same state."
        )
    )
    game_spin_configured: bool = Field(
        description="Whether the active game config declares that target."
    )
