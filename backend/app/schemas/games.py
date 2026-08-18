"""Game catalog and runtime-selection payloads."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.config.ideck import normalize_game_name


class GameOption(BaseModel):
    """One selectable game config exposed to the dashboard."""

    game: str = Field(description="Config filename stem used to select the game.")
    label: str = Field(description="Display name declared by the game config.")
    process: str | None = Field(
        default=None, description="Process name declared by the game config."
    )


class GameCatalog(BaseModel):
    """All available game configs and the currently selected one."""

    active_game: str = Field(description="Filename stem of the active game config.")
    games: list[GameOption] = Field(
        min_length=1, description="Selectable game configs, sorted by name."
    )


class SelectGameRequest(BaseModel):
    """Request to switch the in-memory active game."""

    game: str = Field(
        min_length=1,
        max_length=128,
        description="Filename stem of a game config in the configured game directory.",
    )

    @field_validator("game")
    @classmethod
    def _bare_game_name(cls, value: str) -> str:
        """Reject paths before they reach the config directory resolver."""
        return normalize_game_name(value)


class ActiveGame(BaseModel):
    """The game selected at runtime and the side effects attempted for it."""

    game: str = Field(description="Filename stem of the active game config.")
    label: str = Field(description="Display name declared by the game config.")
    process: str | None = Field(
        default=None, description="Process name declared by the game config."
    )
    obs_window_selected: bool | None = Field(
        default=None,
        description=(
            "Whether OBS was retargeted; null when OBS was disconnected during "
            "the switch."
        ),
    )
