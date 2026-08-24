"""Minimal Win32 interop for driving another process's window -- the only
``ctypes`` in the codebase, so tests can monkeypatch it wholesale and the
suite still collects on a non-Windows machine.

Two delivery mechanisms live here because the i-deck and the game need
different ones. ``post_*`` uses ``PostMessageW`` rather than ``SendMessageW``
so an async caller never stalls behind a busy window, and reaches the i-deck's
SDL panel, which re-reads its message queue -- it never moves the physical
cursor. Unity does not react to posted mouse messages at all: it reads real OS
input, so reaching the game means moving the actual cursor
(``set_cursor_pos``) and injecting a real click (``inject_left_down`` /
``inject_left_up`` via ``SendInput``), which is why those two, unlike
``post_*``, land wherever is topmost under the cursor rather than at a
specific ``hwnd`` -- ``bring_to_front`` and ``window_at`` exist to make that
safe.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Any

# ``os.name`` rather than ``sys.platform``: mypy special-cases the latter, and
# with ``warn_unreachable`` it would report the non-Windows paths below as dead
# code when checked on a Windows checkout.
_IS_WINDOWS = os.name == "nt"

# ``ctypes.WinDLL`` and ``ctypes.WINFUNCTYPE`` only exist on Windows, and this
# module must still import cleanly elsewhere for the test suite to collect.
_WinDLL: Any = getattr(ctypes, "WinDLL", None)
_WINFUNCTYPE: Any = getattr(ctypes, "WINFUNCTYPE", None)

# --- Win32 constants ------------------------------------------------------

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

# Unity reads real OS input (cursor position + SendInput), not the posted
# window messages above -- those reach SDL's i-deck panel but land silently
# nowhere on the game's own window. GA_ROOT resolves whatever HWND is under a
# screen point up to its top-level window, for the "is the game really
# topmost" check `inject_click` needs but `post_*` never did.
GA_ROOT = 2
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

ERROR_ACCESS_DENIED = 5

# Integrity-level plumbing. UIPI silently drops input posted from a lower
# integrity level to a higher one, which is the difference between a backend
# started normally and one started elevated. Comparing the two levels detects
# that before a single message is sent -- unlike probing with a real message,
# which would either be filtered differently (WM_NULL is allowed through) or
# leave a trace in the target's log.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_INTEGRITY_LEVEL = 25

# Restores a minimized window *without* activating it, so a press never steals
# focus. SW_RESTORE would also bring the window to the foreground.
SW_SHOWNOACTIVATE = 4

# Stop / continue return values for the EnumWindows callback.
_STOP = 0
_CONTINUE = 1


class WindowAccessDenied(OSError):
    """UIPI blocked the call: the target window's process outranks this one in
    integrity level. Fix is to run both at the same level, not to retry."""


class _Rect(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class _Point(ctypes.Structure):
    _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))


class _MouseInput(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class _Input(ctypes.Structure):
    # SendInput's union covers keyboard/hardware input too, but this process
    # only ever sends the mouse variant, so the union is spelled out as a
    # single field rather than a real ctypes.Union.
    _fields_ = (("type", ctypes.c_ulong), ("mi", _MouseInput))


@dataclass(frozen=True)
class WindowInfo:
    """A snapshot of one top-level window. ``client_width``/``client_height``
    are the drawable area mouse messages are addressed in, and read ``0``
    while minimized -- Windows gives an iconic window no client area."""

    hwnd: int
    title: str
    class_name: str
    minimized: bool
    client_width: int
    client_height: int


_user32: Any = None
_kernel32_lib: Any = None
_advapi32_lib: Any = None
_enum_proc_type: Any = None


# --- internals ------------------------------------------------------------


def _proc_type() -> Any:
    """The ``EnumWindows`` callback signature, built once."""
    global _enum_proc_type
    if _enum_proc_type is None:
        _enum_proc_type = _WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
    return _enum_proc_type


def _lib() -> Any:
    """Return ``user32``. Argument types are spelled out rather than left to
    ctypes' defaults, whose ``int`` conversion truncates 64-bit handles."""
    global _user32
    if _user32 is not None:
        return _user32

    lib = _WinDLL("user32", use_last_error=True)
    lib.EnumWindows.argtypes = (_proc_type(), ctypes.c_ssize_t)
    lib.EnumWindows.restype = ctypes.c_int
    lib.IsWindow.argtypes = (ctypes.c_void_p,)
    lib.IsWindow.restype = ctypes.c_int
    lib.IsIconic.argtypes = (ctypes.c_void_p,)
    lib.IsIconic.restype = ctypes.c_int
    lib.GetWindowTextW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
    lib.GetWindowTextW.restype = ctypes.c_int
    lib.GetClassNameW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
    lib.GetClassNameW.restype = ctypes.c_int
    lib.GetClientRect.argtypes = (ctypes.c_void_p, ctypes.POINTER(_Rect))
    lib.GetClientRect.restype = ctypes.c_int
    lib.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
    lib.ShowWindow.restype = ctypes.c_int
    lib.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
    lib.SetForegroundWindow.restype = ctypes.c_int
    lib.PostMessageW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    )
    lib.PostMessageW.restype = ctypes.c_int
    lib.GetWindowThreadProcessId.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    )
    lib.GetWindowThreadProcessId.restype = ctypes.c_ulong
    lib.GetCursorPos.argtypes = (ctypes.POINTER(_Point),)
    lib.GetCursorPos.restype = ctypes.c_int
    lib.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    lib.SetCursorPos.restype = ctypes.c_int
    lib.ClientToScreen.argtypes = (ctypes.c_void_p, ctypes.POINTER(_Point))
    lib.ClientToScreen.restype = ctypes.c_int
    lib.WindowFromPoint.argtypes = (_Point,)
    lib.WindowFromPoint.restype = ctypes.c_void_p
    lib.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    lib.GetAncestor.restype = ctypes.c_void_p
    lib.AttachThreadInput.argtypes = (ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int)
    lib.AttachThreadInput.restype = ctypes.c_int
    lib.BringWindowToTop.argtypes = (ctypes.c_void_p,)
    lib.BringWindowToTop.restype = ctypes.c_int
    lib.GetForegroundWindow.argtypes = ()
    lib.GetForegroundWindow.restype = ctypes.c_void_p
    lib.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(_Input), ctypes.c_int)
    lib.SendInput.restype = ctypes.c_uint

    _user32 = lib
    return _user32


