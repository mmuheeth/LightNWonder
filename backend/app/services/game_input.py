"""Clicking the game's own window.

Take-win and gamble are not on the button deck. The i-deck layout has fourteen
keys -- Service, Line1-5, Rebet, Collect, Hold1-5, Maxbet -- and neither of those
is among them; the games' logs settle it, because every gamble decision in them
arrives as a ``TouchMsg`` from the glass and never as an OLED button. So reaching
them means clicking the simulator's Unity window, which is what this module does.

**There is no API to ask instead, and not for want of looking.** Unity implements
neither UI Automation nor Microsoft Active Accessibility, so the window exposes
no tree of controls to address by name. The game does ship a proper automation
service -- GDK's GAF, a Thrift server with a literal ``SimulateTouch(gameObject)``
and a ``GetSelectableObjects`` that enumerates whatever is touchable right now --
and the handlers are present in the game's own ``Assembly-CSharp.dll``. It is not
started in a normal simulator launch, though: nothing listens on the ports its
client defaults to. If it is ever switched on it belongs here as a second way to
deliver a click, and everything below the public API would be what changes.

Two things make clicking a coordinate reliable rather than hopeful:

**Geometry is not hardcoded here.** Aim points live in the active game's
``button_targets`` block as fractions of the window, read by
:mod:`app.utils.click_target` -- the same fractions-of-the-frame convention
:mod:`app.utils.image_roi` crops regions with. A resized simulator needs no
re-measurement, and a new game is a new JSON file.

**Clicks are proven, not assumed.** This is what earns the coordinate its keep. A
click that misses is silent -- no error, no exception, just nothing -- so each one
captures the game log's size beforehand and then reads only what was appended.
The strong proof is the game naming the button it hit: a touch on the gamble
button publishes ``double_up_offer_accept`` before anything else happens, and
:mod:`app.utils.game_log` already matches that in both shapes the games write it.
Where a target names no such event, the fallback is
:data:`app.utils.game_log.TOUCH_REGISTERED` -- weaker, since it cannot say *which*
button was hit, but still enough to rule out the failures that really occur.

The two are reported apart rather than blurred, so a caller is never told a
coordinate was right when all that was proven is that input arrived.

State is module-level, like the other services here, and callers use the
namespace rather than the functions::

    from app.services import game_input as game_input_service
    await game_input_service.click("gamble")
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.config.game_config import (
    ActiveGameSelectionError,
    GameConfig,
    GameConfigError,
    load_game_config,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    AppException,
    GameClickNotConfirmedError,
    GameInputAccessDeniedError,
    GameInputConfigError,
    GameTargetNotFoundError,
    GameWindowNotFoundError,
    ServiceUnavailableError,
)
from app.schemas.game_input import (
    ClickConfirmation,
    ClickResult,
    ClickTargetInfo,
    GameInputStatus,
    GameWindowState,
)
from app.utils import game_log, win32
from app.utils.click_target import ClickTarget, ClickTargetError, named_target
from app.utils.log_tail import LogTail

logger = get_logger("game_input")

_game: GameConfig | None = None
_lock: asyncio.Lock | None = None

# Restoring a window is asynchronous: the client rect stays 0x0 for a frame or
# two after ShowWindow returns. These bound the wait for real geometry.
_RESTORE_ATTEMPTS = 20
_POLL_SECONDS = 0.05


# --- internals ------------------------------------------------------------


def _get_lock() -> asyncio.Lock:
    """Return the module lock, created inside whichever loop is running.

    Built lazily rather than at import time because ``tests/conftest.py`` makes a
    fresh event loop per test, and a lock holding waiters from a dead loop is a
    hazard. :func:`reset` clears it, so each test gets its own.
    """
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _game_or_raise() -> GameConfig:
    """Load the selected game's config once and keep it.

    Raises:
        GameInputConfigError: if the config cannot be read or is malformed.
    """
    global _game
    if _game is None:
        try:
            active_game = settings.ideck_active_game
            _game = load_game_config(settings.ideck_game_config_path_for(active_game))
        except ActiveGameSelectionError as exc:
            raise GameInputConfigError(str(exc)) from exc
        except GameConfigError as exc:
            raise GameInputConfigError(
                f"Config for game {active_game!r}: {exc}"
            ) from exc
    return _game


def _targets() -> Mapping[str, Any]:
    """The active game's ``button_targets`` block.

    Empty when the game declares none, which is a real state: the window can
    still be described, there is simply nothing configured to click in it.

    Raises:
        GameInputConfigError: if the game config cannot be read or is malformed.
    """
    return _game_or_raise().button_targets


def _window_title() -> str:
    """Title to search for: the setting when given, else the game's own name.

    The simulator titles its window after the game, so the game config already
    holds the answer and restating it in the environment would be a second place
    to keep correct. The setting exists for a deployment where it is not true.

    Raises:
        GameInputConfigError: if the game config cannot be read or is malformed.
    """
    configured = settings.GAME_INPUT_WINDOW_TITLE.strip()
    return configured or _game_or_raise().name


def _log_path() -> Path | None:
    """The active game's log, or ``None`` when its config names none."""
    return _game_or_raise().log_path


