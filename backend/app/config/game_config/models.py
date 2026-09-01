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
    """The game's installed ``GameConfig`` directory, outside this repo, holding
    one folder per paytable named exactly as the log's ``paytableId``. ``None``
    when the game declares none, which is a real state -- every dashboard slice
    but the paytable one works without the game installed."""

    win_geometry_path: Path | None
    """The game's own ``winGeometry.xml``, which sits beside those folders and
    is shared by all of them. Read by :mod:`app.utils.win_geometry`."""

    symbols: Mapping[str, str]
    """Display name per symbol code, e.g. ``{"WC": "WILD", "AA": "Ox"}``.

    Configured because it cannot be read: the maths files carry two-letter codes
    and no display text in any element -- not in ``SymbolSetList``, not in
    ``ReelStripList`` -- so a code becomes readable here or nowhere. *Which*
    codes exist is still read from the maths; only what to call them is
    declared. A code with no entry keeps whatever the maths implies about it
    (``Wild``/``Scatter``) or stays a bare code."""

    wild_card_replacement: Sequence[str]
    """Symbol codes the wild (:data:`app.utils.paylines.WILD_SYMBOL`) stands in
    for when a payline is read, upper-cased and in the order declared.

    A closed list, not "anything": the codes left out of it are exactly the ones
    a wild must *not* be read as -- the scatters and feature symbols this family
    of games pays by counting anywhere on the grid rather than along a line. An
    empty list (or no block at all) means the game substitutes nothing, so its
    wild is compared by equality like any other symbol, which is what every
    config written before this block did.

    Declared rather than read for the same reason as :attr:`paylines`: the
    running game's ``math.xml`` does carry it (in ``WildSymbolList``), but
    :mod:`app.services.paylines` checks a screenshot on machines with no game
    installed. :class:`app.utils.paylines.WildRule` is what turns it into the
    substitution rule."""

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
