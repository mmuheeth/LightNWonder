"""Reader for the per-game config files in ``game_config/games/``.

One JSON file per game, named after it, holding everything that differs between
games::

    {
      "name": "HuffNPuffLink",
      "log": "C:\\\\logs\\\\Game\\\\HuffNPuffLink\\\\Logs\\\\HuffNPuffLink_Theme.log",
      "roi":            { "cash_meter": [0.138889, 0.755463, 0.869281, 0.782518] },
      "button_targets": { "take_win": [0.124, 0.917] },
      "ideck":          { "panel": "virtual_oled",
                          "aliases": { "spin": "Rebet", ... } }
    }

Each block serves a different consumer -- ``ideck`` names the deck's keys,
``roi`` and ``button_targets`` are screen regions expressed as fractions of the
frame, for reading meters and clicking in the game window itself. They share a
file because they describe one game, and they share this reader so a second
consumer does not grow a second, subtly different JSON parser.

Failures raise :class:`GameConfigError`, never an ``AppException``: this is a
file reader, and services decide what a bad config means for the HTTP contract.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

__all__ = ["GameConfig", "GameConfigError", "load_game_config"]


class GameConfigError(ValueError):
    """The game config is missing, is not valid JSON, or has an unusable shape."""


@dataclass(frozen=True)
class GameConfig:
    """One parsed game config."""

    name: str
    """Game name as declared in the file, falling back to the filename."""

    path: Path
    """Where it was read from. Carried so errors can name the file."""

    ideck_aliases: Mapping[str, str]
    """Friendly name -> layout key, e.g. ``spin`` -> ``Rebet``.

    Keys are casefolded, so a lookup is case-insensitive without every caller
    remembering to fold. Values keep the layout's own casing.
    """

    ideck_panel: str | None
    """Layout the game expects, e.g. ``virtual_oled``. Informational."""

    log_path: Path | None
    """The game's own log, when it declares one."""

    roi: Mapping[str, Any]
    """Named screen regions, as fractions of the frame.

    Passed through unvalidated beyond being an object: nothing reads it yet, and
    guessing at a shape now would be a constraint invented rather than observed.
    """

    button_targets: Mapping[str, Any]
    """Named in-game click targets, as fractions of the frame. See :attr:`roi`."""


def _object(raw: Any, *, where: str) -> Mapping[str, Any]:
    """Narrow a decoded JSON value to an object, or explain what it was."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise GameConfigError(f"{where} must be a JSON object")
    return raw


def _string_map(raw: Any, *, where: str) -> dict[str, str]:
    """Narrow a decoded JSON value to a flat string -> string map."""
    mapping = _object(raw, where=where)
    for key, value in mapping.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise GameConfigError(f"{where} must map strings to strings")
    return dict(mapping)


def load_game_config(path: Path) -> GameConfig:
    """Read and validate one game config.

    An absent ``ideck`` block is not an error -- keys can always be pressed by
    their layout name -- but an unreadable or malformed file is.

    Raises:
        GameConfigError: if the file is missing, is not valid JSON, is not an
            object, or any block it does declare has the wrong shape.
    """
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GameConfigError(f"No game config at {path}") from exc
    except json.JSONDecodeError as exc:
        raise GameConfigError(
            f"Game config at {path} is not valid JSON: {exc}"
        ) from exc
    except OSError as exc:
        raise GameConfigError(
            f"Could not read the game config at {path}: {exc}"
        ) from exc

    document = _object(raw, where=f"The game config at {path}")

    ideck = _object(document.get("ideck"), where=f"'ideck' in {path}")
    aliases = _string_map(ideck.get("aliases"), where=f"'ideck.aliases' in {path}")

    panel = ideck.get("panel")
    if panel is not None and not isinstance(panel, str):
        raise GameConfigError(f"'ideck.panel' in {path} must be a string")

    log = document.get("log")
    if log is not None and not isinstance(log, str):
        raise GameConfigError(f"'log' in {path} must be a string")

    name = document.get("name") or path.stem
    if not isinstance(name, str):
        raise GameConfigError(f"'name' in {path} must be a string")

    return GameConfig(
        name=name,
        path=path,
        ideck_aliases=MappingProxyType(
            {alias.casefold(): target for alias, target in aliases.items()}
        ),
        ideck_panel=panel,
        log_path=Path(log) if log else None,
        roi=MappingProxyType(
            dict(_object(document.get("roi"), where=f"'roi' in {path}"))
        ),
        button_targets=MappingProxyType(
            dict(
                _object(
                    document.get("button_targets"), where=f"'button_targets' in {path}"
                )
            )
        ),
    )
