"""Replaying the last game play: one scripted walk from the I/O hub simulator,
through the attendant menu, into the newest game-play record and back out again.

:func:`run` is the whole script, and the only thing a caller needs -- the
dashboard button, a test, or any other service composes it as one call.

**Three windows, clicked three different ways, and that is the point.** The
sequence is not a list of points because only one of the windows needs points:

* **DevTool** is WinForms, so *Connect* and *Attendant Key* are real controls
  with real captions and a real enabled flag. They are found by name through
  :mod:`app.utils.window_ui`, so nothing is measured and a moved or resized
  window still works. It is also what makes step two idempotent: a DevTool that
  is already connected greys *Connect* out and lights *Attendant Key* up, which
  is a state this service can read rather than guess.
* **System Admin** is a React app in a browser window, so it is driven
  through its own **DOM** rather than clicked on screen: the browser the
  platform starts for it has a debugging port, and :mod:`app.utils.cdp` uses
  that to find a button by its id -- or, failing that, by the text on it -- and
  dispatch a click into the page.
  Nothing about it is measured. That also means its clicks need no focus and
  no uncovered window -- and, unlike a click at a point, they can be
  *confirmed*, because the page can be read back afterwards.
* **the game** is clicked through :mod:`app.services.game_input` and the active
  game config's own ``button_targets``, exactly like take-win and gamble. The
  game play view's Exit belongs to a game, not to the platform.

**A run ends with the replay on screen, a picture of it, and the cabinet put
back.** Pressing *View* starts the replay in the game's own window, behind the
attendant menu, so the sequence brings that window to the front and captures it
through OBS -- which is the thing a reader of a replay actually wants -- and
then presses the replay's own *Exit* and the menu's. Both of those are a
*choice* (``REPLAY_EXIT_AFTER_SCREENSHOT``, on): a run that was not asked to
exit leaves the two steps out of its record entirely rather than listing them as
skipped, and stops with the replay still on screen to be looked at by hand.

**The record is readable while it is being written.** :func:`start` walks the
script as a background task and :func:`status` carries the live run, so the
steps fill in as they happen, every step appends a line to ``run.logs``, and the
screenshot lands on the record the moment it is taken rather than when the run
ends. That is not decoration: the sequence takes tens of seconds, most of them
spent waiting on something off-machine -- a menu server fetching game-play
history -- and "which of those is it in" is otherwise only answerable from a log
file inside an elevated console window.

**Most steps prove their own click, and the ones that cannot say so.**
*Connect* enables *Attendant Key*; *Attendant Key* opens the menu's window;
*Events / History* makes the *Game Play* tab appear; *Game Play* lists records
to *View*; and, on a run that exits, the game's Exit brings the menu back and
the menu's Exit sends it away. Each of those is waited for, and a step that
cannot show its effect fails rather than reporting a click it merely sent.

*View* is the honest exception: what that click starts is a replay in the
**game's** window, which the menu's page knows nothing about. So it carries
``confirmed: false``, and the picture two steps later is what shows whether it
worked. Bringing the game forward is the other: Windows can refuse a
foreground change, and a refusal is reported rather than treated as failure,
because the replay is running either way.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.config.game_config import (
    ActiveGameSelectionError,
    GameConfig,
    GameConfigError,
    load_game_config,
)
from app.config.runtime import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    AppException,
    ReplayAccessDeniedError,
    ReplayAlreadyRunningError,
    ReplayControlNotFoundError,
    ReplayMenuUnreachableError,
    ReplayScreenshotNotFoundError,
    ReplayStepNotConfirmedError,
    ReplayWindowNotFoundError,
    ServiceUnavailableError,
)
from app.schemas.obs import ScreenshotRequest
from app.schemas.replay import (
    ReplayLogEntry,
    ReplayLogLevel,
    ReplayMenuInfo,
    ReplayRun,
    ReplayRunState,
    ReplayScreenshot,
    ReplayStatus,
    ReplayStep,
    ReplayStepState,
    ReplayWindow,
    ReplayWindowInfo,
    ReplayWindowState,
)
from app.services import game_input as game_input_service
from app.services import obs as obs_service
from app.services import roi as roi_service
from app.utils import cdp, win32, window_ui
from app.utils.paths import UnsafeNameError, resolve_within

logger = get_logger("replay")

_running: bool = False
"""Whether a sequence is walking right now. Checked and set without awaiting
in between, which is what makes it the whole of the mutual exclusion -- see
:func:`_create`."""

_live: _Run | None = None
"""The run in progress, or the last one this process finished. Published
*before* the walk starts so that the first poll after a start already has a
record to show, and mutated in place as the steps go -- a snapshot of it is
what :func:`status` carries."""

_task: asyncio.Task[ReplayRun] | None = None
"""The background walk, kept only so :func:`reset` can cancel it."""

STEP_OPEN_DEVTOOL = "open-devtool"
STEP_CONNECT = "connect"
STEP_ATTENDANT_KEY = "attendant-key"
STEP_FOCUS_MENU = "focus-menu"
STEP_EVENTS_HISTORY = "events-history"
STEP_GAME_PLAY = "game-play"
STEP_VIEW_LATEST = "view-latest"
STEP_FOCUS_GAME = "focus-game"
STEP_SCREENSHOT = "screenshot"
STEP_EXIT_GAMEPLAY = "exit-gameplay"
STEP_EXIT_ATTENDANT = "exit-attendant"

_STEPS: tuple[tuple[str, str, ReplayWindow], ...] = (
    (STEP_OPEN_DEVTOOL, "Open the DevTool window", ReplayWindow.DEVTOOL),
    (STEP_CONNECT, "Connect the I/O hub", ReplayWindow.DEVTOOL),
    (STEP_ATTENDANT_KEY, "Press the attendant key", ReplayWindow.DEVTOOL),
    (
        STEP_FOCUS_MENU,
        "Bring the attendant menu to the front",
        ReplayWindow.SYSTEM_ADMIN,
    ),
    (STEP_EVENTS_HISTORY, "Open Events / History", ReplayWindow.SYSTEM_ADMIN),
    (STEP_GAME_PLAY, "Open the Game Play tab", ReplayWindow.SYSTEM_ADMIN),
    (STEP_VIEW_LATEST, "View the latest record", ReplayWindow.SYSTEM_ADMIN),
    (STEP_FOCUS_GAME, "Bring the game to the front", ReplayWindow.GAME),
    (STEP_SCREENSHOT, "Screenshot the replayed game", ReplayWindow.GAME),
    (STEP_EXIT_GAMEPLAY, "Exit the game play view", ReplayWindow.GAME),
    (STEP_EXIT_ATTENDANT, "Exit the attendant menu", ReplayWindow.SYSTEM_ADMIN),
)

# The two the sequence only runs when asked to. Left out of a run's `steps`
# entirely rather than shown and skipped -- see REPLAY_EXIT_AFTER_SCREENSHOT.
_EXIT_STEPS = frozenset({STEP_EXIT_GAMEPLAY, STEP_EXIT_ATTENDANT})

# Which label each menu step clicks. The menu is a web page, so a button is
# named rather than measured -- see `_click_menu`.
_MENU_LABELS: tuple[tuple[str, str], ...] = (
    (STEP_EVENTS_HISTORY, "REPLAY_EVENTS_HISTORY_LABEL"),
    (STEP_GAME_PLAY, "REPLAY_GAME_PLAY_LABEL"),
    (STEP_VIEW_LATEST, "REPLAY_VIEW_LABEL"),
    (STEP_EXIT_ATTENDANT, "REPLAY_EXIT_LABEL"),
)

# Restoring a window is asynchronous: the client rect stays 0x0 for a frame or
# two after ShowWindow returns.
_RESTORE_ATTEMPTS = 20


# --- records --------------------------------------------------------------


@dataclass
class _StepRecord:
    """One step's progress. Mutable twin of :class:`ReplayStep`."""

    key: str
    label: str
    window: ReplayWindow
    state: ReplayStepState = ReplayStepState.PENDING
    target: str | None = None
    screen_x: int | None = None
    screen_y: int | None = None
    confirmed: bool = False
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None
    error_code: str | None = None

    def to_schema(self) -> ReplayStep:
        return ReplayStep(
            key=self.key,
            label=self.label,
            window=self.window,
            state=self.state,
            target=self.target,
            screen_x=self.screen_x,
            screen_y=self.screen_y,
            confirmed=self.confirmed,
            detail=self.detail,
            started_at=self.started_at,
            finished_at=self.finished_at,
            duration_ms=self.duration_ms,
            error=self.error,
            error_code=self.error_code,
        )


