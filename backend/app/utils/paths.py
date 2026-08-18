"""Path handling for names that came from outside.

A caller-supplied filename is untrusted input. Joining one onto a directory
without checking is the whole of a path-traversal bug: ``..\\..\\Windows\\Temp``
joined to a capture directory is still a valid path, and the process would write
there quite happily.

:func:`resolve_within` is the one place that check lives, so every endpoint that
accepts a filename gets the same answer.
"""

from __future__ import annotations

from pathlib import Path


class UnsafeNameError(ValueError):
    """A supplied name is not a bare filename, or escapes its directory.

    Carries :attr:`reason` separately from the message so callers can put the
    short form in a field-level error detail and compose their own sentence
    around it.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def resolve_within(root: Path, name: str, *, default_suffix: str | None = None) -> Path:
    """Resolve a bare filename inside ``root``.

    Args:
        root: Directory the result must stay inside. Used as given, so pass an
            absolute path -- a relative one resolves against the working
            directory and the containment check would compare against a
            different root than the caller meant.
        name: The untrusted filename. Must be bare: no separators, no drive
            letter, no parent reference.
        default_suffix: Extension to add when ``name`` has none, without its dot.

    Returns:
        The absolute, resolved path.

    Raises:
        UnsafeNameError: if ``name`` is not a bare filename, or the resolved
            path would land outside ``root``.
    """
    candidate = Path(name)
    # `Path("..").name` is ".." rather than "", so an all-dots name clears the
    # first check and has to be rejected on its own.
    if candidate.name != name or not name.strip("."):
        raise UnsafeNameError("path separators, drive letters and '..' are not allowed")
    if default_suffix and not candidate.suffix:
        candidate = candidate.with_suffix(f".{default_suffix}")

    target = (root / candidate).resolve()
    if not target.is_relative_to(root):
        raise UnsafeNameError("the resolved path escapes the target directory")
    return target