def _log() -> LogTail | None:
    """A cursor over the active game's log, or ``None`` when it names none.

    Built per call rather than cached: the path follows the active game, and the
    tests move it between cases.
    """
    path = _log_path()
    return None if path is None else LogTail(path, poll_seconds=_POLL_SECONDS)


def _resolve_target(name: str) -> ClickTarget:
    """Map a caller-supplied name onto an aim point in the active game.

    Raises:
        GameTargetNotFoundError: if the game configures no target by that name.
        GameInputConfigError: if the target is configured but malformed.
    """
    targets = _targets()
    try:
        return named_target(targets, name)
    except ClickTargetError as exc:
        # A name that is simply absent is the caller's mistake; a name that is
        # present but unreadable is the config's, and they are different errors.
        if name.strip().casefold() in {found.casefold() for found in targets}:
            raise GameInputConfigError(str(exc)) from exc
        raise GameTargetNotFoundError(str(exc)) from exc


def _confirm_pattern(target: ClickTarget) -> re.Pattern[str] | None:
    """The log pattern that proves a click on ``target`` hit the right button.

    ``None`` when the target names no event, or names one this game does not
    recognise -- the caller falls back to a generic touch either way.

    Resolved through :func:`app.utils.game_log.resolve_rules` rather than against
    the shipped rules directly, so a game that overrides ``gamble-accepted`` in
    its own config gets its own pattern here too.
    """
    if target.confirm is None:
        return None
    game = _game_or_raise()
    rules = game_log.resolve_rules(
        extra=game.event_rules, disabled=game.disabled_events
    )
    wanted = target.confirm.casefold()
    rule = next((one for one in rules if one.event.casefold() == wanted), None)
    if rule is None:
        logger.warning(
            "Target confirmation event %r is not a recognised event for %s; "
            "falling back to a generic touch",
            target.confirm,
            game.name,
        )
        return None
    return rule.pattern


def _client_point(target: ClickTarget, window: win32.WindowInfo) -> tuple[int, int]:
    """Where a target sits in the live window.

    Raises:
        GameInputConfigError: if the window has no area to aim at. Callers check
            that first, so this is the belt to that braces.
    """
    try:
        return target.to_point(window.client_width, window.client_height)
    except ClickTargetError as exc:
        raise GameInputConfigError(str(exc)) from exc


def _access_denied() -> GameInputAccessDeniedError:
    """The one failure that looks like a bug but is really a deployment detail."""
    return GameInputAccessDeniedError(
        f"Windows is blocking input to the {_window_title()!r} window. It belongs "
        "to a process running at a higher integrity level than this backend, and "
        "User Interface Privilege Isolation does not let a normal process drive "
        "an elevated one -- even as the same user. Start the backend elevated "
        "(Run as administrator) so it matches whatever launched the game."
    )