@dataclass
class _Run:
    """Everything one sequence needs, and nothing anyone else does."""

    run_id: str
    game: str
    started_at: datetime
    steps: dict[str, _StepRecord] = field(default_factory=dict)

    admin_hwnd: int | None = None
    """Handle of the System Admin window once it has been seen, so a later step
    can tell "the same window is still open" from "a new one appeared"."""

    menu: cdp.Session | None = None
    """The open session on the menu's page, opened by the first step that needs
    it and reused by the rest -- four clicks on one page."""

    menu_stack: contextlib.AsyncExitStack | None = None
    """Holds the session's websocket open, and closes it when the run ends.
    Kept beside the session rather than entered per click, because reconnecting
    for each of four clicks would be four chances to lose the page."""

    exits: bool = False
    """Whether this run ends by exiting the game play view and the menu."""

    screenshot: ReplayScreenshot | None = None
    """The replayed game, once it has been captured. Set by its own step, so a
    poller has the picture while the two Exit steps are still running."""

    logs: list[ReplayLogEntry] = field(default_factory=list)
    """What the run has reported so far, oldest first and capped."""

    log_count: int = 0
    """Lines ever written, which is where the next one's ``sequence`` comes
    from -- kept apart from ``len(logs)`` so that dropping the oldest lines
    does not renumber the ones a poller has already shown."""

    menu_page_url: str | None = None
    """URL of the page this run is driving, remembered so that
    :func:`status` can report it without opening a second session on it."""

    finished_at: datetime | None = None
    """Null while the run is still walking. The one thing that says which."""

    failure: AppException | None = None
    """The error that stopped the walk, if one did."""


# --- internals ------------------------------------------------------------


_WARNING = ReplayLogLevel.WARNING
_ERROR = ReplayLogLevel.ERROR

_LOG_METHODS = {
    ReplayLogLevel.INFO: logger.info,
    ReplayLogLevel.WARNING: logger.warning,
    ReplayLogLevel.ERROR: logger.error,
}


def _log(
    run: _Run,
    message: str,
    *,
    step: str | None = None,
    level: ReplayLogLevel = ReplayLogLevel.INFO,
) -> None:
    """Write one line of the run's own commentary.

    Goes to the record *and* to the backend log -- the same line in both, so a
    dashboard and a console are reading one account rather than two. The record
    is the one that matters here: the backend runs elevated, in its own window,
    which is the least convenient place on the machine to read a log while
    something is happening on screen."""
    run.logs.append(
        ReplayLogEntry(
            sequence=run.log_count,
            at=datetime.now(),
            level=level,
            step=step,
            message=message,
        )
    )
    run.log_count += 1
    limit = max(1, settings.REPLAY_LOG_LIMIT)
    if len(run.logs) > limit:
        del run.logs[: len(run.logs) - limit]
    _LOG_METHODS[level]("Replay %s: %s", run.run_id, message)


def _new_steps(*, exits: bool) -> dict[str, _StepRecord]:
    """Every step this run will attempt, pending, in the order they run.

    A run that is not exiting leaves the two Exit steps out altogether rather
    than listing them as skipped: they are not part of what was asked for, and
    `pending` on a finished run has to keep meaning *unreached*."""
    return {
        key: _StepRecord(key=key, label=label, window=window)
        for key, label, window in _STEPS
        if exits or key not in _EXIT_STEPS
    }


def _active_game() -> str:
    """The selected game's name, for the record and the in-game Exit target."""
    try:
        return settings.ideck_active_game
    except ActiveGameSelectionError:
        return ""


def _game_config() -> GameConfig | None:
    """The active game's config, or ``None`` when it cannot be read. Only used
    to report whether the in-game Exit target is declared -- the click itself
    goes through :mod:`app.services.game_input`, which reads its own copy."""
    try:
        return load_game_config(
            settings.ideck_game_config_path_for(settings.ideck_active_game)
        )
    except (ActiveGameSelectionError, GameConfigError):
        return None


def _find_devtool() -> win32.WindowInfo | None:
    return win32.find_window(
        title=settings.REPLAY_DEVTOOL_WINDOW_TITLE,
        class_prefix=settings.REPLAY_DEVTOOL_WINDOW_CLASS_PREFIX or None,
        visible_only=True,
    )


def _find_game(game: str) -> win32.WindowInfo | None:
    """The simulator's own window, found the way :mod:`app.services.game_input`
    finds it -- the setting when one is given, else the game's own name, pinned
    to the Unity window class."""
    return win32.find_window(
        title=settings.GAME_INPUT_WINDOW_TITLE.strip() or game,
        class_name=settings.GAME_INPUT_WINDOW_CLASS,
    )


def _find_admin() -> win32.WindowInfo | None:
    """The attendant menu's window, or ``None`` while it is closed.

    ``visible_only`` is what makes "the menu is gone" answerable: the platform
    hides that window rather than always destroying it, and a handle that
    outlived its window would otherwise read as an open menu."""
    return win32.find_window(
        title=settings.REPLAY_ADMIN_WINDOW_TITLE,
        class_prefix=settings.REPLAY_ADMIN_WINDOW_CLASS_PREFIX or None,
        visible_only=True,
    )


def _access_denied(what: str) -> ReplayAccessDeniedError:
    """The failure that looks like a bug but is really a deployment detail."""
    return ReplayAccessDeniedError(
        f"Windows is blocking input to the {what} window. It belongs to a "
        "process running at a higher integrity level than this backend, and "
        "User Interface Privilege Isolation does not let a normal process "
        "drive an elevated one -- even as the same user. The cabinet tools are "
        "elevated here, so start the backend elevated too (start.ps1 does)."
    )


def _require_windows() -> None:
    if not win32.is_supported():
        raise ServiceUnavailableError(
            "The replay sequence needs the Windows API, which is not available "
            "in this environment"
        )


def _usable(window: win32.WindowInfo, what: str) -> None:
    """Refuse a window this process cannot drive, before anything is clicked.
    A UIPI block otherwise surfaces later as unrelated-looking bugs -- a
    restore that does nothing, a click that vanishes."""
    if not win32.can_post(window.hwnd):
        raise _access_denied(what)


async def _restored(window: win32.WindowInfo, what: str) -> win32.WindowInfo:
    """Un-minimize a window and wait for Windows to give it real geometry.
    A minimized window has no client area to aim a fraction at."""
    if not window.minimized:
        return window
    logger.info("Restoring the minimized %r window", window.title)
    if not win32.restore(window.hwnd):
        raise _access_denied(what)
    for _ in range(_RESTORE_ATTEMPTS):
        current = win32.describe(window.hwnd)
        if current is None:
            break
        if not current.minimized and current.client_width > 0:
            return current
        await asyncio.sleep(settings.REPLAY_POLL_SECONDS)
    raise ReplayWindowNotFoundError(
        f"The {what} window would not come back from being minimized, so it "
        "has no area to click"
    )


async def _await_admin(*, present: bool, timeout: float) -> win32.WindowInfo | None:
    """Watch for the attendant menu opening or closing.

    Returns the window when waiting for it to appear, or ``None`` once it has
    gone; ``None``/the window respectively when the wait ran out, so the
    caller decides whether that is a failure."""
    deadline = time.monotonic() + timeout
    while True:
        window = _find_admin()
        if present and window is not None:
            return window
        if not present and window is None:
            return None
        if time.monotonic() >= deadline:
            # Whichever it is, it is the opposite of what was waited for.
            return window
        await asyncio.sleep(settings.REPLAY_POLL_SECONDS)