def _kernel32() -> Any:
    """Return ``kernel32``, for process handles."""
    global _kernel32_lib
    if _kernel32_lib is not None:
        return _kernel32_lib

    lib = _WinDLL("kernel32", use_last_error=True)
    lib.GetCurrentProcess.argtypes = ()
    lib.GetCurrentProcess.restype = ctypes.c_void_p
    lib.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    lib.OpenProcess.restype = ctypes.c_void_p
    lib.CloseHandle.argtypes = (ctypes.c_void_p,)
    lib.CloseHandle.restype = ctypes.c_int
    lib.GetCurrentThreadId.argtypes = ()
    lib.GetCurrentThreadId.restype = ctypes.c_ulong

    _kernel32_lib = lib
    return _kernel32_lib


def _advapi32() -> Any:
    """Return ``advapi32``, for reading token integrity levels."""
    global _advapi32_lib
    if _advapi32_lib is not None:
        return _advapi32_lib

    lib = _WinDLL("advapi32", use_last_error=True)
    lib.OpenProcessToken.argtypes = (
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_void_p),
    )
    lib.OpenProcessToken.restype = ctypes.c_int
    lib.GetTokenInformation.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    )
    lib.GetTokenInformation.restype = ctypes.c_int
    lib.GetSidSubAuthorityCount.argtypes = (ctypes.c_void_p,)
    lib.GetSidSubAuthorityCount.restype = ctypes.c_void_p
    lib.GetSidSubAuthority.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    lib.GetSidSubAuthority.restype = ctypes.c_void_p

    _advapi32_lib = lib
    return _advapi32_lib


def _describe(lib: Any, hwnd: int) -> WindowInfo | None:
    """Read one window: title, class and client size. ``None`` if it is gone."""
    if not lib.IsWindow(hwnd):
        return None

    title = ctypes.create_unicode_buffer(512)
    lib.GetWindowTextW(hwnd, title, len(title))
    class_name = ctypes.create_unicode_buffer(256)
    lib.GetClassNameW(hwnd, class_name, len(class_name))
    rect = _Rect()
    lib.GetClientRect(hwnd, ctypes.byref(rect))

    return WindowInfo(
        hwnd=hwnd,
        title=title.value,
        class_name=class_name.value,
        minimized=bool(lib.IsIconic(hwnd)),
        client_width=rect.right - rect.left,
        client_height=rect.bottom - rect.top,
    )