async def _ready_window() -> tuple[win32.WindowInfo, bool]:
    """Locate the game window and make sure it has a client area to aim at.

    Returns the window and whether it had to be un-minimized to get there.

    Raises:
        ServiceUnavailableError: if this build cannot reach the Win32 API.
        GameWindowNotFoundError: if the game is absent, or is minimized and
            restoring it is disabled.
    """
    if not win32.is_supported():
        raise ServiceUnavailableError(
            "Game input needs the Windows API, which is not available in this "
            "environment"
        )

    title = _window_title()
    window = win32.find_window(title=title, class_name=settings.GAME_INPUT_WINDOW_CLASS)
    if window is None:
        raise GameWindowNotFoundError(
            f"No window titled {title!r} of class "
            f"{settings.GAME_INPUT_WINDOW_CLASS!r}. Is the game running?"
        )

    # Checked before anything else is attempted: when UIPI is blocking us every
    # later call fails too, but with symptoms that look like unrelated bugs --
    # a restore that does nothing, a click that vanishes.
    if not win32.can_post(window.hwnd):
        raise _access_denied()

    restored = False
    if window.minimized:
        if not settings.GAME_INPUT_RESTORE_IF_MINIMIZED:
            raise GameWindowNotFoundError(
                f"The {title!r} window is minimized and "
                "GAME_INPUT_RESTORE_IF_MINIMIZED is off, so it has no area to click"
            )
        logger.info("Restoring the minimized %r window", window.title)
        if not win32.restore(window.hwnd):
            raise _access_denied()
        restored = True
        window = await _await_client_area(window.hwnd) or window

    if window.client_width <= 0 or window.client_height <= 0:
        raise GameWindowNotFoundError(
            f"The {title!r} window reports no client area, so there is nowhere "
            "to aim a click"
        )
    return window, restored


async def _await_client_area(hwnd: int) -> win32.WindowInfo | None:
    """Poll a just-restored window until Windows gives it real geometry."""
    for _ in range(_RESTORE_ATTEMPTS):
        window = win32.describe(hwnd)
        if window is None:
            return None
        if not window.minimized and window.client_width > 0:
            return window
        await asyncio.sleep(_POLL_SECONDS)
    return win32.describe(hwnd)


async def _watch_log(
    tail: LogTail, expected: re.Pattern[str] | None, offset: int
) -> tuple[ClickConfirmation | None, str | None, bool]:
    """Wait for the game to react to a click.

    Returns the strength of the proof, the line that carried it, and -- whether
    or not the wait succeeded -- whether a bare touch was seen at all. That last
    flag is what turns a failure into a diagnosis: a touch with no target event
    means the click reached the game and missed the button, which is a
    coordinate to re-measure, while no touch at all means the input never
    arrived, which is a window or a privilege problem.

    Lines are matched against the message rather than the raw line, because that
    is what an :class:`app.utils.game_log.EventRule` is written against.
    """
    deadline = time.monotonic() + settings.GAME_INPUT_VERIFY_TIMEOUT_SECONDS
    cursor = offset
    touched = False
    while True:
        chunk, cursor = tail.read_since(cursor)
        for raw in chunk.splitlines():
            parsed = game_log.parse_line(raw)
            message = parsed.message if parsed is not None else raw
            if expected is not None and expected.search(message):
                return ClickConfirmation.TARGET_EVENT, raw.strip(), True
            if game_log.TOUCH_REGISTERED.search(message):
                if expected is None:
                    return ClickConfirmation.TOUCH, raw.strip(), True
                touched = True
        if time.monotonic() >= deadline:
            return None, None, touched
        await asyncio.sleep(_POLL_SECONDS)


async def _post_click(hwnd: int, x: int, y: int, hold_seconds: float) -> None:
    """Post one complete click at a client-area point.

    The move is not optional. It is what the panel service needs for its own
    reasons, and the game needs it too: the Unity build drives its UI through
    TouchScript, whose Windows pointer handler tracks position from motion
    messages, so a button message on its own would be resolved against wherever
    the game last believed the pointer was.
    """
    win32.post_mouse_move(hwnd, x, y)
    win32.post_left_down(hwnd, x, y)
    await asyncio.sleep(hold_seconds)
    win32.post_left_up(hwnd, x, y)


