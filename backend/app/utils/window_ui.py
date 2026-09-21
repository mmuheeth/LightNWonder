"""Driving another application's window: naming the control to click, and
clicking a point of it.

Deliberately ignorant of who consumes it, like the other ``app/utils`` readers.
The distinction it exists to draw is between the two kinds of window this host
has to click:

* one that owns its buttons -- WinForms gives every control its own HWND with
  its own caption, so a click can be aimed at *what the button is* and the
  window itself says whether it is enabled yet. Nothing has to be measured, and
  a moved or resized window needs no re-measurement.
* one that draws them -- a Unity canvas exposes no children at all
  (:func:`app.utils.win32.descendants` comes back empty), so the only thing
  left to aim at is a point, and :mod:`app.utils.click_target` is what keeps
  that point a fraction of the window rather than a pixel. (A window drawing a
  *web page* is the third kind, and is better driven through
  :mod:`app.utils.cdp` than clicked at all.)

:func:`click_at` serves both, because by the time either has resolved, a click
is a screen point and the question is only whether the right window is under it.
It is also where **focus** is dealt with: these windows sit in the background
(a sequence walking three of them leaves two covered at any moment), injected
input lands on whatever is topmost rather than at an HWND, and raising a window
is asynchronous -- so the window is brought forward and *waited for* before
anything is pressed.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterable

from app.utils import win32

__all__ = [
    "CURSOR_SETTLE_SECONDS",
    "FOCUS_POLL_SECONDS",
    "FOCUS_WAIT_SECONDS",
    "WindowNotTopmost",
    "click_at",
    "control_labels",
    "find_control",
    "focus_window",
    "same_label",
]

CURSOR_SETTLE_SECONDS = 0.02
"""How long to let the target see the cursor arrive before pressing. Unity
samples the cursor's position rather than trusting a message's coordinates, so
the move has to land before the button does."""

FOCUS_WAIT_SECONDS = 2.0
"""How long to give a window to actually come forward after being asked to.

Raising a window is asynchronous: ``bring_to_front`` returns before the desktop
has restacked, so a window that was *behind* another one is not under the cursor
yet at the moment it is asked to be. Waiting is the difference between driving a
background window and refusing to -- and these windows are normally in the
background, since a sequence that walks three of them in turn leaves two of them
covered at any moment."""

FOCUS_POLL_SECONDS = 0.05

_WHITESPACE = re.compile(r"\s+")


class WindowNotTopmost(OSError):
    """Something other than the expected window is under the point to click.

    Injected input lands on whatever is topmost rather than at an HWND, so this
    is the difference between clicking a button and clicking whatever covered
    it. Carries both handles; the caller decides what kind of failure that is
    in its own vocabulary."""

    def __init__(self, expected: int, found: int) -> None:
        self.expected = expected
        self.found = found
        super().__init__(
            f"window 0x{found:X} is under the target point, not 0x{expected:X}"
        )


def _normalise(text: str) -> str:
    """A caption reduced to what a human means by it.

    Collapses runs of whitespace (``"Events / History"`` and
    ``"Events /  History"`` name one button), drops the ``&`` that marks a
    WinForms accelerator (``"&Connect"`` is *Connect* with an underlined C), and
    folds case."""
    return _WHITESPACE.sub(" ", text.replace("&", "")).strip().casefold()


def same_label(left: str, right: str) -> bool:
    """Whether two captions name the same control."""
    return _normalise(left) == _normalise(right)


def find_control(
    controls: Iterable[win32.ControlInfo],
    text: str,
    *,
    visible_only: bool = True,
) -> win32.ControlInfo | None:
    """The control captioned ``text``, or ``None``.

    Hidden controls are skipped by default: a WinForms tab page keeps the
    controls of every *other* page alive and off-screen, so a search that
    ignored visibility would happily aim at the copy of a button that nobody
    can see. A disabled control is still returned -- "it is there but greyed
    out" is an answer the caller needs, not a miss."""
    for control in controls:
        if visible_only and not control.visible:
            continue
        if control.text and same_label(control.text, text):
            return control
    return None


