"""Virtual OLED i-deck runtime settings.

The i-deck service also consumes per-game data from
:mod:`app.config.game_config`. This module contains only deployment-specific
settings: the panel window, its files, and the press/verification behavior.
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

    # The emulated button deck for a game running in a simulator is served by
    # OledPanelSvc.exe as an SDL window. Presses are posted to that window as
    # mouse messages, so the physical cursor never moves.
    IDECK_WINDOW_TITLE: str = "Virtual OLED"
    IDECK_WINDOW_CLASS: str = "SDL_app"
    # Which layout is a property of the cabinet, not of the game: every title
    # this drives runs on `virtual_oled`, and a game that needed another one
    # would need a differently-built deck, not a different config file. So the
    # name lives here, in the one setting that has to name the file anyway.
    IDECK_PANEL_XML: Path = Path(
        r"C:\ssd\cabinet\deployment\cfg\ButtonPanel\virtual_oled.xml"
    )

    # The panel service's own log. Every press it accepts appears here as
    # "Button Pressed ID=<hex>", which is how a press is confirmed.
    IDECK_LOG_PATH: Path = Path(r"C:\logs\OledPanelSvc.log")

    # Game configs ship with the code, so this is anchored to the package, not
    # to the working directory. An override may be absolute, or relative to
    # the `app` package. The selected filename is stored beside this directory
    # in `game_config/active_game.json`.
    IDECK_GAME_CONFIG_DIR: Path = PACKAGE_ROOT / "config" / "game_config" / "games"

    # Hold between button-down and button-up. A real press measures ~200ms in
    # the panel log; well short of that still registers.
    IDECK_PRESS_HOLD_SECONDS: float = 0.12
    # With verification off, a press reports success as soon as it is posted.
    IDECK_VERIFY_PRESSES: bool = True
    IDECK_VERIFY_TIMEOUT_SECONDS: float = 2.0
    # A minimized window has no client area to aim at, so it is restored first
    # -- without activating it, so focus is left alone.
    IDECK_RESTORE_IF_MINIMIZED: bool = True
    # SDL can swallow the first click on an unfocused window. When a press goes
    # unconfirmed, foreground the panel and try once more. Still no cursor move.
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