def _require_log_for_verification() -> LogTail:
    """Return the log cursor, failing loudly when there is nothing to read.

    Degrading to unverified silently is exactly what must not happen here. A
    click at a stale coordinate produces no error of its own -- the game simply
    does nothing -- so an unread log would turn every click into an unprovable
    claim.

    Raises:
        GameInputConfigError: if the active game names no log, or names one that
            does not exist yet.
    """
    tail = _log()
    if tail is None:
        raise GameInputConfigError(
            f"GAME_INPUT_VERIFY_CLICKS is on but the config for "
            f"{_game_or_raise().name!r} names no 'log' to confirm clicks "
            "against. Add one, or set GAME_INPUT_VERIFY_CLICKS=false to click "
            "without confirmation."
        )
    if not tail.exists():
        raise GameInputConfigError(
            f"GAME_INPUT_VERIFY_CLICKS is on but the log for "
            f"{_game_or_raise().name!r} does not exist yet: {tail.path}. Start "
            "the game and try again, or set GAME_INPUT_VERIFY_CLICKS=false."
        )
    return tail


def _not_confirmed(
    name: str, x: int, y: int, expected: str | None, *, touched: bool
) -> GameClickNotConfirmedError:
    """Explain an unconfirmed click in terms of what to go and fix."""
    timeout = settings.GAME_INPUT_VERIFY_TIMEOUT_SECONDS
    if touched:
        return GameClickNotConfirmedError(
            f"Clicked {name!r} at ({x}, {y}) and the game registered a touch, but "
            f"it never published {expected!r} within {timeout}s. The click "
            "reached the game and missed the button: either the target is not on "
            "screen right now, or its coordinates in the game config need "
            "re-measuring against a current screenshot."
        )
    return GameClickNotConfirmedError(
        f"Clicked {name!r} at ({x}, {y}) but the game logged no reaction within "
        f"{timeout}s -- not even a touch. The window is not receiving posted "
        "input, which is usually integrity levels: start the backend elevated if "
        "the game is elevated."
    )


# --- public API -----------------------------------------------------------


async def status() -> GameInputStatus:
    """Report what the service can see of the game window.

    Never raises: a closed game, a game config with no targets and a non-Windows
    host are all states worth reporting rather than failed requests.
    """
    base: dict[str, Any] = {
        "window_title": settings.GAME_INPUT_WINDOW_TITLE,
        "window_class": settings.GAME_INPUT_WINDOW_CLASS,
        "game": "",
        "log_path": "",
        "verify_clicks": settings.GAME_INPUT_VERIFY_CLICKS,
    }

    if not win32.is_supported():
        return GameInputStatus(state=GameWindowState.UNSUPPORTED, **base)

    try:
        base["game"] = settings.ideck_active_game
        title = _window_title()
        base["window_title"] = title
        base["target_count"] = len(_targets())
        path = _log_path()
        base["log_path"] = "" if path is None else str(path)
    except ActiveGameSelectionError as exc:
        logger.warning("Game-input active-game selection is unusable: %s", exc)
        return GameInputStatus(state=GameWindowState.NOT_FOUND, **base)
    except AppException as exc:
        logger.warning("Game-input configuration is unusable: %s", exc.message)
        return GameInputStatus(state=GameWindowState.NOT_FOUND, **base)

    window = win32.find_window(title=title, class_name=settings.GAME_INPUT_WINDOW_CLASS)
    if window is None:
        return GameInputStatus(state=GameWindowState.NOT_FOUND, **base)

    base |= {
        "hwnd": window.hwnd,
        "client_width": window.client_width,
        "client_height": window.client_height,
    }
    if not win32.can_post(window.hwnd):
        return GameInputStatus(state=GameWindowState.ACCESS_DENIED, **base)
    if window.minimized:
        return GameInputStatus(state=GameWindowState.MINIMIZED, **base)
    return GameInputStatus(state=GameWindowState.READY, **base)


async def targets() -> list[ClickTargetInfo]:
    """Every target the active game configures, in name order.

    Client coordinates are populated only while the game window is open and
    restored, which is what makes this the thing to read when a coordinate needs
    checking against a screenshot.

    Raises:
        GameInputConfigError: if the game config cannot be read, or a target in
            it is malformed.
    """
    configured = _targets()
    window: win32.WindowInfo | None = None
    if win32.is_supported():
        window = win32.find_window(
            title=_window_title(), class_name=settings.GAME_INPUT_WINDOW_CLASS
        )
    usable = (
        window is not None
        and not window.minimized
        and window.client_width > 0
        and window.client_height > 0
    )

    found: list[ClickTargetInfo] = []
    for name in sorted(configured):
        try:
            target = named_target(configured, name)
        except ClickTargetError as exc:
            raise GameInputConfigError(str(exc)) from exc
        point = (
            target.to_point(window.client_width, window.client_height)
            if usable and window is not None
            else (None, None)
        )
        found.append(
            ClickTargetInfo(
                name=name,
                fraction_x=target.x,
                fraction_y=target.y,
                confirm_event=target.confirm,
                client_x=point[0],
                client_y=point[1],
            )
        )
    return found


