"""Validated in-memory representation of one game's configuration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.utils.game_log import EventRule

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

    Passed through unvalidated beyond being an object. The shape is enforced
    where a region is actually used, by :func:`app.utils.image_roi.named_roi` --
    which is where a typo in a region name or in its numbers becomes a sentence
    saying which region it was and what was wrong with it. :mod:`app.services.ocr`
    is the consumer today.
    """

    ocr: Mapping[str, Mapping[str, Any]]
    """Per-region OCR option overrides, keyed by the region name in :attr:`roi`.

    Validated at load time by :func:`app.utils.ocr.parse_overrides` but not
    applied here: the values they override come from the environment, which is
    read when a region is actually read rather than when the config is parsed.
    A region with nothing to override needs no entry.
    """

    button_targets: Mapping[str, Any]
    """Named in-game click targets, as fractions of the frame."""

    event_rules: Sequence[EventRule]
    """Extra log-event rules this game declares, already compiled.

    Combined with the shipped defaults by
    :func:`app.utils.game_log.resolve_rules`, which puts these first so a game
    can override a default rather than only add to it.
    """

    disabled_events: Sequence[str]
    """Names of shipped rules this game does not want.

    The shipped defaults are already trimmed to visually distinct events, so
    this is for a game whose own timing makes one of them noisy in practice.
    """


def freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Expose parsed config mappings as immutable views."""
    return MappingProxyType(dict(values))
