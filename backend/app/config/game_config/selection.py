"""Persistence helpers for the manually selected game configuration."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.config.ideck import normalize_game_name

__all__ = [
    "ActiveGameSelectionError",
    "load_active_game",
    "save_active_game",
]


class ActiveGameSelectionError(ValueError):
    """The active-game selection file is missing or unusable."""


def load_active_game(path: Path) -> str:
    """Read and validate the selected game name from ``path``."""
    try:
        document: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ActiveGameSelectionError(
            f"Active game selection file not found: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ActiveGameSelectionError(
            f"Active game selection at {path} is not valid JSON: {exc}"
        ) from exc
    except OSError as exc:
        raise ActiveGameSelectionError(
            f"Could not read active game selection at {path}: {exc}"
        ) from exc

    if not isinstance(document, dict):
        raise ActiveGameSelectionError(
            f"Active game selection at {path} must be a JSON object"
        )

    value = document.get("game")
    if not isinstance(value, str):
        raise ActiveGameSelectionError(
            f"Active game selection at {path} must define a string 'game'"
        )

    try:
        return normalize_game_name(value, label="active game")
    except ValueError as exc:
        raise ActiveGameSelectionError(str(exc)) from exc


def save_active_game(path: Path, game: str) -> str:
    """Persist a validated game name atomically and return its canonical name."""
    try:
        name = normalize_game_name(game, label="active game")
    except ValueError as exc:
        raise ActiveGameSelectionError(str(exc)) from exc

    temporary = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps({"game": name}, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    except OSError as exc:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise ActiveGameSelectionError(
            f"Could not write active game selection at {path}: {exc}"
        ) from exc

    return name