async def click(
    name: str, *, verify: bool | None = None, hold_seconds: float | None = None
) -> ClickResult:
    """Click one configured target and confirm the game reacted.

    Clicks serialise on the module lock, so two callers never interleave a
    button-down and a button-up on the same window.

    Raises:
        ServiceUnavailableError: if this build cannot reach the Win32 API.
        GameWindowNotFoundError: if the game window is absent or unusable.
        GameTargetNotFoundError: if the active game configures no such target.
        GameInputConfigError: if the config or the target is malformed, or
            verification is on with no log to read.
        GameClickNotConfirmedError: if the click was posted but the game never
            reacted to it.
    """
    started = time.monotonic()
    should_verify = settings.GAME_INPUT_VERIFY_CLICKS if verify is None else verify
    hold = (
        settings.GAME_INPUT_CLICK_HOLD_SECONDS if hold_seconds is None else hold_seconds
    )

    async with _get_lock():
        game = _game_or_raise()
        # Resolved before any window is touched, so a typo posts nothing.
        target = _resolve_target(name)
        expected = _confirm_pattern(target) if should_verify else None
        tail = _require_log_for_verification() if should_verify else None

        window, restored = await _ready_window()
        x, y = _client_point(target, window)

        offset = tail.offset() if tail is not None else 0
        try:
            await _post_click(window.hwnd, x, y, hold)
        except win32.WindowAccessDenied as exc:
            raise _access_denied() from exc

        confirmation: ClickConfirmation | None = None
        evidence: str | None = None
        touched = False
        refocused = False
        if tail is not None:
            confirmation, evidence, touched = await _watch_log(tail, expected, offset)

            # A window that has never been focused can swallow the first posted
            # click. Worth exactly one retry, and still without moving the cursor.
            if confirmation is None and settings.GAME_INPUT_FOCUS_ON_RETRY:
                logger.info("Click on %r went unconfirmed; retrying focused", name)
                win32.focus(window.hwnd)
                refocused = True
                offset = tail.offset()
                try:
                    await _post_click(window.hwnd, x, y, hold)
                except win32.WindowAccessDenied as exc:
                    raise _access_denied() from exc
                confirmation, evidence, retried_touch = await _watch_log(
                    tail, expected, offset
                )
                touched = touched or retried_touch

    # Raised outside the lock: the window is free for the next caller either way.
    if should_verify and confirmation is None:
        raise _not_confirmed(name, x, y, target.confirm, touched=touched)

    elapsed_ms = round((time.monotonic() - started) * 1000)
    logger.info(
        "Clicked %r on %s at (%d, %d) in %dms%s",
        name,
        game.name,
        x,
        y,
        elapsed_ms,
        f" [{confirmation.value}]" if confirmation else " [unverified]",
    )
    return ClickResult(
        target=name,
        game=game.name,
        fraction_x=target.x,
        fraction_y=target.y,
        client_x=x,
        client_y=y,
        confirmed=confirmation is not None,
        confirmed_by=confirmation,
        verified=should_verify,
        expected_event=target.confirm,
        evidence=evidence,
        restored=restored,
        refocused=refocused,
        elapsed_ms=elapsed_ms,
    )


def reset() -> None:
    """Drop the cached game config and lock without touching any window.

    Mirrors ``ideck_service.reset()``: tests call it between cases. Clearing the
    lock matters as much as clearing the config -- the next test builds one bound
    to its own event loop.
    """
    global _game, _lock
    _game = None
    _lock = None


def reset_game_config() -> None:
    """Drop only the cached per-game metadata after a runtime game switch."""
    global _game
    _game = None
