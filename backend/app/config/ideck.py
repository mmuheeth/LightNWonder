"""Virtual OLED i-deck runtime settings.

The i-deck service also consumes per-game data from
:mod:`app.config.game_config`. This module contains only deployment-specific
settings: the panel window, its files, and the press/verification behavior.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

__all__ = ["IDeckSettings"]


class IDeckSettings(BaseSettings):
    """Runtime options for the virtual OLED panel integration."""

    # The emulated button deck for a game running in a simulator is served by
    # OledPanelSvc.exe as an SDL window. Presses are posted to that window as
    # mouse messages, so the physical cursor never moves.
    IDECK_WINDOW_TITLE: str = "Virtual OLED"
    IDECK_WINDOW_CLASS: str = "SDL_app"
    IDECK_PANEL_XML: Path = Path(
        r"C:\ssd\cabinet\deployment\cfg\ButtonPanel\virtual_oled.xml"
    )

    # The panel service's own log. Every press it accepts appears here as
    # "Button Pressed ID=<hex>", which is how a press is confirmed.
    IDECK_LOG_PATH: Path = Path(r"C:\logs\OledPanelSvc.log")

    # Selects <IDECK_GAME_CONFIG_DIR>/<IDECK_GAME>.json. Must be a bare name.
    IDECK_GAME: str = "HuffNPuffLink"
    # Game configs ship with the code, so this is anchored to the package, not
    # to the working directory. An override may be absolute, or relative to
    # the `app` package.
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

    @field_validator("IDECK_GAME")
    @classmethod
    def _bare_game_name(cls, value: str) -> str:
        """Keep the game name a bare filename.

        It is interpolated into a path, so a separator or a parent reference
        here would let the setting read a file anywhere on disk.
        """
        name = value.strip()
        if not name:
            raise ValueError("IDECK_GAME must not be empty")
        if Path(name).name != name or not name.strip("."):
            raise ValueError(
                "IDECK_GAME must be a bare name: no path separators, drive "
                f"letters or '..' (got {value!r})"
            )
        return name

    @property
    def ideck_panel_xml(self) -> Path:
        """Absolute path to the panel layout the OLED service renders from."""
        return self.IDECK_PANEL_XML.resolve()

    @property
    def ideck_log_path(self) -> Path:
        """Absolute path to the OLED service log used to confirm presses."""
        return self.IDECK_LOG_PATH.resolve()

    @property
    def ideck_game_config_path(self) -> Path:
        """Absolute path to the selected game's config file.

        A relative ``IDECK_GAME_CONFIG_DIR`` resolves against the ``app``
        package rather than the working directory, because these files are
        shipped with the code.
        """
        directory = self.IDECK_GAME_CONFIG_DIR
        if not directory.is_absolute():
            directory = PACKAGE_ROOT / directory
        return (directory / f"{self.IDECK_GAME}.json").resolve()
