"""Minimal Win32 interop for driving another process's window.

The only ``ctypes`` in the codebase, deliberately confined to one module: the
``user32`` signatures are declared in exactly one place, and tests monkeypatch
these functions wholesale, so the suite never touches a real window and still
runs on a machine that is not Windows.

**Nothing here moves the physical cursor.** A press is delivered as window
messages, which the target handles as it would a real click while the user's
pointer stays where it is.

Every call is cheap and non-blocking. The ``post_*`` functions use
``PostMessageW`` rather than ``SendMessageW``: posting returns as soon as the
message is queued instead of blocking until the target pumps it, so an async
caller never stalls behind a busy window.
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
    """Windows refused the call because the target window outranks this process.

    User Interface Privilege Isolation blocks input sent from a lower integrity
    level to a higher one, so a backend started normally cannot drive a window
    owned by a process that was started elevated -- even as the same user. The
    fix is to run both at the same level, not to retry.
    """


class _Rect(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


@dataclass(frozen=True)
class WindowInfo:
    """A snapshot of one top-level window.

    ``client_width`` and ``client_height`` describe the drawable area, which is
    the coordinate space mouse messages are addressed in. Both read ``0`` while
    the window is minimized: Windows gives an iconic window no client area.
    """

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
    """Return ``user32``, declaring the signatures we use exactly once.

    Argument types are spelled out rather than left to ctypes' defaults, whose
    ``int`` conversion truncates 64-bit window handles.
    """
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
    """Read a process handle's integrity level, or ``None`` if it is opaque.

    The level is the last sub-authority of the token's mandatory-label SID:
    0x1000 low, 0x2000 medium, 0x3000 high, 0x4000 system.
    """
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
    fallback, so a panel that appends a suffix to its caption is still found.
    Supplying ``class_name`` disambiguates a title another process happens to
    share.
    """
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
    """Whether this process is allowed to send the window input.

    Compares integrity levels rather than probing with a real message: the
    comparison is a pure read, so it leaves nothing in the target's log, and it
    is accurate where a probe message is not -- UIPI lets ``WM_NULL`` through
    while dropping the mouse messages a press is made of.

    Unknown answers are optimistic. If the owning process cannot be inspected,
    this reports true and lets the actual call be the thing that fails, rather
    than blocking a press that might have worked.
    """
    theirs = _window_integrity(hwnd)
    if theirs is None:
        return True
    ours = _own_integrity()
    return ours is None or ours >= theirs


def restore(hwnd: int) -> bool:
    """Un-minimize a window without activating it.

    Returns whether Windows accepted the request; it refuses when the window
    belongs to a higher-integrity process.
    """
    return bool(_lib().ShowWindow(hwnd, SW_SHOWNOACTIVATE))


def focus(hwnd: int) -> bool:
    """Bring a window to the foreground.

    Best effort: Windows also refuses a foreground change requested by a process
    that does not already own the foreground, so a false return is a hint rather
    than a hard failure.
    """
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


def reset() -> None:
    """Drop the cached library handles. Used by tests."""
    global _user32, _kernel32_lib, _advapi32_lib, _enum_proc_type
    _user32 = None
    _kernel32_lib = None
    _advapi32_lib = None
    _enum_proc_type = None