def _token_integrity(process: int) -> int | None:
    """Read a process handle's integrity level (last sub-authority of the
    token's mandatory-label SID: 0x1000 low ... 0x4000 system), or ``None``
    if opaque."""
    advapi32, kernel32 = _advapi32(), _kernel32()
    token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        size = ctypes.c_ulong(0)
        # First call sizes the buffer and is expected to fail.
        advapi32.GetTokenInformation(
            token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size)
        )
        if size.value == 0:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, TOKEN_INTEGRITY_LEVEL, buffer, size, ctypes.byref(size)
        ):
            return None
        # TOKEN_MANDATORY_LABEL opens with a SID_AND_ATTRIBUTES, whose first
        # member is the PSID we want.
        sid = ctypes.c_void_p.from_buffer(buffer).value
        if sid is None:
            return None
        count = ctypes.cast(
            advapi32.GetSidSubAuthorityCount(sid), ctypes.POINTER(ctypes.c_ubyte)
        ).contents.value
        if count == 0:
            return None
        return int(
            ctypes.cast(
                advapi32.GetSidSubAuthority(sid, count - 1),
                ctypes.POINTER(ctypes.c_ulong),
            ).contents.value
        )
    finally:
        kernel32.CloseHandle(token)


def _own_integrity() -> int | None:
    return _token_integrity(_kernel32().GetCurrentProcess())


def _window_integrity(hwnd: int) -> int | None:
    """Integrity level of the process owning ``hwnd``, or ``None`` if opaque."""
    pid = ctypes.c_ulong(0)
    _lib().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    process = _kernel32().OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if not process:
        return None
    try:
        return _token_integrity(process)
    finally:
        _kernel32().CloseHandle(process)


def _lparam(x: int, y: int) -> int:
    """Pack a client-area point into a mouse message's ``lParam``."""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _post(hwnd: int, message: int, wparam: int, x: int, y: int) -> None:
    lib = _lib()
    ctypes.set_last_error(0)
    if lib.PostMessageW(hwnd, message, wparam, _lparam(x, y)):
        return
    code = ctypes.get_last_error()
    detail = f"PostMessageW(0x{message:04X}) failed for window 0x{hwnd:X}"
    if code == ERROR_ACCESS_DENIED:
        raise WindowAccessDenied(code, detail)
    raise OSError(code, detail)


# --- public API -----------------------------------------------------------


def is_supported() -> bool:
    """Whether this process can reach ``user32`` at all."""
    return _IS_WINDOWS and _WinDLL is not None and _WINFUNCTYPE is not None


def find_window(*, title: str, class_name: str | None = None) -> WindowInfo | None:
    """Find a top-level window by title, optionally pinned to a window class.
    An exact (case-insensitive) title match wins; a substring match is the
    fallback, for a panel that appends a suffix to its caption."""
    lib = _lib()
    wanted = title.casefold()
    exact: WindowInfo | None = None
    partial: WindowInfo | None = None

    def visit(hwnd: int, _unused: int) -> int:
        nonlocal exact, partial
        info = _describe(lib, hwnd)
        if info is None or not info.title:
            return _CONTINUE
        if class_name is not None and info.class_name != class_name:
            return _CONTINUE
        found = info.title.casefold()
        if found == wanted:
            exact = info
            return _STOP
        if partial is None and wanted in found:
            partial = info
        return _CONTINUE

    # EnumWindows reports FALSE when the callback stopped it early, which is the
    # success path here, so its return value is deliberately ignored.
    lib.EnumWindows(_proc_type()(visit), 0)
    return exact or partial


def describe(hwnd: int) -> WindowInfo | None:
    """Re-read a window by handle. ``None`` once the window has been destroyed."""
    return _describe(_lib(), hwnd)


def can_post(hwnd: int) -> bool:
    """Whether this process is allowed to send the window input. Compares
    integrity levels rather than probing with a real message, since UIPI lets
    ``WM_NULL`` through while dropping the mouse messages a press is made of.
    Unknown answers are optimistic -- an uninspectable process reports true and
    lets the actual call fail instead."""
    theirs = _window_integrity(hwnd)
    if theirs is None:
        return True
    ours = _own_integrity()
    return ours is None or ours >= theirs


def restore(hwnd: int) -> bool:
    """Un-minimize a window without activating it. Returns whether Windows
    accepted the request -- it refuses for a higher-integrity process."""
    return bool(_lib().ShowWindow(hwnd, SW_SHOWNOACTIVATE))


def focus(hwnd: int) -> bool:
    """Bring a window to the foreground. Best effort -- Windows can refuse a
    foreground change from a process that doesn't already own it."""
    return bool(_lib().SetForegroundWindow(hwnd))


