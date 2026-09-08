"""Application runtime settings, composed from each integration's settings mixin. Access
via :func:`get_settings`, cached so ``.env`` parses once per process."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict

from app.config.agents import AgentSettings
from app.config.analyze_spin import AnalyzeSpinSettings
from app.config.event_capture import EventCaptureSettings
from app.config.frame import FrameSettings
from app.config.game_input import GameInputSettings
from app.config.ideck import PACKAGE_ROOT, IDeckSettings
from app.config.image_classifier import ImageClassifierSettings
from app.config.obs import ObsSettings
from app.config.ocr import OcrSettings
from app.config.paylines import PaylineSettings
from app.config.paytable import PaytableSettings

Environment = Literal["local", "development", "staging", "production"]

# ``NoDecode`` stops pydantic-settings from JSON-decoding the raw env value so
# our own validator can accept a plain comma-separated list.
CsvList = Annotated[list[str], NoDecode]

__all__ = [
    "PACKAGE_ROOT",
    "CsvList",
    "Environment",
    "Settings",
    "get_settings",
    "settings",
]


class Settings(
    AgentSettings,
    ObsSettings,
    IDeckSettings,
    EventCaptureSettings,
    GameInputSettings,
    OcrSettings,
    FrameSettings,
    PaylineSettings,
    PaytableSettings,
    AnalyzeSpinSettings,
    ImageClassifierSettings,
):
    """Complete runtime configuration for the API."""

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
    # The health endpoints deliberately live outside this prefix so that
    # probes never have to track it.
    API_PREFIX: str = "/api"
    DOCS_URL: str | None = "/docs"
    REDOC_URL: str | None = "/redoc"
    OPENAPI_URL: str | None = "/openapi.json"

    # --- Database ---------------------------------------------------------
    # Keep the actual credential in the ignored .env file or deployment secret
    # store. The application only opens a connection pool; schema management
    # and migrations are intentionally outside this configuration layer.
    DATABASE_URL: SecretStr | None = Field(
        default=None,
        description="PostgreSQL connection URL used by the application pool.",
    )

    # --- CORS -------------------------------------------------------------
    CORS_ORIGINS: CsvList = Field(
        default_factory=lambda: [
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ]
    )
    CORS_ALLOW_CREDENTIALS: bool = True

    # --- Observability ----------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False
    REQUEST_ID_HEADER: str = "X-Request-ID"

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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()
