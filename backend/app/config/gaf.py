"""``GafSettings`` (the environment's defaults) and :func:`resolve_target`, which
folds one game's ``gaf`` block over them into an immutable, ready-to-dial target.

Nothing here talks to the network or the filesystem: :func:`resolve_target` only
validates shape and resolves the object-query paths, exactly as
:mod:`app.utils.click_target` and :mod:`app.utils.reel_grid` validate their own
config blocks. Whether a resolved path actually exists is
:mod:`app.services.gaf`'s question, asked once the files are about to be read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings

from app.utils import gaf_objects

__all__ = [
    "DEFAULT_GENERAL_QUERIES",
    "DEFAULT_GENERIC_QUERIES",
    "GAME_TYPES",
    "GafSettings",
    "GafTarget",
    "GafTargetError",
    "resolve_target",
]

# The standard AGTF layout, relative to a workspace's own `object_query_root`.
# Game-specific last in each group, as the merge order requires -- see
# GAF_AUTOMATION.md §4, measured against a live HuffNPuffHighRise workspace.
DEFAULT_GENERAL_QUERIES: tuple[str, ...] = (
    "GameCommon/JsonConfigFiles/GameObjectQueryFiles/BallyStyleThemeGameObjectQuery.json",
    "GameCommon/JsonConfigFiles/GameObjectQueryFiles/WagerSaverOneViewObjectQuery.json",
    "GameSpecific/JsonConfigFiles/GameObjectQueryFiles/BallyStyleThemeGameObjectQuery.json",
)
"""Passed to ``InitializeGameClient``."""

DEFAULT_GENERIC_QUERIES: tuple[str, ...] = (
    "GameCommon/JsonConfigFiles/GameObjectQueryFiles/GenericGameObjectQuery.json",
    "GameCommon/JsonConfigFiles/GameObjectQueryFiles/IDeckBetSliderObjectQuery.json",
    "GameCommon/JsonConfigFiles/GameObjectQueryFiles/ProgressiveObjectQuery.json",
    "GameCommon/JsonConfigFiles/GameObjectQueryFiles/GenericWagerSaverObjectQuery.json",
    "GameSpecific/JsonConfigFiles/GameObjectQueryFiles/GenericGameObjectQuery.json",
)
"""Passed to ``InitializeGenericGameClient``."""

GAME_TYPES: tuple[str, ...] = ("BallyStyle", "ShuffleStyle")
"""The only two client wrappers GAF knows how to select. Matched by name,
case-insensitively, so a typo is refused rather than silently picking the
wrong one and failing several calls later."""

_KNOWN_KEYS = frozenset(
    {
        "host",
        "port",
        "game_type",
        "gdk_version",
        "object_query_root",
        "take_win_button",
        "general_queries",
        "generic_queries",
    }
)


class GafTargetError(ValueError):
    """A game's ``gaf`` block is malformed, or names an unusable value."""


class GafSettings(BaseSettings):
    """Cabinet- and environment-level defaults for driving a game through GAF.

    Every one of these is overridable per game -- see :func:`resolve_target` --
    because none is a property of this machine: two games on one host can
    listen on two different ports.
    """

    # Where NRobot.Server.exe listens. Cabinet-level, unlike the game's own
    # Thrift endpoint below -- one NRobot server serves every game on this host.
    GAF_SERVER_URL: str = "http://127.0.0.1:8270"

    # Most keywords answer in ~90-130ms; a cold InitializeGameClient takes
    # ~2.4s. This ceiling exists so a wedged server cannot hang a worker
    # thread forever.
    GAF_REQUEST_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)

    # A freshly launched game takes a while to accept a client. AGTF's own
    # suites retry 12 times at 10s, far too long to leave a dashboard button
    # spinning, so these assume the game is already up.
    GAF_CONNECT_ATTEMPTS: int = Field(default=5, ge=1)
    GAF_CONNECT_RETRY_SECONDS: float = Field(default=2.0, ge=0)

    # Defaults for a game config whose `gaf` block omits them.
    GAF_HOST: str = "127.0.0.1"
    GAF_PORT: int = Field(default=9090, ge=1, le=65535)
    GAF_GAME_TYPE: str = "BallyStyle"
    # The AGTF common default is 10; HuffNPuffHighRise needs 12. The wrong
    # value silently selects a different client wrapper.
    GAF_GDK_VERSION: str = "12"
    GAF_TAKE_WIN_BUTTON: str = "TakeWinButton"

    # A win HOLDS the game in statePlaying until it is collected, so a settle
    # wait has to allow for the longest count-up this game can produce.
    GAF_SETTLE_TIMEOUT_SECONDS: float = Field(default=120.0, gt=0)
    GAF_SETTLE_POLL_SECONDS: float = Field(default=0.5, gt=0)

    # The press is acknowledged before the game has moved; this bounds the
    # wait for it to actually leave its pre-press state. A ceiling, not a
    # delay -- the wait ends the moment the game moves.
    GAF_SPIN_START_SECONDS: float = Field(default=5.0, gt=0)

    # Read the three meters after a spin and a collect. They come back as the
    # game's own strings, which is the one thing this reading has over OCR.
    GAF_READ_METERS: bool = True


