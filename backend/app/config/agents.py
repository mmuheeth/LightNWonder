"""Runtime settings for LangChain agents and LangGraph workflows.

The application deliberately keeps these settings provider-neutral. A model is
selected with ``provider:model`` (for example ``openai:gpt-4o-mini`` or
``anthropic:claude-sonnet-4-6``), while provider-specific adapters remain
replaceable packages. Agent execution is disabled by default so a development
environment containing placeholder credentials can boot safely.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

AgentCheckpointBackend = Literal["memory", "postgres"]

_PLACEHOLDER_MARKERS = (
    "dummy",
    "change-me",
    "replace-me",
    "replace_with",
    "replace-with",
    "your-key",
    "your_key",
    "example",
    "not-a-real",
)

__all__ = [
    "AgentCheckpointBackend",
    "AgentSettings",
    "is_placeholder_secret",
]


def is_placeholder_secret(value: SecretStr | None) -> bool:
    """Return whether a secret is absent or clearly a sample value.

    This prevents tracing or model factories from forwarding the dummy values
    in ``.env.example`` to a provider. It is intentionally conservative: a
    value that does not look like a placeholder is treated as user-supplied.
    """

    if value is None:
        return True
    raw = value.get_secret_value().strip().lower()
    return not raw or any(marker in raw for marker in _PLACEHOLDER_MARKERS)


class AgentSettings(BaseSettings):
    """Configuration shared by agents, graphs, tools, and tracing."""

    # --- Model ------------------------------------------------------------
    AGENT_ENABLED: bool = False
    AGENT_NAME: str = "lightnwonder_agent"
    AGENT_MODEL: str = "openai:gpt-4o-mini"
    # Optional when AGENT_MODEL already includes ``provider:``.
    AGENT_PROVIDER: str | None = None
    # ``api_key`` works for OpenAI and Anthropic. Providers with a different
    # constructor name can set this to e.g. ``google_api_key``.
    AGENT_API_KEY: SecretStr | None = None
    AGENT_API_KEY_PARAM: str = "api_key"
    AGENT_BASE_URL: str | None = None
    AGENT_TEMPERATURE: float = Field(default=0.0, ge=0.0, le=2.0)
    AGENT_MAX_TOKENS: int = Field(default=2048, ge=1, le=32768)
    AGENT_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0.0, le=3600.0)
    AGENT_MAX_RETRIES: int = Field(default=2, ge=0, le=10)
    AGENT_RECURSION_LIMIT: int = Field(default=50, ge=1, le=1000)
    AGENT_SYSTEM_PROMPT: str = (
        "You are a reliable assistant. Use available tools deliberately, "
        "explain uncertainty, and never invent tool results."
    )

    # --- State and durability --------------------------------------------
    AGENT_CHECKPOINT_BACKEND: AgentCheckpointBackend = "memory"
    # PostgreSQL is used when the backend is ``postgres``. If empty, the
    # application-level DATABASE_URL is used by the checkpoint helper.
    AGENT_CHECKPOINT_URL: SecretStr | None = None
    AGENT_CHECKPOINT_SETUP: bool = False

    # --- LangSmith observability -----------------------------------------
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"
    LANGCHAIN_API_KEY: SecretStr | None = None
    LANGCHAIN_PROJECT: str = "lightnwonder-local"

    @field_validator(
        "AGENT_API_KEY",
        "AGENT_BASE_URL",
        "AGENT_CHECKPOINT_URL",
        "LANGCHAIN_API_KEY",
        mode="before",
    )
    @classmethod
    def _empty_values_are_none(cls, value: object) -> object:
        """Treat blank dotenv values as unset optional values."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("AGENT_NAME", "AGENT_MODEL", "AGENT_SYSTEM_PROMPT")
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("AGENT_PROVIDER")
    @classmethod
    def _normalise_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        return value or None

    @field_validator("AGENT_API_KEY_PARAM")
    @classmethod
    def _valid_api_key_parameter(cls, value: str) -> str:
        value = value.strip()
        if not value.isidentifier():
            raise ValueError("AGENT_API_KEY_PARAM must be a valid identifier")
        return value

    @property
    def agent_is_configured(self) -> bool:
        """Whether model construction has a non-placeholder credential."""
        return not is_placeholder_secret(self.AGENT_API_KEY)
