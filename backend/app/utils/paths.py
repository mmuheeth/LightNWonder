"""Path handling for names that came from outside: guards against traversal, since
a relative path joined to a capture dir is still a valid path."""

from __future__ import annotations

from pathlib import Path


class UnsafeNameError(ValueError):
    """A supplied name is not a bare filename, or escapes its directory."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def resolve_within(root: Path, name: str, *, default_suffix: str | None = None) -> Path:
    """Resolve a bare filename inside ``root``. ``root`` must be absolute -- a relative
    one resolves against the working directory, not the caller's intended root."""
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


def resolve_subdirectory(root: Path, name: str) -> Path:
    """Resolve a caller-supplied relative directory (e.g. ``session-01/run``)
    below ``root``, rejecting absolute, drive-qualified, or escaping paths."""
    value = name.strip()
    if not value:
        raise UnsafeNameError("the output directory must not be empty")

    candidate = Path(value)
    if candidate.is_absolute() or candidate.anchor:
        raise UnsafeNameError(
            "the output directory must be relative to the configured capture root"
        )

    resolved_root = root.resolve()
    target = (resolved_root / candidate).resolve()
    if not target.is_relative_to(resolved_root):
        raise UnsafeNameError("the output directory escapes the configured root")
    return target