@dataclass(frozen=True, slots=True)
class GafTarget:
    """One game's fully-resolved GAF endpoint, object dictionary and button.

    Everything a session needs to open and drive, with every path already
    made absolute -- :mod:`app.services.gaf` never re-derives any of it.
    """

    game: str
    host: str
    port: int
    game_type: str
    gdk_version: str
    take_win_button: str
    query_root: Path | None
    general_queries: tuple[Path, ...]
    generic_queries: tuple[Path, ...]

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def query_files(self) -> tuple[Path, ...]:
        """Every object-query file, general group first. Combined only for
        reporting -- the service always merges the two groups separately."""
        return (*self.general_queries, *self.generic_queries)


def _string(block: Mapping[str, Any], key: str, *, default: str) -> str:
    value = block.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise GafTargetError(f"'{key}' must be a non-empty string, got {value!r}")
    return value


def _port(block: Mapping[str, Any], *, default: int) -> int:
    value = block.get("port", default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GafTargetError(f"'port' must be an integer, got {value!r}")
    if not 1 <= value <= 65535:
        raise GafTargetError(f"'port' must be between 1 and 65535, got {value}")
    return value


def _game_type(block: Mapping[str, Any], *, default: str) -> str:
    value = block.get("game_type", default)
    if not isinstance(value, str):
        raise GafTargetError(f"'game_type' must be a string, got {value!r}")
    for candidate in GAME_TYPES:
        if value.lower() == candidate.lower():
            return candidate
    raise GafTargetError(
        f"'game_type' must be one of {', '.join(GAME_TYPES)}; got {value!r}"
    )


def _query_root(block: Mapping[str, Any]) -> Path | None:
    value = block.get("object_query_root")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GafTargetError(
            f"'object_query_root' must be a non-empty string, got {value!r}"
        )
    return Path(value)


def _query_list(
    block: Mapping[str, Any], key: str, *, default: Sequence[str]
) -> tuple[str, ...]:
    if key not in block:
        return tuple(default)
    value = block[key]
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise GafTargetError(f"'{key}' must be a list of file paths, got {value!r}")
    if not value:
        raise GafTargetError(f"'{key}' must not be an empty list")
    entries: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise GafTargetError(f"every entry in '{key}' must be a non-empty string")
        entries.append(entry)
    return tuple(entries)


def resolve_target(
    game: str, block: Mapping[str, Any], settings: GafSettings
) -> GafTarget:
    """Fold a game's ``gaf`` block over the environment's defaults.

    Every value but ``object_query_root`` falls back to a ``GafSettings``
    field; ``object_query_root`` has no sensible default because it names a
    Perforce workspace. Unknown keys are refused by name -- a misspelled
    ``prot`` would otherwise silently keep the default port and fail much
    later with no hint which key was wrong.
    """
    unknown = sorted(set(block) - _KNOWN_KEYS)
    if unknown:
        raise GafTargetError(
            f"{game}'s 'gaf' block has unrecognised key(s) {unknown}; known keys "
            f"are {sorted(_KNOWN_KEYS)}"
        )

    root = _query_root(block)
    general = _query_list(block, "general_queries", default=DEFAULT_GENERAL_QUERIES)
    generic = _query_list(block, "generic_queries", default=DEFAULT_GENERIC_QUERIES)
    try:
        general_paths = gaf_objects.resolve(root, general)
        generic_paths = gaf_objects.resolve(root, generic)
    except gaf_objects.ObjectQueryError as exc:
        raise GafTargetError(str(exc)) from exc

    return GafTarget(
        game=game,
        host=_string(block, "host", default=settings.GAF_HOST),
        port=_port(block, default=settings.GAF_PORT),
        game_type=_game_type(block, default=settings.GAF_GAME_TYPE),
        gdk_version=_string(block, "gdk_version", default=settings.GAF_GDK_VERSION),
        take_win_button=_string(
            block, "take_win_button", default=settings.GAF_TAKE_WIN_BUTTON
        ),
        query_root=root,
        general_queries=general_paths,
        generic_queries=generic_paths,
    )
