"""Virtual OLED i-deck control.

``OledPanelSvc.exe`` owns an SDL window with no API of its own, so a press is
delivered as mouse messages ``PostMessage``d straight to it -- never touching
the physical cursor. Key geometry comes from the same layout file the panel
service renders from (:mod:`app.utils.panel_xml`), which is also the only
source of key names. A press is confirmed by reading what was appended to the
panel's log (:mod:`app.utils.panel_log`/:mod:`app.utils.log_tail`) after it, and
retried once with the window foregrounded if unconfirmed, since SDL can swallow
a first click on an unfocused window.
"""

from __future__ import annotations

import asyncio
import re
import time

from app.config.game_config import ActiveGameSelectionError
from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    AppException,
    IDeckAccessDeniedError,
    IDeckButtonNotFoundError,
    IDeckConfigError,
    IDeckPressNotConfirmedError,
    IDeckWindowNotFoundError,
    ServiceUnavailableError,
)
from app.schemas.ideck import (
    IDeckButton,
    IDeckStatus,
    IDeckWindowState,
    PressResult,
    ProbeResult,
)
from app.utils import panel_log, win32
from app.utils.log_tail import LogTail
from app.utils.panel_xml import PanelButton, PanelLayout, PanelXmlError, parse_panel

logger = get_logger("ideck")

_layout: PanelLayout | None = None
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


def _layout_or_raise() -> PanelLayout:
    """Parse the panel layout once and keep it."""
    global _layout
    if _layout is None:
        try:
            _layout = parse_panel(settings.ideck_panel_xml)
        except PanelXmlError as exc:
            raise IDeckConfigError(str(exc)) from exc
        logger.info(
            "Loaded i-deck layout %r (%dx%d, %d buttons) from %s",
            _layout.panel_id,
            _layout.width,
            _layout.height,
            len(_layout.buttons),
            settings.ideck_panel_xml,
        )
    return _layout


def _log() -> LogTail:
    """A cursor over the panel service's log. Built per call, not cached, since
    tests move the path between cases."""
    return LogTail(settings.ideck_log_path, poll_seconds=_POLL_SECONDS)


def _resolve_button(name: str) -> PanelButton:
    """Map a caller-supplied name onto a key in the layout, case-insensitively."""
    layout = _layout_or_raise()
    button = layout.by_xml_id(name.strip())
    if button is None:
        known = sorted(b.xml_id for b in layout.buttons)
        raise IDeckButtonNotFoundError(
            f"No i-deck button named {name!r}. Known names: {', '.join(known)}"
        )
    return button


def _client_point(button: PanelButton, window: win32.WindowInfo) -> tuple[int, int]:
    """Where a key's centre sits in the live window."""
    return _layout_or_raise().to_client(
        button,
        client_width=window.client_width,
        client_height=window.client_height,
    )


def _access_denied() -> IDeckAccessDeniedError:
    """The one failure that looks like a bug but is really a deployment detail."""
    return IDeckAccessDeniedError(
        f"Windows is blocking input to the {settings.IDECK_WINDOW_TITLE!r} window. "
        "It belongs to a process running at a higher integrity level than this "
        "backend, and User Interface Privilege Isolation does not let a normal "
        "process drive an elevated one -- even as the same user. Start the "
        "backend elevated (Run as administrator) so it matches whatever launched "
        "the panel."
    )