async def _await_enabled(
    window: win32.WindowInfo, caption: str, *, timeout: float
) -> win32.ControlInfo | None:
    """Watch for a control to become usable. Re-enumerated every poll rather
    than re-read by handle: a framework is free to recreate a control, and a
    stale handle answers for a window that is no longer on screen."""
    deadline = time.monotonic() + timeout
    while True:
        control = window_ui.find_control(win32.descendants(window.hwnd), caption)
        if control is not None and control.enabled:
            return control
        if time.monotonic() >= deadline:
            return control
        await asyncio.sleep(settings.REPLAY_POLL_SECONDS)


def _control_or_raise(
    window: win32.WindowInfo, caption: str, what: str
) -> win32.ControlInfo:
    """One named control of a window that owns its controls."""
    controls = win32.descendants(window.hwnd)
    control = window_ui.find_control(controls, caption)
    if control is not None:
        return control
    labels = window_ui.control_labels(controls)
    found = ", ".join(labels) if labels else "none"
    raise ReplayControlNotFoundError(
        f"The {what} window has no control captioned {caption!r}. Captions "
        f"found: {found}."
    )


async def _click(
    step: _StepRecord,
    window: win32.WindowInfo,
    point: tuple[int, int],
    *,
    target: str,
    what: str,
) -> None:
    """Click a screen point of a window, recording what was aimed at either way.

    The window is raised and waited for inside ``click_at``: all three of these
    windows normally sit in the background, and injected input lands on
    whatever is topmost rather than at an HWND."""
    step.target = target
    step.screen_x, step.screen_y = point
    try:
        await window_ui.click_at(
            window.hwnd,
            *point,
            hold_seconds=settings.REPLAY_CLICK_HOLD_SECONDS,
            focus_wait_seconds=settings.REPLAY_FOCUS_WAIT_SECONDS,
        )
    except window_ui.WindowNotTopmost as exc:
        raise ReplayWindowNotFoundError(
            f"The window under {target!r} is 0x{exc.found:X}, not the {what} "
            f"window's 0x{exc.expected:X}, so a click there would hit that "
            f"window instead. The {what} window was asked to come forward and "
            f"had {settings.REPLAY_FOCUS_WAIT_SECONDS}s to do it, so something "
            "is holding the foreground -- an always-on-top window, or a dialog."
        ) from exc
    except win32.WindowAccessDenied as exc:
        raise _access_denied(what) from exc


async def _menu(run: _Run) -> cdp.Session:
    """The open session on the attendant menu's page, opened once per run.

    The menu is a web page, so it is driven through its own DOM rather than
    clicked on screen: the button is addressed by the text on it, nothing is
    measured, and nothing needs the window raised or uncovered -- input
    dispatched into a renderer does not care what the desktop is doing. The
    session is kept for the whole run because the menu is clicked four times."""
    if run.menu is not None:
        return run.menu
    stack = contextlib.AsyncExitStack()
    try:
        session = await stack.enter_async_context(
            cdp.open_page(
                settings.REPLAY_ADMIN_CDP_URL,
                url_contains=settings.REPLAY_ADMIN_PAGE_URL_CONTAINS,
                timeout=settings.REPLAY_CDP_TIMEOUT_SECONDS,
            )
        )
    except cdp.CdpError as exc:
        await stack.aclose()
        raise ReplayMenuUnreachableError(str(exc)) from exc
    run.menu = session
    run.menu_stack = stack
    run.menu_page_url = session.page.url
    _log(run, f"driving the menu's page at {session.page.url}")
    return session


async def _await_menu_element(
    run: _Run, label: str, *, timeout: float
) -> tuple[list[cdp.Element], list[str]]:
    """Wait for a labelled element to be on the page and visible.

    This is what replaces waiting for the menu's window to load: "the button
    is there" is a question the page answers, so nothing has to be timed
    against a guess. It is also why a hidden copy is not clicked -- a closed
    drawer or the other tab's panel keeps its buttons in the DOM, and only the
    page can say which of them a user could actually press.

    Returns every visible match in the page's own order, and -- when none
    arrived -- the labels that *were* there, which is the difference between a
    menu showing the wrong page and a button that was renamed."""
    session = await _menu(run)
    deadline = time.monotonic() + timeout
    while True:
        try:
            found = await cdp.elements(session)
        except cdp.CdpError as exc:
            raise ReplayMenuUnreachableError(str(exc)) from exc
        matches = _matching(found, label)
        if matches:
            return matches, []
        if time.monotonic() >= deadline:
            return [], cdp.labels(found)
        await asyncio.sleep(settings.REPLAY_POLL_SECONDS)


def _matching(found: Sequence[cdp.Element], label: str) -> list[cdp.Element]:
    """The visible elements a label names, best interpretation first.

    **An id is preferred to text, and this page is why.** Its nav buttons carry
    ``id="Events / History"`` and ``id="Game Play"`` -- the label *is* the id --
    which is exact, unaffected by restyling, and cannot be confused with the
    Quick Links anchor that reads the same. Only when nothing carries the id is
    the text consulted.

    **The id is matched the way a caption is**, not byte for byte: collapsed
    whitespace, folded case. The menu writes ``id="exit"`` for a button reading
    *Exit*, and an exact comparison missed it and fell through to the text --
    where several elements read *Exit* and the innermost one was not the button
    that closes the menu. An id that differs from its label only in case is
    still the exact answer; insisting on the case is how the better answer gets
    skipped for a worse one.

    Text matches are then narrowed to the innermost: a button and every panel
    wrapped around it share their text, and clicking the wrapper is how a click
    lands on padding. DOM order survives that narrowing, because for a label
    that legitimately repeats -- every row of a record list has a *View* -- the
    order the page lists them in is the only thing that says which is newest."""
    by_id = [
        element
        for element in found
        if element.visible
        and element.element_id
        and window_ui.same_label(element.element_id, label)
    ]
    if by_id:
        return by_id

    by_text = [
        element
        for element in found
        if element.visible
        and element.text
        and window_ui.same_label(element.text, label)
    ]
    return [
        element
        for element in by_text
        if not any(element.contains(other) for other in by_text)
    ]


@dataclass(frozen=True)
class _Clicked:
    """What one DOM click found and hit."""

    element: cdp.Element
    matches: int
    """How many visible elements carried that label. More than one is normal --
    every row of a record list has a *View* -- and the first was clicked."""

    how: str
    """``id`` or ``text``: which of the two ways the label resolved. Worth
    recording, because an id is exact while text is a description, so a step
    that resolved by text is the one to look at first when a click surprises
    someone."""

    @property
    def tag(self) -> str:
        return self.element.tag


def _hit(chain: Sequence[cdp.HitElement], label: str) -> bool:
    """Whether the thing under a point is the thing a label names.

    The whole chain, because what sits under a point is usually a child of
    what was aimed at -- a MUI button's centre is covered by its own ripple
    ``<span>``, which carries neither the id nor the text. A wrapper further
    up matching on *text* cannot happen by accident: the comparison is whole
    labels, and a panel's text is the panel's whole contents."""
    return any(
        (hit.element_id and window_ui.same_label(hit.element_id, label))
        or (hit.text and window_ui.same_label(hit.text, label))
        for hit in chain
    )


