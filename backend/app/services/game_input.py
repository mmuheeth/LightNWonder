"""Clicking the game's own window."""

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
from app.config.runtime import settings
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
    """Return the module lock, created lazily (not at import time) since each
    test gets a fresh event loop and :func:`reset` clears it between tests."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _game_or_raise() -> GameConfig:
    """Load the selected game's config once and keep it."""
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
    """The active game's ``button_targets`` block. Empty when the game declares
    none -- a real state, not an error."""
    return _game_or_raise().button_targets


def _window_title() -> str:
    """Title to search for: the setting when given, else the game's own name --
    the simulator titles its window after the game."""
    configured = settings.GAME_INPUT_WINDOW_TITLE.strip()
    return configured or _game_or_raise().name


def _log_path() -> Path | None:
    """The active game's log, or ``None`` when its config names none."""
    return _game_or_raise().log_path


def _log() -> LogTail | None:
    """A cursor over the active game's log, or ``None`` when it names none. Built
    per call, not cached, since the path follows the active game."""
    path = _log_path()
    return None if path is None else LogTail(path, poll_seconds=_POLL_SECONDS)


def _resolve_target(name: str) -> ClickTarget:
    """Map a caller-supplied name onto an aim point in the active game."""
    targets = _targets()
    try:
        return named_target(targets, name)
    except ClickTargetError as exc:
        # Absent is the caller's mistake; present-but-unreadable is the config's.
        if name.strip().casefold() in {found.casefold() for found in targets}:
            raise GameInputConfigError(str(exc)) from exc
        raise GameTargetNotFoundError(str(exc)) from exc


def _confirm_pattern(target: ClickTarget) -> re.Pattern[str] | None:
    """The log pattern proving a click on ``target`` hit the right button, or ``None``
    when it names no event this game recognises."""
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
    """Where a target sits in the live window."""
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
    Returns the window and whether it had to be un-minimized to get there."""
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

    # Checked first: a UIPI block otherwise surfaces later as unrelated-looking
    # bugs -- a restore that does nothing, a click that vanishes.
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
    """Wait for the game to react to a click."""
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


_CURSOR_SETTLE_SECONDS = 0.02
"""How long to let the target see the cursor arrive before pressing -- Unity
samples the cursor's position rather than trusting a message's coordinates,
so the move has to land before the button does."""


async def _inject_click(hwnd: int, x: int, y: int, hold_seconds: float) -> bool:
    """Click a client-area point as real hardware input."""
    screen_x, screen_y = win32.client_to_screen(hwnd, x, y)
    raised = win32.bring_to_front(hwnd)

    previous = win32.get_cursor_pos()
    win32.set_cursor_pos(screen_x, screen_y)
    try:
        await asyncio.sleep(_CURSOR_SETTLE_SECONDS)

        # Injected input lands on whatever is topmost under the cursor, not
        # at this hwnd -- firing blind risks clicking whatever is on top of
        # the game instead (a File Explorer window, say).
        topmost = win32.window_at(screen_x, screen_y)
        if topmost != hwnd:
            raise GameWindowNotFoundError(
                f"The window under the target point is 0x{topmost:X}, not "
                f"the game's 0x{hwnd:X}, so a click there would hit that "
                "window instead. Bring the game window to the front and "
                "make sure nothing else covers it, then try again."
            )

        win32.inject_left_down()
        await asyncio.sleep(hold_seconds)
        win32.inject_left_up()
    finally:
        win32.set_cursor_pos(*previous)
    return raised


def _require_log_for_verification() -> LogTail:
    """The log cursor, failing loudly when there is nothing to read: an unread log makes
    every click an unprovable claim."""
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
    """Report what the service can see of the game window. Never raises -- a
    closed game or non-Windows host is a state, not a failure."""
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
    """Every target the active game configures, in name order. Client coordinates
    are populated only while the game window is open and restored."""
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
    """Click one configured target and confirm the game reacted. Clicks
    serialise on the module lock, so two callers never interleave a down and up."""
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
            await _inject_click(window.hwnd, x, y, hold)
        except win32.WindowAccessDenied as exc:
            raise _access_denied() from exc

        confirmation: ClickConfirmation | None = None
        evidence: str | None = None
        touched = False
        refocused = False
        if tail is not None:
            confirmation, evidence, touched = await _watch_log(tail, expected, offset)

            # An unfocused window can swallow the first injected click -- one
            # retry. `_inject_click` already brings the window to the front
            # on every attempt, so the retry gains nothing extra there; it
            # exists for a click that failed because the game briefly wasn't
            # topmost yet when the first one fired.
            if confirmation is None and settings.GAME_INPUT_FOCUS_ON_RETRY:
                logger.info("Click on %r went unconfirmed; retrying focused", name)
                refocused = True
                offset = tail.offset()
                try:
                    await _inject_click(window.hwnd, x, y, hold)
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
    """Drop the cached game config and lock without touching any window."""
    global _game, _lock
    _game = None
    _lock = None


def reset_game_config() -> None:
    """Drop only the cached per-game metadata after a runtime game switch."""
    global _game
    _game = None