def control_labels(controls: Iterable[win32.ControlInfo]) -> list[str]:
    """Every distinct caption in a window, in the order found, for the error
    message that has to say what *was* there instead."""
    seen: list[str] = []
    for control in controls:
        label = control.text.strip()
        if label and label not in seen:
            seen.append(label)
    return seen


async def _await_topmost(
    hwnd: int, screen_x: int, screen_y: int, wait_seconds: float
) -> int:
    """Wait for ``hwnd`` to be the window under a screen point, and return
    whatever is there when the wait runs out.

    Asks again halfway through rather than only polling: Windows can refuse a
    foreground change to a process that does not already own the foreground,
    and the second request usually lands once the first has stopped competing
    with whatever was being dismissed."""
    deadline = time.monotonic() + wait_seconds
    nudge_at = time.monotonic() + wait_seconds / 2
    nudged = False
    while True:
        found = win32.window_at(screen_x, screen_y)
        if found == hwnd:
            return found
        now = time.monotonic()
        if now >= deadline:
            return found
        if not nudged and now >= nudge_at:
            nudged = True
            win32.bring_to_front(hwnd)
        await asyncio.sleep(FOCUS_POLL_SECONDS)


async def focus_window(hwnd: int, *, wait_seconds: float | None = None) -> bool:
    """Bring a window to the foreground and wait for it to actually get there.

    The same asynchrony as :func:`click_at`'s wait, asked as a different
    question: this is about a window being *activated* -- which is what shows
    it to someone watching, and what a UI that repaints on activation needs --
    rather than about a point being clickable. Returns whether it made it, so
    the caller can report a window Windows refused to activate rather than
    claim one it never saw come forward."""
    budget = FOCUS_WAIT_SECONDS if wait_seconds is None else wait_seconds
    deadline = time.monotonic() + budget
    nudge_at = time.monotonic() + budget / 2
    nudged = False
    win32.bring_to_front(hwnd)
    while True:
        if win32.foreground_window() == hwnd:
            return True
        now = time.monotonic()
        if now >= deadline:
            return win32.foreground_window() == hwnd
        if not nudged and now >= nudge_at:
            nudged = True
            win32.bring_to_front(hwnd)
        await asyncio.sleep(FOCUS_POLL_SECONDS)


async def click_at(
    hwnd: int,
    screen_x: int,
    screen_y: int,
    *,
    hold_seconds: float,
    settle_seconds: float = CURSOR_SETTLE_SECONDS,
    focus_wait_seconds: float | None = None,
) -> bool:
    """Click a screen point as real hardware input, and report whether the
    window came to the front for it.

    ``hwnd`` is the **top-level** window expected under the point, not the
    control being aimed at: ``win32.window_at`` resolves a hit up to its root,
    so a control's own handle would never match.

    The window is **brought forward and waited for**, because these windows
    live in the background: injected input lands on whatever is topmost rather
    than at an HWND, so clicking a covered window would drive whatever covers
    it. Only once the wait runs out with something else still there does this
    raise :class:`WindowNotTopmost`, without pressing anything. The cursor goes
    back where it was either way."""
    budget = FOCUS_WAIT_SECONDS if focus_wait_seconds is None else focus_wait_seconds
    raised = win32.bring_to_front(hwnd)

    previous = win32.get_cursor_pos()
    win32.set_cursor_pos(screen_x, screen_y)
    try:
        await asyncio.sleep(settle_seconds)

        # Firing blind risks clicking whatever is on top of the target instead
        # (a File Explorer window, say) -- so this is checked, not assumed,
        # and given time to become true before it is believed.
        topmost = await _await_topmost(hwnd, screen_x, screen_y, budget)
        if topmost != hwnd:
            raise WindowNotTopmost(hwnd, topmost)

        # Re-aimed after the wait: raising a window can move the pointer's
        # target out from under it (a restack, a resize on activation), and the
        # press is delivered wherever the cursor actually is.
        win32.set_cursor_pos(screen_x, screen_y)
        await asyncio.sleep(settle_seconds)

        win32.inject_left_down()
        await asyncio.sleep(hold_seconds)
        win32.inject_left_up()
    finally:
        win32.set_cursor_pos(*previous)
    return raised