async def _aimed(
    run: _Run, step: _StepRecord, label: str, matches: list[cdp.Element]
) -> tuple[cdp.Element, tuple[float, float], list[cdp.Element]]:
    """Wait until the element is really *at* the point its own box claims.

    **This is the DOM's version of `window_ui.click_at`'s topmost wait, and it
    exists for a measured failure.** A rectangle says where an element was when
    the page was read; a dispatched click goes to a *point*, and lands on
    whatever occupies it at that moment. The two part company whenever the page
    moves in between -- and the menu does exactly that at the one place it
    matters: the run leaves the game play view, the attendant menu comes back,
    and its drawer is still sliding in when the page is read. The Exit button's
    box is then several hundred pixels from where it settles, the click goes to
    empty space, and every layer of the design reports success -- the element
    was found, by its id, and the click was dispatched without error. The step
    fails a timeout later saying the menu is still open, which is true and
    tells nobody what went wrong.

    So the page is asked ``elementFromPoint`` before the click, and the element
    is re-read until the two agree. Running the budget out is a **refusal**
    naming what is there instead, never a click sent in hope."""
    session = await _menu(run)
    deadline = time.monotonic() + settings.REPLAY_CONTROL_WAIT_SECONDS
    warned = False
    while True:
        element = matches[0]
        if not element.in_viewport:
            raise ReplayControlNotFoundError(
                f"The attendant menu's {label!r} is on the page but scrolled "
                f"out of view ({len(matches)} found; the first is at "
                f"({round(element.x)}, {round(element.y)}) in a "
                "viewport that does not reach it), so a click at its "
                "coordinates would hit whatever is there instead. Nothing was "
                "clicked."
            )
        point = element.center
        try:
            chain = await cdp.hit_test(session, *point)
        except cdp.CdpError as exc:
            raise ReplayMenuUnreachableError(str(exc)) from exc
        if _hit(chain, label):
            return element, point, matches

        under = chain[0] if chain else None
        if time.monotonic() >= deadline:
            described = (
                f"<{under.tag}>"
                + (f" id={under.element_id!r}" if under.element_id else "")
                + (f" reading {under.text[:60]!r}" if under.text else "")
                if under is not None
                else "nothing at all"
            )
            raise ReplayControlNotFoundError(
                f"The attendant menu's {label!r} says it is at "
                f"({round(point[0])}, {round(point[1])}), but what is there is "
                f"{described} -- so the click would land on that instead. The "
                f"page did not settle within "
                f"{settings.REPLAY_CONTROL_WAIT_SECONDS}s; something on it is "
                "still moving. Nothing was clicked."
            )
        if not warned:
            warned = True
            _log(
                run,
                f"{label!r} is not yet at its own coordinates "
                f"({round(point[0])}, {round(point[1])}) -- the page is still "
                "moving; waiting for it to settle before clicking",
                step=step.key,
                level=_WARNING,
            )
        await asyncio.sleep(settings.REPLAY_POLL_SECONDS)
        try:
            found = await cdp.elements(session)
        except cdp.CdpError as exc:
            raise ReplayMenuUnreachableError(str(exc)) from exc
        current = _matching(found, label)
        if current:
            matches = current


async def _click_menu(
    run: _Run, step: _StepRecord, label: str, *, timeout: float | None = None
) -> _Clicked:
    """Click the menu's button labelled ``label``, waiting for it to exist and
    then for it to be where it says it is.

    The click is dispatched into the page, so -- unlike the two windows this
    sequence clicks on screen -- it needs no focus, no raise, and moves no
    cursor: ``step.screen_x``/``screen_y`` are the element's own position in
    the page, not a point on the desktop. What it *does* need is for the page
    to have stopped moving, which is what `_aimed` waits for."""
    wait = settings.REPLAY_DOM_WAIT_SECONDS if timeout is None else timeout
    step.target = label
    matches, present = await _await_menu_element(run, label, timeout=wait)
    if not matches:
        shown = ", ".join(present) if present else "none"
        raise ReplayControlNotFoundError(
            f"The attendant menu has no visible {label!r} to click after "
            f"{wait}s. Labels on the page: {shown}."
        )

    element, point, matches = await _aimed(run, step, label, matches)
    how = (
        "id"
        if element.element_id and window_ui.same_label(element.element_id, label)
        else "text"
    )
    session = await _menu(run)
    step.screen_x, step.screen_y = round(point[0]), round(point[1])
    try:
        await session.click(*point)
    except cdp.CdpError as exc:
        raise ReplayMenuUnreachableError(str(exc)) from exc
    return _Clicked(element=element, matches=len(matches), how=how)


def _finish_step(step: _StepRecord, state: ReplayStepState) -> None:
    """Close one step off and time it."""
    step.state = state
    step.finished_at = datetime.now()
    if step.started_at is not None:
        step.duration_ms = max(
            0, int((step.finished_at - step.started_at).total_seconds() * 1000)
        )


@contextlib.asynccontextmanager
async def _step(run: _Run, key: str) -> AsyncIterator[_StepRecord]:
    """Run one step of the sequence, recording how it went either way.

    Every step is logged twice -- once on the way in, so a run that stalls says
    what it is stalled *on*, and once on the way out with whatever it found.
    That pair is the whole of the progress reporting; no step has to remember
    to announce itself."""
    step = run.steps[key]
    step.state = ReplayStepState.RUNNING
    step.started_at = datetime.now()
    _log(run, f"{step.label}...", step=key)
    try:
        yield step
    except AppException as exc:
        step.error = exc.message
        step.error_code = exc.error_code
        _finish_step(step, ReplayStepState.FAILED)
        _log(run, f"{step.label} failed: {exc.message}", step=key, level=_ERROR)
        raise
    except Exception as exc:
        step.error = f"{type(exc).__name__}: {exc}"
        _finish_step(step, ReplayStepState.FAILED)
        logger.exception("Replay %s hit an unexpected error at %s", run.run_id, key)
        _log(run, f"{step.label} failed: {step.error}", step=key, level=_ERROR)
        raise
    else:
        if step.state is ReplayStepState.RUNNING:
            # A body that sets `step.error` without raising is recorded failed
            # and the run carries on -- for the step that did its work and
            # knows the result is unusable. Same convention as `analyze_spin`.
            _finish_step(
                step,
                ReplayStepState.FAILED if step.error else ReplayStepState.COMPLETED,
            )
        _log(
            run,
            f"{step.label}: {step.error or step.detail or step.state.value}",
            step=key,
            level=_ERROR if step.error else ReplayLogLevel.INFO,
        )


def _skip(step: _StepRecord, detail: str) -> None:
    """Mark a step deliberately not run, which is not the same as unreached."""
    step.detail = detail
    _finish_step(step, ReplayStepState.SKIPPED)


async def _settle() -> None:
    """Let a clicked UI finish redrawing before the next step reads it."""
    await asyncio.sleep(settings.REPLAY_SETTLE_SECONDS)


async def _close_menu(run: _Run) -> None:
    """Drop the run's session on the menu's page.

    Never raises: the last step of a successful run *closes* that page, so the
    socket being gone by now is the sequence having worked, not a failure to
    report. Losing the session is also never a reason to fail a run -- nothing
    is left running on the cabinet by an unclosed websocket."""
    stack = run.menu_stack
    run.menu = None
    run.menu_stack = None
    if stack is None:
        return
    try:
        await stack.aclose()
    except Exception as exc:  # noqa: BLE001 - clean-up must not mask the run
        logger.debug("Replay %s could not close its page session: %s", run.run_id, exc)


async def _menu_status() -> ReplayMenuInfo:
    """What the attendant menu's page looks like right now, if it is open.

    Never raises: the menu is closed for most of the day, and a closed menu has
    no page to drive -- that is a state to report, exactly as a closed OBS is.
    The labels are the counterpart of a window's control captions, and are what
    a step that could not find its button gets diagnosed against.

    **Not probed while a run is walking**, and that is not caution about
    breaking it -- a page takes more than one debugging client. It is that this
    is polled: reading every label off the DOM once a second, on the machine
    that is driving the cabinet, to answer a question the run's own log is
    already answering. So a live run reports the page it has open and says the
    labels were not re-read."""
    url = settings.REPLAY_ADMIN_CDP_URL
    live = _live
    if _running and live is not None:
        return ReplayMenuInfo(
            reachable=live.menu is not None,
            probed=False,
            cdp_url=url,
            page_url=live.menu_page_url,
        )
    try:
        async with cdp.open_page(
            url,
            url_contains=settings.REPLAY_ADMIN_PAGE_URL_CONTAINS,
            timeout=settings.REPLAY_CDP_TIMEOUT_SECONDS,
        ) as session:
            found = await cdp.elements(session)
            names = cdp.labels(found)
            return ReplayMenuInfo(
                reachable=True,
                cdp_url=url,
                page_url=session.page.url,
                page_title=session.page.title,
                labels=names,
                label_count=len(names),
            )
    except cdp.CdpError as exc:
        return ReplayMenuInfo(reachable=False, cdp_url=url, error=str(exc))
    except (OSError, TimeoutError) as exc:  # pragma: no cover - needs a browser
        return ReplayMenuInfo(reachable=False, cdp_url=url, error=str(exc))