async def _ready_window() -> tuple[win32.WindowInfo, bool]:
    """Locate the panel window and make sure it has a client area to aim at.
    Returns the window and whether it had to be un-minimized to get there."""
    if not win32.is_supported():
        raise ServiceUnavailableError(
            "i-deck control needs the Windows API, which is not available in "
            "this environment"
        )

    window = win32.find_window(
        title=settings.IDECK_WINDOW_TITLE, class_name=settings.IDECK_WINDOW_CLASS
    )
    if window is None:
        raise IDeckWindowNotFoundError(
            f"No window titled {settings.IDECK_WINDOW_TITLE!r} of class "
            f"{settings.IDECK_WINDOW_CLASS!r}. Is OledPanelSvc running?"
        )

    # Checked first: a UIPI block otherwise surfaces later as unrelated-looking
    # bugs -- a restore that does nothing, a press that vanishes.
    if not win32.can_post(window.hwnd):
        raise _access_denied()

    restored = False
    if window.minimized:
        if not settings.IDECK_RESTORE_IF_MINIMIZED:
            raise IDeckWindowNotFoundError(
                f"The {settings.IDECK_WINDOW_TITLE!r} window is minimized and "
                "IDECK_RESTORE_IF_MINIMIZED is off, so it has no area to press"
            )
        logger.info("Restoring the minimized %r window", window.title)
        if not win32.restore(window.hwnd):
            raise _access_denied()
        restored = True
        window = await _await_client_area(window.hwnd) or window

    if window.client_width <= 0 or window.client_height <= 0:
        raise IDeckWindowNotFoundError(
            f"The {settings.IDECK_WINDOW_TITLE!r} window reports no client area, "
            "so there is nowhere to aim a press"
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


async def _watch_log(pattern: re.Pattern[str], offset: int) -> str | None:
    """Wait for a line matching ``pattern`` after ``offset``, or ``None`` on timeout."""
    return await _log().wait_for(
        pattern, offset=offset, timeout=settings.IDECK_VERIFY_TIMEOUT_SECONDS
    )


async def _post_press(hwnd: int, x: int, y: int, hold_seconds: float) -> None:
    """Post one complete click at a client-area point. The move is not optional:
    SDL takes a click's position from the last motion event, not the button message."""
    win32.post_mouse_move(hwnd, x, y)
    win32.post_left_down(hwnd, x, y)
    await asyncio.sleep(hold_seconds)
    win32.post_left_up(hwnd, x, y)


def _require_log_for_verification() -> None:
    """Fail loudly when confirmation is on but the log cannot be read -- silently
    degrading to unverified would defeat the point of verification."""
    if not _log().exists():
        raise IDeckConfigError(
            f"IDECK_VERIFY_PRESSES is on but no panel log exists at "
            f"{settings.ideck_log_path}. Point IDECK_LOG_PATH at the real log, "
            "or set IDECK_VERIFY_PRESSES=false to press without confirmation."
        )


# --- public API -----------------------------------------------------------


async def status() -> IDeckStatus:
    """Report what the service can see of the panel. Never raises -- a closed
    panel, missing layout file, or non-Windows host are states, not failures."""
    base = {
        "window_title": settings.IDECK_WINDOW_TITLE,
        "window_class": settings.IDECK_WINDOW_CLASS,
        "game": "unknown",
        "panel_xml": str(settings.ideck_panel_xml),
        "log_path": str(settings.ideck_log_path),
        "verify_presses": settings.IDECK_VERIFY_PRESSES,
    }

    if not win32.is_supported():
        return IDeckStatus(state=IDeckWindowState.UNSUPPORTED, **base)

    try:
        base["game"] = settings.ideck_active_game
        layout = _layout_or_raise()
    except ActiveGameSelectionError as exc:
        logger.warning("i-deck active-game selection is unusable: %s", exc)
        return IDeckStatus(state=IDeckWindowState.NOT_FOUND, **base)
    except AppException as exc:
        logger.warning("i-deck configuration is unusable: %s", exc.message)
        return IDeckStatus(state=IDeckWindowState.NOT_FOUND, **base)

    base |= {
        "panel_id": layout.panel_id,
        "panel_width": layout.width,
        "panel_height": layout.height,
        "button_count": len(layout.buttons),
    }

    window = win32.find_window(
        title=settings.IDECK_WINDOW_TITLE, class_name=settings.IDECK_WINDOW_CLASS
    )
    if window is None:
        return IDeckStatus(state=IDeckWindowState.NOT_FOUND, **base)

    if not win32.can_post(window.hwnd):
        # Surfaced here, not just on the first press -- the window looks healthy
        # until something actually tries to drive it.
        state = IDeckWindowState.ACCESS_DENIED
    elif window.minimized:
        state = IDeckWindowState.MINIMIZED
    else:
        state = IDeckWindowState.READY
    return IDeckStatus(
        state=state,
        hwnd=window.hwnd,
        client_width=window.client_width,
        client_height=window.client_height,
        **base,
    )


async def buttons() -> list[IDeckButton]:
    """Every key on the deck, in layout order. Client coordinates are filled in
    only when the panel is open and not minimized."""
    layout = _layout_or_raise()

    window: win32.WindowInfo | None = None
    if win32.is_supported():
        window = win32.find_window(
            title=settings.IDECK_WINDOW_TITLE, class_name=settings.IDECK_WINDOW_CLASS
        )
        if window is not None and (window.minimized or window.client_width <= 0):
            window = None

    resolved: list[IDeckButton] = []
    for button in layout.buttons:
        point = _client_point(button, window) if window is not None else None
        resolved.append(
            IDeckButton(
                xml_id=button.xml_id,
                button_id=button.button_id,
                panel_x=button.x,
                panel_y=button.y,
                width=button.width,
                height=button.height,
                client_x=point[0] if point else None,
                client_y=point[1] if point else None,
            )
        )
    return resolved


async def press(
    name: str, *, verify: bool | None = None, hold_seconds: float | None = None
) -> PressResult:
    """Press one key and confirm the panel registered it. Presses serialise on
    the module lock, so two callers never interleave a down and up."""
    started = time.monotonic()
    should_verify = settings.IDECK_VERIFY_PRESSES if verify is None else verify
    hold = settings.IDECK_PRESS_HOLD_SECONDS if hold_seconds is None else hold_seconds

    async with _get_lock():
        button = _resolve_button(name)
        if should_verify:
            _require_log_for_verification()

        window, restored = await _ready_window()
        x, y = _client_point(button, window)
        pattern = panel_log.press_pattern(button.button_id)

        offset = _log().offset()
        try:
            await _post_press(window.hwnd, x, y, hold)
        except win32.WindowAccessDenied as exc:
            raise _access_denied() from exc
        evidence = await _watch_log(pattern, offset) if should_verify else None

        refocused = False
        if should_verify and evidence is None and settings.IDECK_FOCUS_ON_RETRY:
            # SDL can swallow the first click on an unfocused window as the one
            # that focuses it -- foregrounding costs focus but never the cursor.
            logger.info(
                "Press of %r went unconfirmed; retrying with the panel focused", name
            )
            win32.focus(window.hwnd)
            refocused = True
            offset = _log().offset()
            try:
                await _post_press(window.hwnd, x, y, hold)
            except win32.WindowAccessDenied as exc:
                raise _access_denied() from exc
            evidence = await _watch_log(pattern, offset)

    # Raised outside the lock: the panel is free for the next caller either way.
    if should_verify and evidence is None:
        raise IDeckPressNotConfirmedError(
            f"Pressed {name!r} (switch {button.button_id}) at ({x}, {y}) but the "
            f"panel logged nothing within {settings.IDECK_VERIFY_TIMEOUT_SECONDS}s. "
            "The window may be ignoring posted input."
        )

    elapsed_ms = round((time.monotonic() - started) * 1000)
    logger.info(
        "Pressed %r (%s, switch %d) at (%d, %d) in %dms%s",
        name,
        button.xml_id,
        button.button_id,
        x,
        y,
        elapsed_ms,
        "" if should_verify else " [unverified]",
    )
    return PressResult(
        button=name,
        xml_id=button.xml_id,
        button_id=button.button_id,
        client_x=x,
        client_y=y,
        confirmed=evidence is not None,
        verified=should_verify,
        evidence=evidence,
        restored=restored,
        refocused=refocused,
        elapsed_ms=elapsed_ms,
    )


async def press_sequence(
    names: list[str], *, delay_seconds: float = 0.5, verify: bool | None = None
) -> list[PressResult]:
    """Press several keys in order, pausing between them. Stops at the first
    failure, so a half-finished sequence is never reported as a success."""
    results: list[PressResult] = []
    for index, name in enumerate(names):
        if index:
            await asyncio.sleep(delay_seconds)
        results.append(await press(name, verify=verify))
    return results


async def probe() -> ProbeResult:
    """Check the panel reacts to posted input, without pressing anything --
    posts a mouse move and watches the log for SDL noticing it cross the panel's
    edge. Never raises: a failed probe is a result, not an error."""
    if not win32.is_supported():
        return ProbeResult(
            supported=False,
            window_found=False,
            posted=False,
            observed=False,
            detail="The Windows API is not available in this environment.",
        )

    try:
        window, _ = await _ready_window()
    except AppException as exc:
        return ProbeResult(
            supported=True,
            window_found=False,
            posted=False,
            observed=False,
            detail=exc.message,
        )

    offset = _log().offset()
    try:
        win32.post_mouse_move(
            window.hwnd, window.client_width // 2, window.client_height // 2
        )
    except win32.WindowAccessDenied:
        return ProbeResult(
            supported=True,
            window_found=True,
            posted=False,
            observed=False,
            detail=_access_denied().message,
        )
    except OSError as exc:
        return ProbeResult(
            supported=True,
            window_found=True,
            posted=False,
            observed=False,
            detail=f"Could not post to the panel window: {exc}",
        )

    evidence = await _watch_log(panel_log.MOUSE_CROSSING, offset)
    if evidence is not None:
        detail = "The panel reacted to a posted mouse move, so presses should land."
    else:
        detail = (
            "The move was posted but the panel logged no reaction. It may already "
            "have considered the pointer inside, so this is inconclusive rather "
            "than a definite failure -- try a press of a harmless key next."
        )
    return ProbeResult(
        supported=True,
        window_found=True,
        posted=True,
        observed=evidence is not None,
        evidence=evidence,
        detail=detail,
    )


def reset() -> None:
    """Drop the cached layout and lock without touching any window. No per-game
    state to drop -- the deck is addressed by layout key, not by game."""
    global _layout, _lock
    _layout = None
    _lock = None
