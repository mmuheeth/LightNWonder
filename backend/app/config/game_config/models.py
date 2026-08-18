"""Validated in-memory representation of one game's configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

__all__ = ["GameConfig", "GameConfigError", "freeze_mapping"]


class GameConfigError(ValueError):
    """The game config is missing, is not valid JSON, or has an unusable shape."""


@dataclass(frozen=True)
class GameConfig:
    """One parsed game config."""

    name: str
    """Game name as declared in the file, falling back to the filename."""

    path: Path
    """Where it was read from. Carried so errors can name the file."""

    process: str | None
    """Game process name, when the config declares one."""

    obs_window_source: str | None
    """Optional OBS window-capture source name for this game.

    When omitted, the OBS service discovers the window-capture source in the
    active program scene. Set this only when that scene contains more than one
    window-capture source and the config needs to disambiguate them.
    """

    ideck_aliases: Mapping[str, str]
    """Friendly name -> layout key mapping from the selected game.

    Keys are casefolded, so a lookup is case-insensitive without every caller
    remembering to fold. Values keep the layout's own casing.
    """

    ideck_panel: str | None
    """Layout the game expects, e.g. ``virtual_oled``. Informational."""

    log_path: Path | None
    """The game's own log, when it declares one."""

    roi: Mapping[str, Any]
    """Named screen regions, as fractions of the frame.

    Passed through unvalidated beyond being an object: nothing reads it yet,
    and guessing at a shape now would be a constraint invented rather than
    observed.
    """

    button_targets: Mapping[str, Any]
    """Named in-game click targets, as fractions of the frame."""


def freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Expose parsed config mappings as immutable views."""
    return MappingProxyType(dict(values))