# --- the steps ------------------------------------------------------------


async def _ready_devtool() -> tuple[win32.WindowInfo, bool]:
    """Find DevTool, un-minimize it and bring it to the front.

    **Every step that clicks DevTool starts here, not just the first one.** A
    minimized window is the case that forces it: its controls report rectangles
    at -32000, so a click aimed at one goes nowhere near the button -- and the
    window can be minimized at *any* point in the sequence, not only before it
    starts. Pressing the attendant key also hands the foreground to the menu,
    so by the time anything comes back to DevTool it is behind something again.

    Returns the window as it is *after* being restored and raised, because both
    change where its controls are, together with whether it actually got the
    foreground."""
    title = settings.REPLAY_DEVTOOL_WINDOW_TITLE
    window = _find_devtool()
    if window is None:
        raise ReplayWindowNotFoundError(
            f"No window titled {title!r} is open, so the replay cannot "
            "continue. Launch the I/O hub simulator (DevTool.exe, in the "
            "cabinet deployment's bin/ipc) and try again."
        )
    _usable(window, title)
    window = await _restored(window, title)
    focused = await window_ui.focus_window(
        window.hwnd, wait_seconds=settings.REPLAY_FOCUS_WAIT_SECONDS
    )
    # Re-read after the restore and the raise: the client area and every
    # control rectangle move with them.
    return (win32.describe(window.hwnd) or window), focused


async def _open_devtool(run: _Run) -> None:
    """Make the I/O hub simulator's window ready to be clicked.

    Never launches it: the backend runs elevated so that it can drive the
    cabinet's tools, and a service that starts elevated processes of its own is
    a bigger thing than this sequence needs."""
    async with _step(run, STEP_OPEN_DEVTOOL) as step:
        title = settings.REPLAY_DEVTOOL_WINDOW_TITLE
        window, focused = await _ready_devtool()

        step.target = title
        step.confirmed = True
        step.detail = (
            f"{title} at 0x{window.hwnd:X}, "
            f"{window.client_width}x{window.client_height}"
            + ("" if focused else " (Windows kept the foreground elsewhere)")
        )


async def _connect(run: _Run) -> None:
    """Press *Connect*, and prove it by *Attendant Key* coming alive.

    Idempotent, because the panel says which state it is in: a connected
    DevTool greys *Connect* out and enables *Attendant Key*, so there is
    nothing to press and the step is skipped rather than failed."""
    async with _step(run, STEP_CONNECT) as step:
        devtool, _ = await _ready_devtool()
        caption = settings.REPLAY_CONNECT_LABEL
        attendant_caption = settings.REPLAY_ATTENDANT_KEY_LABEL
        connect = _control_or_raise(devtool, caption, "DevTool")
        attendant = window_ui.find_control(
            win32.descendants(devtool.hwnd), attendant_caption
        )

        if not connect.enabled:
            if attendant is not None and attendant.enabled:
                _skip(
                    step,
                    f"already connected: {caption!r} is greyed out and "
                    f"{attendant_caption!r} is live",
                )
                return
            raise ReplayStepNotConfirmedError(
                f"{caption!r} is greyed out but {attendant_caption!r} is not "
                "enabled either, so the I/O hub is in neither state this step "
                "knows how to act on. Look at the DevTool window."
            )

        await _click(step, devtool, connect.center, target=caption, what="DevTool")
        enabled = await _await_enabled(
            devtool,
            attendant_caption,
            timeout=settings.REPLAY_CONTROL_WAIT_SECONDS,
        )
        if enabled is None or not enabled.enabled:
            raise ReplayStepNotConfirmedError(
                f"Clicked {caption!r} but {attendant_caption!r} never became "
                f"enabled within {settings.REPLAY_CONTROL_WAIT_SECONDS}s, so "
                "the I/O hub did not connect."
            )
        step.confirmed = True
        step.detail = f"{attendant_caption!r} became enabled"


async def _attendant_key(run: _Run) -> None:
    """Press *Attendant Key*, and prove it by the attendant menu opening."""
    async with _step(run, STEP_ATTENDANT_KEY) as step:
        devtool, _ = await _ready_devtool()
        caption = settings.REPLAY_ATTENDANT_KEY_LABEL
        # Re-read rather than reuse what `_connect` saw: it is the click just
        # made that was supposed to enable this control.
        attendant = _control_or_raise(devtool, caption, "DevTool")
        if not attendant.enabled:
            raise ReplayStepNotConfirmedError(
                f"{caption!r} is greyed out, so the I/O hub is not connected "
                "and the attendant key cannot be pressed."
            )

        await _click(step, devtool, attendant.center, target=caption, what="DevTool")
        admin = await _await_admin(
            present=True, timeout=settings.REPLAY_WINDOW_WAIT_SECONDS
        )
        if admin is None:
            raise ReplayStepNotConfirmedError(
                f"Clicked {caption!r} but the "
                f"{settings.REPLAY_ADMIN_WINDOW_TITLE!r} window never opened "
                f"within {settings.REPLAY_WINDOW_WAIT_SECONDS}s."
            )
        run.admin_hwnd = admin.hwnd
        step.confirmed = True
        step.detail = (
            f"the {admin.title!r} window opened at 0x{admin.hwnd:X}; whether "
            "its page has finished loading is the next step's question"
        )


async def _focus_menu(run: _Run) -> None:
    """Bring the attendant menu's window forward.

    **None of the clicks need it**, which is worth being clear about: the menu
    is driven through its page, and input dispatched into a renderer does not
    care what the desktop is doing. Two other things do. A person watching the
    sequence should see the menu it is walking, rather than a DevTool window
    while three invisible clicks happen behind it. And a browser window that is
    minimized or fully covered is one Chromium is entitled to throttle --
    animation frames and timers included -- which is the difference between a
    React route that draws in 200ms and one that draws when someone looks at it.

    Best effort throughout: Windows can refuse a foreground change, and the
    window can be gone entirely, and neither is a reason to fail a run whose
    remaining work is all in the page. A refusal is reported, not raised."""
    async with _step(run, STEP_FOCUS_MENU) as step:
        title = settings.REPLAY_ADMIN_WINDOW_TITLE
        window = await _await_admin(
            present=True, timeout=settings.REPLAY_WINDOW_WAIT_SECONDS
        )
        if window is None:
            step.detail = (
                f"no {title!r} window to raise, so nothing was shown to the "
                "screen; its page is what gets driven, so the walk carries on"
            )
            _log(run, step.detail, step=STEP_FOCUS_MENU, level=_WARNING)
            return

        run.admin_hwnd = window.hwnd
        step.target = window.title or title
        if window.minimized:
            # Unlike DevTool, nothing here is aimed at a control rectangle, so
            # a restore Windows refuses costs the view and not the run.
            _log(run, f"restoring the minimized {title!r} window", step=STEP_FOCUS_MENU)
            win32.restore(window.hwnd)
            window = win32.describe(window.hwnd) or window

        focused = await window_ui.focus_window(
            window.hwnd, wait_seconds=settings.REPLAY_FOCUS_WAIT_SECONDS
        )
        step.confirmed = focused
        step.detail = (
            f"{window.title or title!r} is in front at 0x{window.hwnd:X}"
            if focused
            else (
                f"asked for {window.title or title!r} but Windows kept the "
                f"foreground elsewhere after "
                f"{settings.REPLAY_FOCUS_WAIT_SECONDS}s; the page is driven "
                "through its own DOM, so the walk carries on regardless"
            )
        )
        # The route this sequence is about to change repaints on activation.
        await _settle()


