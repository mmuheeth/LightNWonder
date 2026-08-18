"""Models and loader for the per-game static configuration files."""

from app.config.game_config.loader import load_game_config
from app.config.game_config.models import GameConfig, GameConfigError

__all__ = ["GameConfig", "GameConfigError", "load_game_config"]
