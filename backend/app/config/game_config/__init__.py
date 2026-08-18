"""Models, loader, and active-selection storage for game configuration."""

from app.config.game_config.loader import load_game_config
from app.config.game_config.models import GameConfig, GameConfigError
from app.config.game_config.selection import (
    ActiveGameSelectionError,
    load_active_game,
    save_active_game,
)

__all__ = [
    "ActiveGameSelectionError",
    "GameConfig",
    "GameConfigError",
    "load_active_game",
    "load_game_config",
    "save_active_game",
]