async def _events_history(run: _Run) -> None:
    """Open *Events / History*.

    Confirmed by what the click was *for*: the Game Play tab that the next step
    needs appearing on the page. That confirmation is available only because
    the menu is a web page -- a measured click into a window could report that
    it had been sent, and nothing more."""
    async with _step(run, STEP_EVENTS_HISTORY) as step:
        label = settings.REPLAY_EVENTS_HISTORY_LABEL
        wanted = settings.REPLAY_GAME_PLAY_LABEL
        clicked = await _click_menu(run, step, label)
        step.detail = f"clicked {label!r}, found by {clicked.how} ({clicked.tag})"

        arrived, present = await _await_menu_element(
            run, wanted, timeout=settings.REPLAY_DOM_WAIT_SECONDS
        )
        if not arrived:
            shown = ", ".join(present) if present else "none"
            raise ReplayStepNotConfirmedError(
                f"Clicked {label!r} but {wanted!r} never appeared on the page "
                f"within {settings.REPLAY_DOM_WAIT_SECONDS}s, so that click did "
                f"not open the events page. Labels on the page: {shown}."
            )
        step.confirmed = True
        step.detail = f"{step.detail}; {wanted!r} appeared"


async def _game_play(run: _Run) -> None:
    """Open the *Game Play* tab and wait for the records to arrive.

    **This is the long wait of the whole sequence, and it is a wait for
    something.** Opening that tab sends the menu's own server off to fetch
    game-play history, so what follows is a round-trip rather than a page
    drawing itself -- hence ``REPLAY_RECORDS_WAIT_SECONDS`` rather than the
    DOM timeout the other steps use. The page is *asked* whether a row has
    appeared, so a fast fetch costs one poll and a slow one costs only what it
    takes; the alternative -- sleeping for the worst case -- would pay the
    worst case every time and still click into an empty table when the fetch
    took longer.

    A row is recognised by its own *View* button, which is the thing the next
    step needs, so what is waited for and what is then used are the same
    object rather than two guesses about loading."""
    async with _step(run, STEP_GAME_PLAY) as step:
        label = settings.REPLAY_GAME_PLAY_LABEL
        wanted = settings.REPLAY_VIEW_LABEL
        wait = settings.REPLAY_RECORDS_WAIT_SECONDS
        clicked = await _click_menu(run, step, label)
        step.detail = f"clicked {label!r}, found by {clicked.how} ({clicked.tag})"
        _log(
            run,
            f"waiting up to {wait:g}s for the server to return the game-play "
            f"records ({wanted!r} on a row is what says they arrived)",
            step=STEP_GAME_PLAY,
        )

        arrived, present = await _await_menu_element(run, wanted, timeout=wait)
        if not arrived:
            shown = ", ".join(present) if present else "none"
            raise ReplayStepNotConfirmedError(
                f"Clicked {label!r} but no {wanted!r} appeared within "
                f"{wait}s, so the tab listed no records to view -- either the "
                "menu's server did not answer in time, or this cabinet has no "
                f"game-play history to replay. Labels on the page: {shown}."
            )
        step.confirmed = True
        step.detail = (
            f"{step.detail}; the record list came up with {len(arrived)} "
            f"{wanted!r} button{'' if len(arrived) == 1 else 's'} on it"
        )


async def _view_latest(run: _Run) -> None:
    """Press *View* on the newest record.

    **The newest record is the first one.** Every row carries the same label,
    so this takes the first visible match in the page's own order, which is the
    order the list is drawn in. How many rows were found travels on the step,
    so a list that ever sorts the other way is visible in the record rather
    than silently replaying the wrong end of it."""
    async with _step(run, STEP_VIEW_LATEST) as step:
        label = settings.REPLAY_VIEW_LABEL
        clicked = await _click_menu(run, step, label)
        step.detail = (
            f"clicked the first of {clicked.matches} {label!r} on the page "
            f"(found by {clicked.how}), which is the newest record"
        )
        # Nothing on this page says the replay started: that happens in the
        # game's own window, so the next step's Exit is what confirms it.
        await _settle()


async def _focus_game(run: _Run) -> None:
    """Bring the game's window to the front, so the replay is what is on screen.

    Two reasons this is a step of its own rather than something the screenshot
    does on the way past. It is what makes the replay *visible* -- pressing
    *View* starts it in a window that is behind the attendant menu, and a
    person watching should see it. And OBS captures that window: a capture of
    an occluded window is a real risk on some capture paths, so putting it in
    front first is what makes the picture worth taking.

    Windows can refuse a foreground change, so the result is reported rather
    than assumed -- and a refusal does *not* fail the step, because the replay
    is running either way and the screenshot is still worth attempting."""
    async with _step(run, STEP_FOCUS_GAME) as step:
        window = _find_game(run.game)
        if window is None:
            raise ReplayWindowNotFoundError(
                f"No window titled {run.game!r} of class "
                f"{settings.GAME_INPUT_WINDOW_CLASS!r} is open, so there is no "
                "game to bring forward. Is the simulator still running?"
            )
        _usable(window, run.game or "the game")
        window = await _restored(window, run.game or "the game")
        step.target = window.title

        focused = await window_ui.focus_window(
            window.hwnd, wait_seconds=settings.REPLAY_FOCUS_WAIT_SECONDS
        )
        step.confirmed = focused
        step.detail = (
            f"{window.title!r} is in front at 0x{window.hwnd:X}, "
            f"{window.client_width}x{window.client_height}"
            if focused
            else (
                f"asked for {window.title!r} but Windows kept the foreground "
                f"elsewhere after {settings.REPLAY_FOCUS_WAIT_SECONDS}s; the "
                "replay is running regardless, so the screenshot is still taken"
            )
        )
        # The window repaints on activation, and OBS captures what it draws.
        await _settle()


