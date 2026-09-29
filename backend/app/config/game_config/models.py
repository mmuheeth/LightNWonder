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

    game_config_dir: Path | None
    """The game's installed ``GameConfig`` directory (outside this repo), holding
    one folder per paytable named as the log's ``paytableId``. ``None`` is a real
    state -- every slice but the paytable one works without the game installed."""

    win_geometry_path: Path | None
    """The game's own ``winGeometry.xml``, which sits beside those folders and
    is shared by all of them. Read by :mod:`app.utils.win_geometry`."""

    symbols: Mapping[str, str]
    """Display name per symbol code, e.g. ``{"WC": "WILD", "AA": "Ox"}``. Declared
    because it cannot be read -- the maths files carry no display text in any
    element -- so a code is named here or not at all; which codes *exist* still
    comes from the maths."""

    wild_card_replacement: Sequence[str]
    """Symbol codes the wild (:data:`app.utils.paylines.WILD_SYMBOL`) stands in
    for, upper-cased in declared order -- a **closed list** that deliberately
    excludes scatters/feature symbols (a wild beside one is not a match). Declared
    rather than read from ``math.xml``'s ``WildSymbolList`` because
    :mod:`app.services.paylines` also checks a screenshot with no game installed;
    an empty list substitutes nothing."""

    scatter_symbols: Sequence[str]
    """Symbol codes counted anywhere on the grid rather than along a line -- the
    complement of :attr:`wild_card_replacement`, declared for the same reason.
    Read by :mod:`app.services.analyze_spin`; a tile's prize number, if any, is
    not declared here since it's a property of the tile, not the code -- OCR
    reads it."""

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
    """Per-region OCR option overrides, keyed by region name in :attr:`roi`
    (and ``symbol_tile`` for the orb reader, which isn't in :attr:`roi`).
    Validated by :func:`app.utils.paddle_ocr.parse_overrides`."""

    button_targets: Mapping[str, Any]
    """Named in-game click targets, as fractions of the window client area --
    the same space :attr:`roi` is measured in."""

    gaf: Mapping[str, Any]
    """Optional GAF automation block: the Thrift endpoint, client wrapper and
    object-query dictionary that let :mod:`app.services.gaf` drive this game.
    Per-game because every value is a property of the game, not this machine;
    ``object_query_root`` points outside the repo like :attr:`game_config_dir` and,
    like it, is validated for shape only by :func:`app.config.gaf.resolve_target` --
    existence is checked later, when the files are actually read. Empty means the
    game can't be driven this way."""

    letterbox: Mapping[str, Any]
    """Optional per-game overrides for the three ``FRAME_LETTERBOX_*`` settings
    (``trim``, ``threshold``, ``min_fraction``); absent keys keep the environment's
    value. Per-game because content-box stability is a property of what the game
    *draws* -- a drifting cloud or fading border moves the box between frames, so
    trimming is turned off for that game alone. Resolved by
    :func:`app.services.roi.letterbox_options`."""

    meter: Mapping[str, Any]
    """Optional cash-meter overrides: ``band`` ([top, bottom] fraction of strip
    height), ``windows`` (per-field [low, high] width span), and ``ordinal`` (bool,
    files cash/win/bet by left-to-right position for a skin whose window title
    can't be trusted). Cash is always first, bet always last, win between and
    optional -- see :func:`app.utils.meter.assign_fields`."""

    event_rules: Sequence[EventRule]
    """Extra log-event rules this game declares, combined ahead of the shipped
    defaults by :func:`app.utils.game_log.resolve_rules`."""

    disabled_events: Sequence[str]
    """Names of shipped default rules this game disables."""


def freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Expose parsed config mappings as immutable views."""
    return MappingProxyType(dict(values))
