"""LangChain model and LangSmith tracing factories."""

from __future__ import annotations

import os
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.config.agents import AgentSettings, is_placeholder_secret

__all__ = ["build_chat_model", "configure_tracing"]


def configure_tracing(settings: AgentSettings) -> None:
    """Configure LangSmith tracing without forwarding placeholder secrets."""
    if not settings.LANGCHAIN_TRACING_V2:
        return

    # LangChain v1 still reads the LANGCHAIN_* names. LANGSMITH_* aliases make
    # the intent explicit for newer SDKs without changing application code.
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_ENDPOINT"] = settings.LANGCHAIN_ENDPOINT
    os.environ["LANGSMITH_ENDPOINT"] = settings.LANGCHAIN_ENDPOINT
    os.environ["LANGCHAIN_PROJECT"] = settings.LANGCHAIN_PROJECT
    os.environ["LANGSMITH_PROJECT"] = settings.LANGCHAIN_PROJECT

    if not is_placeholder_secret(settings.LANGCHAIN_API_KEY):
        assert settings.LANGCHAIN_API_KEY is not None
        os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY.get_secret_value()


def build_chat_model(settings: AgentSettings) -> BaseChatModel:
    """Create the configured provider-neutral LangChain chat model.

    ``AGENT_MODEL`` accepts LangChain's ``provider:model`` notation. The
    provider adapter resolves the correct integration package, while the
    optional generic API key parameter handles providers that do not read a
    standard environment variable. No request is made by this function.
    """
    configure_tracing(settings)

    model_kwargs: dict[str, Any] = {
        "temperature": settings.AGENT_TEMPERATURE,
        "max_tokens": settings.AGENT_MAX_TOKENS,
        "timeout": settings.AGENT_TIMEOUT_SECONDS,
        "max_retries": settings.AGENT_MAX_RETRIES,
    }

    if settings.AGENT_PROVIDER:
        model_kwargs["model_provider"] = settings.AGENT_PROVIDER
    if settings.AGENT_BASE_URL:
        model_kwargs["base_url"] = settings.AGENT_BASE_URL
    if not is_placeholder_secret(settings.AGENT_API_KEY):
        assert settings.AGENT_API_KEY is not None
        model_kwargs[settings.AGENT_API_KEY_PARAM] = (
            settings.AGENT_API_KEY.get_secret_value()
        )

    return init_chat_model(settings.AGENT_MODEL, **model_kwargs)