async def _screenshot(run: _Run) -> None:
    """Capture the replayed game through OBS, and put it on the run.

    **One copy, probed and served.** OBS answers ``GetSourceScreenshot`` (a
    data URI) and ``SaveSourceScreenshot`` (a file) with two separate calls,
    and measured on this cabinet they disagree: the first inline copy after a
    connect or a source re-point comes back entirely black while the file
    written a moment later holds the real picture. Checking one and showing
    the other is how a card ends up displaying a black rectangle over a step
    that passed -- so the **file** is what is probed and the file is what
    ``GET /api/replay/screenshot/{file_name}`` serves. There is deliberately no
    data URI on the record: it is polled while the run walks on, and a 1.8MB
    base64 copy of a portrait canvas per poll costs more than the sequence it
    is reporting.

    **Read back, not trusted.** The file is black on the first attempt often
    enough to matter, so it is re-shot -- and a picture that stays empty is
    marked ``blank`` rather than presented as a result. That fails the step (a
    black picture of a replay is worse than none, because it looks like one)
    but not the run: it is set without raising, so the two Exit steps still
    put the cabinet back."""
    async with _step(run, STEP_SCREENSHOT) as step:
        # Idempotent: a live session is reused. Done here rather than at the
        # start of the run so that nothing about OBS is touched by a sequence
        # that never gets this far.
        await obs_service.connect()

        # Point the window capture at the game before capturing it. Without
        # this the shot is of whatever the source was last aimed at, which
        # comes back a plausible-looking picture of the wrong thing.
        pointed: str | None = None
        try:
            selection = await obs_service.select_current_game_window()
            pointed = selection.source_name
        except AppException as exc:
            # A scene with no window-capture source can still be screenshotted;
            # it is the *aim* that is unverified, so this is noted, not fatal.
            _log(
                run,
                f"could not re-point the OBS window capture: {exc.message}",
                step=STEP_SCREENSHOT,
                level=_WARNING,
            )
        else:
            _log(
                run,
                f"pointed the OBS window capture {pointed!r} at "
                f"{selection.window_title!r}",
                step=STEP_SCREENSHOT,
            )
        # OBS renders nothing for a moment after a source is re-pointed.
        await _settle()

        attempts = 0
        result = None
        blank = True
        path: Path | None = None
        while True:
            attempts += 1
            result = await obs_service.take_screenshot(
                ScreenshotRequest(
                    image_format="png",
                    width=settings.REPLAY_SCREENSHOT_WIDTH,
                    file_name=f"replay-{run.run_id}-{attempts}",
                    output_dir=settings.REPLAY_SCREENSHOT_SUBDIR,
                )
            )
            if result.file_path is None:
                # Nothing to probe and nothing to serve. OBS was asked for a
                # file, so this is a refusal rather than a black frame.
                path = None
                break
            path = Path(result.file_path)
            blank = await asyncio.to_thread(roi_service.is_blank, path)
            if not blank or attempts > settings.REPLAY_SCREENSHOT_BLANK_RETRIES:
                break
            _log(
                run,
                f"{path.name} came back empty; retaking it",
                step=STEP_SCREENSHOT,
                level=_WARNING,
            )
            await _settle()

        step.target = result.source_name
        if path is None:
            raise ReplayScreenshotNotFoundError(
                f"OBS captured {result.source_name!r} but reported writing no "
                "file, so there is no picture of the replay to show. Check the "
                "screenshot directory it was asked to write into."
            )

        run.screenshot = ReplayScreenshot(
            source_name=result.source_name,
            file_name=path.name,
            file_path=str(path),
            attempts=attempts,
            blank=blank,
        )
        step.detail = (
            f"captured {result.source_name!r} to {path.name}"
            + (f", window capture re-pointed via {pointed!r}" if pointed else "")
            + (f", after {attempts} attempts" if attempts > 1 else "")
        )
        if blank:
            # Recorded as failed without raising: the picture is on the record
            # either way, marked for what it is.
            step.error = (
                "OBS wrote the screenshot but it is empty, so it shows nothing "
                f"of the replay (after {attempts} attempts). Check that the OBS "
                "window-capture source is pointed at the game."
            )
        else:
            step.confirmed = True


async def _exit_gameplay(run: _Run) -> None:
    """Exit the game play view, in the game's own window.

    Goes through :mod:`app.services.game_input` so the point comes from the
    active game config, like every other in-game click. **That target only
    exists while a replay is on screen** -- Spin, Previous and Exit are the
    replay's own controls, drawn over the game -- so it had to be measured off
    a frame captured during one, and it sits 22 pixels from *Previous*, which
    replays the record before the one that was asked for.

    Deliberately unverified in `game_input`: that service retries an
    unconfirmed click with the window focused, and a second Exit would land on
    whatever replaced the first one. The proof used instead is stronger and
    belongs to this sequence -- the attendant menu coming back."""
    async with _step(run, STEP_EXIT_GAMEPLAY) as step:
        target = settings.REPLAY_GAME_EXIT_TARGET
        step.target = target
        result = await game_input_service.click(target, verify=False)
        step.detail = (
            f"clicked {target!r} on {result.game} at "
            f"({result.client_x}, {result.client_y})"
        )

        admin = await _await_admin(
            present=True, timeout=settings.REPLAY_WINDOW_WAIT_SECONDS
        )
        if admin is None:
            raise ReplayStepNotConfirmedError(
                f"Clicked {target!r} but the "
                f"{settings.REPLAY_ADMIN_WINDOW_TITLE!r} window never came "
                f"back within {settings.REPLAY_WINDOW_WAIT_SECONDS}s, so the "
                "game play view did not exit. The target may need "
                "re-measuring against a current screenshot."
            )
        run.admin_hwnd = admin.hwnd
        step.confirmed = True
        step.detail = f"{step.detail}; the attendant menu came back"
        # It comes back *drawing itself*: the window is there before the page
        # inside it has settled. The next step aims at a point on that page.
        await _settle()


async def _exit_attendant(run: _Run) -> None:
    """Exit the attendant menu, which is the sequence's own clean-up: a run
    that stopped here would leave the cabinet sitting in the menu."""
    async with _step(run, STEP_EXIT_ATTENDANT) as step:
        label = settings.REPLAY_EXIT_LABEL
        clicked = await _click_menu(run, step, label)
        remaining = await _await_admin(
            present=False, timeout=settings.REPLAY_WINDOW_WAIT_SECONDS
        )
        if remaining is not None:
            raise ReplayStepNotConfirmedError(
                f"Clicked {label!r} -- a <{clicked.tag}> found by {clicked.how}, "
                f"one of {clicked.matches} on the page -- but the attendant menu "
                f"is still open after {settings.REPLAY_WINDOW_WAIT_SECONDS}s, so "
                "that was not the button that closes it. Resolving by *text* is "
                "the suspect half: several things on this page read 'Exit', and "
                "only the one carrying the id is the button. Check that "
                "REPLAY_EXIT_LABEL matches that id."
            )
        step.confirmed = True
        step.detail = (
            f"clicked {label!r}, found by {clicked.how}; the attendant menu closed"
        )


# --- public API -----------------------------------------------------------


def _create() -> _Run:
    """Publish a new run, or refuse because one is already walking.

    **The exclusion is this function and nothing else.** The flag is read and
    set with no ``await`` between the two, so two requests arriving together
    cannot both get past it -- which is also why there is no lock: a lock would
    make the loser *wait* for the cursor and the foreground window, and by the
    time it got them it would be replaying a different record than the one its
    caller asked about. Refusing is the honest answer.

    The run is published before it starts walking, so the first poll after a
    start already has a record to show rather than an empty one."""
    global _running, _live
    if _running:
        raise ReplayAlreadyRunningError(
            "A replay sequence is already in progress. It moves the cursor and "
            "the foreground window, so a second one would click into the first "
            "one's windows."
        )
    exits = settings.REPLAY_EXIT_AFTER_SCREENSHOT
    active = _Run(
        run_id=uuid.uuid4().hex[:12],
        game=_active_game(),
        started_at=datetime.now(),
        steps=_new_steps(exits=exits),
        exits=exits,
    )
    _running = True
    _live = active
    _log(
        active,
        f"starting: {len(active.steps)} steps on "
        f"{active.game or 'no active game'}"
        + ("" if exits else ", leaving the replay on screen at the end"),
    )
    return active


def _snapshot(run: _Run) -> ReplayRun:
    """The run as it stands, mid-walk or finished.

    Safe to take at any moment because it only *reads*: there is no await in
    here, so a poll cannot land between two halves of a step's bookkeeping.

    The verdict comes from the *steps* rather than from whether something
    raised, because a step is allowed to record a failure and let the run carry
    on -- a blank screenshot does exactly that."""
    steps = [run.steps[key].to_schema() for key, _, _ in _STEPS if key in run.steps]
    ran = [step for step in steps if step.state is not ReplayStepState.PENDING]
    confirmed = sum(1 for step in steps if step.confirmed)
    failed = next(
        (step for step in steps if step.state is ReplayStepState.FAILED), None
    )
    running = next(
        (step for step in steps if step.state is ReplayStepState.RUNNING), None
    )

    finished = run.finished_at
    if finished is None:
        state = ReplayRunState.RUNNING
        message = (
            f"{running.label} ({len(ran)} of {len(steps)})"
            if running is not None
            else f"Starting the replay: {len(steps)} steps"
        )
    elif failed is None:
        state = ReplayRunState.COMPLETED
        message = (
            f"Replayed the latest game play: {len(ran)} steps, {confirmed} confirmed"
        )
    else:
        state = ReplayRunState.FAILED
        message = (
            f"Replay stopped at {failed.label.lower()}: {run.failure.message}"
            if run.failure is not None
            else f"Replay finished, but {failed.label.lower()} failed"
        )

    until = finished if finished is not None else datetime.now()
    return ReplayRun(
        run_id=run.run_id,
        game=run.game,
        state=state,
        message=message,
        steps=steps,
        logs=list(run.logs),
        screenshot=run.screenshot,
        started_at=run.started_at,
        finished_at=finished,
        duration_ms=max(0, int((until - run.started_at).total_seconds() * 1000)),
        error=run.failure.message
        if run.failure is not None
        else (failed.error if failed else None),
        error_code=(
            run.failure.error_code
            if run.failure is not None
            else (failed.error_code if failed else None)
        ),
    )


