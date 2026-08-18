"""Application settings.

Values are read from the environment (and from a local ``.env`` file during
development). Access them through :func:`get_settings`, which is cached so the
environment is parsed exactly once per process.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "development", "staging", "production"]

# The ``app`` package itself. Data that ships with the code is addressed from
# here rather than from the working directory, so the app runs the same whether
# it was started from `backend/`, from the repo root, or by a service manager
# with a working directory of its own.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# ``NoDecode`` stops pydantic-settings from JSON-decoding the raw env value so
# our own validator can accept a plain comma-separated list.
CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    """Runtime configuration for the API."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ------------------------------------------------------
    APP_NAME: str = "LightNWonder API"
    APP_DESCRIPTION: str = "LightNWonder backend service."
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Environment = "local"
    DEBUG: bool = True

    # --- Server -----------------------------------------------------------
    HOST: str = "0.0.0.0"
    PORT: int = 8001
    RELOAD: bool = True

    # --- API --------------------------------------------------------------
    # The health endpoints deliberately live outside this prefix so that probes
    # never have to track it.
    API_PREFIX: str = "/api"
    DOCS_URL: str | None = "/docs"
    REDOC_URL: str | None = "/redoc"
    OPENAPI_URL: str | None = "/openapi.json"

    # --- CORS -------------------------------------------------------------
    CORS_ORIGINS: CsvList = ["http://localhost:3001", "http://127.0.0.1:3001"]
    CORS_ALLOW_CREDENTIALS: bool = True

    # --- Observability ----------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False
    REQUEST_ID_HEADER: str = "X-Request-ID"

    # --- OBS Studio -------------------------------------------------------
    # obs-websocket v5, bundled with OBS Studio 28+. Enable the server under
    # Tools > WebSocket Server Settings. Paths below are resolved on the *OBS*
    # host, which is the same machine as the backend unless OBS_HOST is remote.
    OBS_HOST: str = "127.0.0.1"
    OBS_PORT: int = 4455
    OBS_PASSWORD: SecretStr = SecretStr("")
    OBS_AUTO_CONNECT: bool = False
    OBS_CONNECT_TIMEOUT_SECONDS: float = 5.0
    OBS_REQUEST_TIMEOUT_SECONDS: float = 10.0
    # Screenshots -- and recordings, when OBS_SET_RECORD_DIRECTORY is on -- are
    # written here. Relative paths resolve against the working directory, which
    # for this project is always `backend/`.
    OBS_CAPTURE_DIR: Path = Path("OBS-capture")
    OBS_SET_RECORD_DIRECTORY: bool = True

    # --- Virtual OLED i-deck ----------------------------------------------
    # The emulated button deck for a game running in a simulator, served by
    # OledPanelSvc.exe as an SDL window. Presses are posted to that window as
    # mouse messages, so the physical cursor never moves. Button geometry is
    # read from the panel's own layout file rather than restated here; the
    # friendly names that map onto it live in the per-game config.
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
    # to the working directory. An override may be absolute, or relative to the
    # `app` package.
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

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_csv_list(cls, value: object) -> object:
        """Accept either a JSON array or a comma-separated string."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                import json

                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

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

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        level = value.upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return level

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def docs_url(self) -> str | None:
        """Hide the interactive docs in production."""
        return None if self.is_production else self.DOCS_URL

    @property
    def redoc_url(self) -> str | None:
        return None if self.is_production else self.REDOC_URL

    @property
    def openapi_url(self) -> str | None:
        return None if self.is_production else self.OPENAPI_URL

    @property
    def obs_url(self) -> str:
        """WebSocket URL of the obs-websocket server."""
        return f"ws://{self.OBS_HOST}:{self.OBS_PORT}"

    @property
    def obs_capture_dir(self) -> Path:
        """Absolute capture directory; OBS rejects relative output paths."""
        return self.OBS_CAPTURE_DIR.resolve()

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

        ``IDECK_GAME`` is validated to be a bare name, so this cannot be walked
        out of the config directory.
        """
        directory = self.IDECK_GAME_CONFIG_DIR
        if not directory.is_absolute():
            directory = PACKAGE_ROOT / directory
        return (directory / f"{self.IDECK_GAME}.json").resolve()


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()
