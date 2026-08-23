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
    """Optional OBS window-capture source name; omit to auto-discover it in
    the active scene (set only to disambiguate multiple sources)."""

    log_path: Path | None
    """The game's own log, when it declares one."""

    roi: Mapping[str, Any]
    """Named screen regions, as fractions of the game's content box (not the
    OBS canvas -- see :mod:`app.utils.letterbox`). Shape enforced at use by
    :func:`app.utils.image_roi.named_roi`."""

    reel_bounds: Mapping[str, Any]
    """Where reel strips/rows sit inside the ``roi.reels`` crop -- fractions of
    that crop, not of the game. Shape enforced by
    :meth:`app.utils.reel_grid.ReelGrid.from_mapping`."""

    paylines: Mapping[str, Any]
    """Winning patterns keyed by bet configuration then line number, e.g.
    ``{"5": {"1": [[2,1],[2,2],...]}}`` with ``[row, column]`` 1-indexed
    against :attr:`reel_bounds`. Shape enforced by :func:`app.utils.paylines.read_set`."""

    ocr: Mapping[str, Mapping[str, Any]]
    """Per-region OCR option overrides, keyed by region name in :attr:`roi`.
    Validated by :func:`app.utils.ocr.parse_overrides`."""

    button_targets: Mapping[str, Any]
    """Named in-game click targets, as fractions of the window client area --
    the same space :attr:`roi` is measured in."""

    meter: Mapping[str, Any]
    """Optional cash-meter overrides: ``band`` is ``[top, bottom]`` fraction of
    strip height, ``windows`` maps a field to its ``[low, high]`` width span.
    Defaults come from :mod:`app.services.meter` / :data:`app.utils.meter.DEFAULT_WINDOWS`."""

    event_rules: Sequence[EventRule]
    """Extra log-event rules this game declares, combined ahead of the shipped
    defaults by :func:`app.utils.game_log.resolve_rules`."""

    disabled_events: Sequence[str]
    """Names of shipped default rules this game disables."""


def freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Expose parsed config mappings as immutable views."""
    return MappingProxyType(dict(values))
