"""Runtime catalog and selection of the shipped game configurations."""

from __future__ import annotations

from pathlib import Path

from app.config.game_config import (
    ActiveGameSelectionError,
    GameConfig,
    GameConfigError,
    load_game_config,
    save_active_game,
)
from app.config.ideck import normalize_game_name
from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import GameConfigInvalidError, GameNotFoundError
from app.schemas.games import ActiveGame, GameCatalog, GameOption
from app.services import game_input as game_input_service

logger = get_logger("games")


def _config_path(game: str) -> tuple[str, Path]:
    """Resolve a selectable game name inside the configured game directory."""
    try:
        name = normalize_game_name(game)
    except ValueError as exc:
        raise GameNotFoundError(str(exc)) from exc

    path = settings.ideck_game_config_path_for(name)
    if not path.is_file():
        raise GameNotFoundError(f"No game config named {name!r} was found")
    return path.stem, path


def _load(path: Path) -> GameConfig:
    """Load one config and translate file errors to the API contract."""
    try:
        return load_game_config(path)
    except GameConfigError as exc:
        raise GameConfigInvalidError(
            f"Could not load game config {path}: {exc}"
        ) from exc


def _option(path: Path, config: GameConfig) -> GameOption:
    """Build the small public representation used by the dropdown."""
    return GameOption(game=path.stem, label=config.name, process=config.process)


def _active_game_name() -> str:
    """Read the manually selected game name from the game-config directory."""
    try:
        return settings.ideck_active_game
    except ActiveGameSelectionError as exc:
        raise GameConfigInvalidError(str(exc)) from exc


def catalog() -> GameCatalog:
    """List every valid game config in the configured directory."""
    directory = settings.ideck_game_config_dir
    paths = sorted(directory.glob("*.json"), key=lambda path: path.stem.casefold())
    if not paths:
        raise GameConfigInvalidError(f"No game configs found in {directory}")

    options = [_option(path, _load(path)) for path in paths]
    active_game = _active_game_name()
    if not any(option.game == active_game for option in options):
        raise GameNotFoundError(
            f"The active game {active_game!r} has no matching config in {directory}"
        )
    return GameCatalog(active_game=active_game, games=options)


def select(game: str) -> ActiveGame:
    """Persist the active game and drop every cached per-game metadata."""
    name, path = _config_path(game)
    config = _load(path)

    try:
        save_active_game(settings.ideck_active_game_path, name)
    except ActiveGameSelectionError as exc:
        raise GameConfigInvalidError(str(exc)) from exc

    # Layouts and window settings are deployment-level data; only the cached
    # per-game metadata needs dropping when the selector changes. Every service
    # that caches a GameConfig has to be told, or it keeps serving the old game.
    # i-deck is not one of them -- it addresses the deck by layout key, which is
    # the cabinet's, not the game's.
    game_input_service.reset_game_config()
    logger.info("Active game changed to %s", name)

    return ActiveGame(game=name, label=config.name, process=config.process)
