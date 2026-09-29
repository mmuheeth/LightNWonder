"""The AGTF object-query files: friendly control name -> Unity GameObject path, read
from a Perforce workspace outside this repo and merged here, ignorant of what they are
for (:mod:`app.services.gaf` hands the merged result to the game client). They carry a
UTF-8 BOM (``utf-8-sig`` or the first byte raises); later files win the merge and the
game-specific pair must go last, since the common files supply the bulk and the
game-specific ones override a subset. The *general* and *generic* groups feed separate
client calls and never merge into each other."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ObjectQueryError",
    "ObjectQueryMissing",
    "QuerySet",
    "QuerySource",
    "load_query_set",
    "missing_files",
    "resolve",
]

ENCODING = "utf-8-sig"
"""These files ship with a BOM; plain ``utf-8`` throws on it."""


class ObjectQueryError(Exception):
    """An object-query file is unreadable, or is not a JSON object."""


class ObjectQueryMissing(ObjectQueryError):
    """One or more object-query files are not where the config says they are -- its own
    class because the fix differs: missing means the Perforce workspace isn't synced or
    the configured root is wrong, unreadable means the file itself is broken."""

    def __init__(self, missing: Sequence[Path]) -> None:
        self.missing = tuple(missing)
        listed = "\n  ".join(str(path) for path in self.missing)
        super().__init__(
            f"{len(self.missing)} object-query file(s) are missing:\n  {listed}"
        )


@dataclass(frozen=True, slots=True)
class QuerySource:
    """One file that contributed to a merged dictionary."""

    path: Path
    group: str
    """``"general"`` or ``"generic"`` -- which client call it feeds."""
    keys: int
    """How many named objects it declared."""


@dataclass(frozen=True, slots=True)
class QuerySet:
    """Both merged dictionaries, and a record of what went into them."""

    general: Mapping[str, Any]
    generic: Mapping[str, Any]
    sources: tuple[QuerySource, ...]

    def general_json(self) -> str:
        """The general dictionary as ``InitializeGameClient`` wants it."""
        return json.dumps(self.general)

    def generic_json(self) -> str:
        """The generic dictionary as ``InitializeGenericGameClient`` wants it."""
        return json.dumps(self.generic)

    def names(self) -> tuple[str, ...]:
        """Every control name either dictionary can resolve, sorted."""
        return tuple(sorted({*self.general, *self.generic}))


def resolve(root: Path | None, entries: Iterable[str | Path]) -> tuple[Path, ...]:
    """Turn configured file references into absolute paths. A relative entry resolves
    against ``root`` (the workspace root a config declares); an absolute one is left
    alone, so a single file can be pointed elsewhere without moving the rest."""
    resolved: list[Path] = []
    for entry in entries:
        path = Path(entry)
        if not path.is_absolute():
            if root is None:
                raise ObjectQueryError(
                    f"{entry!r} is a relative path but no object-query root is "
                    "configured to resolve it against"
                )
            path = root / path
        resolved.append(path)
    return tuple(resolved)


def missing_files(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Which of ``paths`` are not readable files, in order given -- split out from
    :func:`load_query_set` so a status poll gets the answer without parsing JSON."""
    return tuple(path for path in paths if not path.is_file())


def _read(path: Path, *, group: str) -> tuple[Mapping[str, Any], QuerySource]:
    """Read one object-query file into its dictionary."""
    try:
        raw: Any = json.loads(path.read_text(encoding=ENCODING))
    except OSError as exc:
        raise ObjectQueryError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ObjectQueryError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ObjectQueryError(
            f"{path} must be a JSON object mapping names to objects, not "
            f"{type(raw).__name__}"
        )
    return raw, QuerySource(path=path, group=group, keys=len(raw))


def _merge(
    paths: Sequence[Path], *, group: str
) -> tuple[dict[str, Any], list[QuerySource]]:
    """Merge one group of files in order; later keys win."""
    merged: dict[str, Any] = {}
    sources: list[QuerySource] = []
    for path in paths:
        entries, source = _read(path, group=group)
        merged.update(entries)
        sources.append(source)
    return merged, sources


def load_query_set(general: Sequence[Path], generic: Sequence[Path]) -> QuerySet:
    """Read and merge both groups. Every file must exist first -- a half-merged
    dictionary fails much later with a dictionary-key error naming nothing -- so
    missing files are collected and reported together rather than one per attempt."""
    absent = missing_files([*general, *generic])
    if absent:
        raise ObjectQueryMissing(absent)

    general_objects, general_sources = _merge(general, group="general")
    generic_objects, generic_sources = _merge(generic, group="generic")
    return QuerySet(
        general=general_objects,
        generic=generic_objects,
        sources=(*general_sources, *generic_sources),
    )
