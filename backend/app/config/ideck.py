"""Virtual OLED i-deck runtime settings: the panel window, its files, and
press/verification behavior. Per-game data lives in app.config.game_config.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

__all__ = ["IDeckSettings", "normalize_game_name"]


def normalize_game_name(value: str, *, label: str = "game") -> str:
    """Validate the bare filename stem used to select a game config."""
    name = value.strip()
    if not name:
        raise ValueError(f"{label} must not be empty")
    if Path(name).name != name or not name.strip("."):
        raise ValueError(
            f"{label} must be a bare name: no path separators, drive letters "
            f"or '..' (got {value!r})"
        )
    return name


class IDeckSettings(BaseSettings):
    """Runtime options for the virtual OLED panel integration."""

    # OledPanelSvc.exe serves the deck as an SDL window; presses are posted as
    # mouse messages, so the physical cursor never moves.
    IDECK_WINDOW_TITLE: str = "Virtual OLED"
    IDECK_WINDOW_CLASS: str = "SDL_app"
    # Property of the cabinet, not the game: every title drives `virtual_oled`.
    IDECK_PANEL_XML: Path = Path(
        r"C:\ssd\cabinet\deployment\cfg\ButtonPanel\virtual_oled.xml"
    )

    # The panel service's own log; a press is confirmed by "Button Pressed ID=<hex>".
    IDECK_LOG_PATH: Path = Path(r"C:\logs\OledPanelSvc.log")

    # Anchored to the package (game configs ship with the code), not the cwd.
    # The active selection is stored beside this dir in `active_game.json`.
    IDECK_GAME_CONFIG_DIR: Path = PACKAGE_ROOT / "config" / "game_config" / "games"

    # A real press measures ~200ms in the panel log; well short of that still registers.
    IDECK_PRESS_HOLD_SECONDS: float = 0.12
    IDECK_VERIFY_PRESSES: bool = True
    IDECK_VERIFY_TIMEOUT_SECONDS: float = 2.0
    # A minimized window has no client area to aim at; restored without
    # activating, so focus is left alone.
    IDECK_RESTORE_IF_MINIMIZED: bool = True
    # SDL can swallow the first click on an unfocused window; on an unconfirmed
    # press, foreground the panel and retry once (still no cursor move).
    IDECK_FOCUS_ON_RETRY: bool = True

    @property
    def ideck_panel_xml(self) -> Path:
        """Absolute path to the panel layout the OLED service renders from."""
        return self.IDECK_PANEL_XML.resolve()

    @property
    def ideck_log_path(self) -> Path:
        """Absolute path to the OLED service log used to confirm presses."""
        return self.IDECK_LOG_PATH.resolve()

    @property
    def ideck_game_config_dir(self) -> Path:
        """Absolute directory containing the shipped game config files."""
        directory = self.IDECK_GAME_CONFIG_DIR
        if not directory.is_absolute():
            directory = PACKAGE_ROOT / directory
        return directory.resolve()

    @property
    def ideck_active_game_path(self) -> Path:
        """Absolute path to the persisted active-game selection file."""
        return (self.ideck_game_config_dir.parent / "active_game.json").resolve()

    @property
    def ideck_active_game(self) -> str:
        """Read the manually selected game name from the config directory."""
        # Imported lazily because the selection helper uses normalize_game_name
        # from this module, and importing it at module load time would cycle.
        from app.config.game_config.selection import load_active_game

        return load_active_game(self.ideck_active_game_path)

    def ideck_game_config_path_for(self, game: str) -> Path:
        """Absolute path to a named game config after validating its name."""
        name = normalize_game_name(game)
        return (self.ideck_game_config_dir / f"{name}.json").resolve()

    @property
    def ideck_game_config_path(self) -> Path:
        """Absolute path to the currently selected game's config file."""
        return self.ideck_game_config_path_for(self.ideck_active_game)