def post_mouse_move(hwnd: int, x: int, y: int) -> None:
    """Tell a window the pointer is over ``(x, y)`` without moving the cursor."""
    _post(hwnd, WM_MOUSEMOVE, 0, x, y)


def post_left_down(hwnd: int, x: int, y: int) -> None:
    """Press the left button at ``(x, y)`` in client coordinates."""
    _post(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, x, y)


def post_left_up(hwnd: int, x: int, y: int) -> None:
    """Release the left button at ``(x, y)`` in client coordinates."""
    _post(hwnd, WM_LBUTTONUP, 0, x, y)


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """A client-area point of ``hwnd``, in screen coordinates -- what
    ``set_cursor_pos``/``window_at`` need, since a click target only knows
    the client-fraction point ``to_point`` resolved."""
    point = _Point(x, y)
    _lib().ClientToScreen(hwnd, ctypes.byref(point))
    return point.x, point.y


def get_cursor_pos() -> tuple[int, int]:
    """The real cursor's current screen position, to restore after a click."""
    point = _Point()
    _lib().GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def set_cursor_pos(x: int, y: int) -> None:
    """Move the real cursor to a screen point. ``SendInput`` reads this
    position rather than a message's coordinates, so a Unity window -- unlike
    the i-deck's SDL one -- only sees a click here."""
    if not _lib().SetCursorPos(x, y):
        code = ctypes.get_last_error()
        raise OSError(
            code,
            f"SetCursorPos({x}, {y}) failed -- is the session locked, or a "
            "screensaver running? Either owns the input desktop.",
        )


def window_at(x: int, y: int) -> int:
    """The top-level window under a screen point, or ``0`` if none. Only
    meaningful before an injected click, which lands on whatever is topmost
    there -- a posted message ignores z-order entirely, so ``post_*`` never
    needed this."""
    lib = _lib()
    hwnd = lib.WindowFromPoint(_Point(x, y)) or 0
    if not hwnd:
        return 0
    return lib.GetAncestor(hwnd, GA_ROOT) or hwnd


def bring_to_front(hwnd: int) -> bool:
    """Raise ``hwnd`` and give it the foreground. Returns whether it actually
    landed there -- ``SetForegroundWindow`` is refused unless the caller
    already owns the foreground, hence attaching this thread's input queue to
    the target's first. The one function here that steals focus; needed only
    because ``inject_click`` follows the cursor/topmost window, not an HWND."""
    lib, kernel32 = _lib(), _kernel32()
    target_thread = lib.GetWindowThreadProcessId(hwnd, None)
    our_thread = kernel32.GetCurrentThreadId()
    attached = (
        bool(lib.AttachThreadInput(our_thread, target_thread, True))
        if target_thread != our_thread
        else False
    )
    try:
        lib.BringWindowToTop(hwnd)
        lib.SetForegroundWindow(hwnd)
    finally:
        if attached:
            lib.AttachThreadInput(our_thread, target_thread, False)
    return bool(lib.GetForegroundWindow() == hwnd)


def _send_input(flags: int, what: str) -> None:
    lib = _lib()
    event = _Input(type=INPUT_MOUSE, mi=_MouseInput(0, 0, 0, flags, 0, None))
    ctypes.set_last_error(0)
    if lib.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1:
        return
    code = ctypes.get_last_error()
    detail = f"SendInput({what}) was blocked"
    if code == ERROR_ACCESS_DENIED:
        raise WindowAccessDenied(code, detail)
    raise OSError(code, detail)


def inject_left_down() -> None:
    """Press the left button as real hardware input, at the cursor's current
    position. Deliberately carries no coordinates of its own --
    ``MOUSEEVENTF_ABSOLUTE`` normalizes to a second coordinate space, so
    moving with ``set_cursor_pos`` first and injecting without movement keeps
    everything in the one space ``ClickTarget`` already speaks."""
    _send_input(MOUSEEVENTF_LEFTDOWN, "LEFTDOWN")


def inject_left_up() -> None:
    """Release the left button as real hardware input. A failure here is
    worse than the click failing outright -- the button can be left stuck
    down -- but there is nothing more this layer can do about it than say
    so; the caller's log already names the target and window."""
    _send_input(MOUSEEVENTF_LEFTUP, "LEFTUP")


def reset() -> None:
    """Drop the cached library handles. Used by tests."""
    global _user32, _kernel32_lib, _advapi32_lib, _enum_proc_type
    _user32 = None
    _kernel32_lib = None
    _advapi32_lib = None
    _enum_proc_type = None