def _close(run: _Run) -> None:
    """Stamp a run finished, once. Idempotent because a cancelled walk closes
    itself on the way out and a finished one closes at the end."""
    if run.finished_at is not None:
        return
    run.finished_at = datetime.now()
    elapsed = (run.finished_at - run.started_at).total_seconds()
    _log(run, f"finished in {elapsed:.1f}s")


async def _walk(active: _Run) -> ReplayRun:
    """The sequence itself. Never raises for a step that failed -- the record of
    how far it got *is* the answer, and it is already on ``active``."""
    global _running
    try:
        await _open_devtool(active)
        await _connect(active)
        await _attendant_key(active)
        await _focus_menu(active)
        await _events_history(active)
        await _game_play(active)
        await _view_latest(active)
        await _focus_game(active)
        await _screenshot(active)
        if active.exits:
            await _exit_gameplay(active)
            await _exit_attendant(active)
    except AppException as exc:
        active.failure = exc
    except Exception as exc:
        # `_step` has already recorded it against the step that raised; this is
        # only here so that a background walk cannot die with an unretrieved
        # exception, leaving a run that says `running` forever.
        logger.exception("Replay %s crashed", active.run_id)
        _log(active, f"crashed: {type(exc).__name__}: {exc}", level=_ERROR)
    finally:
        await _close_menu(active)
        _close(active)
        _running = False
    return _snapshot(active)


async def run() -> ReplayRun:
    """Replay the latest game play, end to end. **This is the whole script.**

    Opens the I/O hub simulator, connects it, presses the attendant key, brings
    the attendant menu forward, walks it to the newest game-play record, views
    it, brings the game forward, photographs the replay and -- unless
    ``REPLAY_EXIT_AFTER_SCREENSHOT`` is off -- exits the replay and the menu.

    Awaits the whole sequence and returns the finished record, so this is the
    one call to compose from anywhere: a service, a script, a test. Use
    :func:`start` instead to walk it in the background and follow it through
    :func:`status`.

    Always returns a record rather than raising for a step that failed: the
    caller wants to see how far it got, and the envelope carries no data on a
    failure. Read ``state``; the first failed step carries the error that
    stopped it. The only exceptions are refusals to start at all -- no Windows
    API (503), or a sequence already running (409).
    """
    _require_windows()
    return await _walk(_create())


async def start() -> ReplayRun:
    """Start the script in the background, and report where it begins.

    Exactly :func:`run` on a task, and it exists for one reason: the sequence
    takes tens of seconds, most of them waiting on a menu server or a window
    that has to come forward, so a caller that held a request open for the
    whole thing could only ever show the result. This returns the run at step
    one -- every step listed and pending -- and :func:`status` carries the same
    record filling in, screenshot included, as it walks.
    """
    _require_windows()
    global _task
    active = _create()
    _task = asyncio.create_task(_walk(active), name=f"replay-{active.run_id}")
    return _snapshot(active)


def _window_info(
    which: ReplayWindow, title: str, window: win32.WindowInfo | None
) -> ReplayWindowInfo:
    """One window's state, with its captions when it owns any."""
    if not win32.is_supported():
        return ReplayWindowInfo(
            window=which, state=ReplayWindowState.UNSUPPORTED, title=title
        )
    if window is None:
        return ReplayWindowInfo(
            window=which, state=ReplayWindowState.NOT_FOUND, title=title
        )
    state = (
        ReplayWindowState.READY
        if win32.can_post(window.hwnd)
        else ReplayWindowState.ACCESS_DENIED
    )
    return ReplayWindowInfo(
        window=which,
        state=state,
        title=window.title or title,
        hwnd=window.hwnd,
        client_width=window.client_width,
        client_height=window.client_height,
        controls=window_ui.control_labels(win32.descendants(window.hwnd)),
    )


async def status() -> ReplayStatus:
    """What a replay would find if it ran now. Never raises -- a closed window
    or an unmeasured target is a state to report, not a failure.

    The captions are part of the answer on purpose: a step that cannot find
    *Attendant Key* is either a window that is not there or a button that was
    renamed, and only the list says which."""
    game = _active_game()
    windows: list[ReplayWindowInfo] = []
    if win32.is_supported():
        windows.append(
            _window_info(
                ReplayWindow.DEVTOOL,
                settings.REPLAY_DEVTOOL_WINDOW_TITLE,
                _find_devtool(),
            )
        )
        windows.append(
            _window_info(
                ReplayWindow.SYSTEM_ADMIN,
                settings.REPLAY_ADMIN_WINDOW_TITLE,
                _find_admin(),
            )
        )
        game_window = win32.find_window(
            title=settings.GAME_INPUT_WINDOW_TITLE.strip() or game,
            class_name=settings.GAME_INPUT_WINDOW_CLASS,
        )
        windows.append(_window_info(ReplayWindow.GAME, game or "the game", game_window))
    else:
        windows = [
            _window_info(which, "", None)
            for which in (
                ReplayWindow.DEVTOOL,
                ReplayWindow.SYSTEM_ADMIN,
                ReplayWindow.GAME,
            )
        ]

    config = _game_config()
    exit_target = settings.REPLAY_GAME_EXIT_TARGET
    spin_target = settings.REPLAY_GAME_SPIN_TARGET
    live = _live

    return ReplayStatus(
        running=_running,
        game=game,
        windows=windows,
        menu=await _menu_status(),
        run=_snapshot(live) if live is not None else None,
        exits_after_screenshot=settings.REPLAY_EXIT_AFTER_SCREENSHOT,
        game_exit_target=exit_target,
        game_exit_configured=_declares(config, exit_target),
        game_spin_target=spin_target,
        game_spin_configured=_declares(config, spin_target),
    )


def _declares(config: GameConfig | None, target: str) -> bool:
    """Whether the active game measured a named button."""
    return config is not None and any(
        name.casefold() == target.casefold() for name in config.button_targets
    )


def screenshot_dir() -> Path:
    """Where this service's screenshots live: its own subdirectory of the
    capture root, deliberately not the dashboard's. That one is "the latest
    screenshot" that ROI, grid and the classifier read, and a replayed spin is
    not a frame of the live game."""
    return settings.obs_screenshot_dir / settings.REPLAY_SCREENSHOT_SUBDIR


def screenshot_path(file_name: str) -> Path:
    """Resolve one replay screenshot for serving.

    The name comes off a URL, so it goes through the path guards -- a filename
    joined to a capture directory is still a perfectly good path to somewhere
    else."""
    try:
        target = resolve_within(screenshot_dir(), file_name, default_suffix="png")
    except UnsafeNameError as exc:
        raise ReplayScreenshotNotFoundError(
            f"Invalid screenshot name: {exc.reason}"
        ) from exc
    if not target.is_file():
        raise ReplayScreenshotNotFoundError(
            f"No replay screenshot named {file_name!r} is in {screenshot_dir()}"
        )
    return target


async def reset() -> None:
    """Drop the running flag and the record, and cancel a walk in progress.

    Async, and awaits the cancellation, for the reason `event_capture` and
    `analyze_spin` are: a task cancelled but never awaited is a pending-task
    warning, and ``filterwarnings = error`` makes that a failed test. Touches
    no window -- a half-walked sequence leaves the cabinet wherever it got to,
    which is a thing for a person to look at rather than for clean-up to
    guess at."""
    global _running, _live, _task
    task = _task
    _task = None
    _running = False
    _live = None
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
