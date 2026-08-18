"""JSON reader for the per-game configuration files."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.config.game_config.models import GameConfig, GameConfigError, freeze_mapping

__all__ = ["load_game_config"]


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
            object, or any block it declares has the wrong shape.
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

    process = document.get("process")
    if process is not None and not isinstance(process, str):
        raise GameConfigError(f"'process' in {path} must be a string")

    return GameConfig(
        name=name,
        path=path,
        process=process,
        ideck_aliases=freeze_mapping(
            {alias.casefold(): target for alias, target in aliases.items()}
        ),
        ideck_panel=panel,
        log_path=Path(log) if log else None,
        roi=freeze_mapping(_object(document.get("roi"), where=f"'roi' in {path}")),
        button_targets=freeze_mapping(
            _object(document.get("button_targets"), where=f"'button_targets' in {path}")
        ),
    )
