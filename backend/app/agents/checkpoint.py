"""Checkpoint lifecycle helpers for stateful LangGraph workflows."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from app.agents.errors import AgentConfigurationError
from app.config.agents import AgentSettings

__all__ = ["async_checkpoint_context", "checkpoint_context"]


def _plain_secret(value: SecretStr | str | None) -> str | None:
    if value is None:
        return None
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    raw = raw.strip()
    return raw or None


def _checkpoint_url(
    settings: AgentSettings, database_url: SecretStr | str | None
) -> str:
    url = _plain_secret(settings.AGENT_CHECKPOINT_URL) or _plain_secret(database_url)
    if not url:
        raise AgentConfigurationError(
            "AGENT_CHECKPOINT_BACKEND=postgres requires AGENT_CHECKPOINT_URL "
            "or DATABASE_URL."
        )
    return url


@contextmanager
def checkpoint_context(
    settings: AgentSettings,
    *,
    database_url: SecretStr | str | None = None,
) -> Iterator[BaseCheckpointSaver]:
    """Yield a sync checkpointer and close it when the graph is no longer used."""
    if settings.AGENT_CHECKPOINT_BACKEND == "memory":
        yield InMemorySaver()
        return

    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(
        _checkpoint_url(settings, database_url)
    ) as checkpointer:
        if settings.AGENT_CHECKPOINT_SETUP:
            checkpointer.setup()
        yield checkpointer


@asynccontextmanager
async def async_checkpoint_context(
    settings: AgentSettings,
    *,
    database_url: SecretStr | str | None = None,
) -> AsyncIterator[BaseCheckpointSaver]:
    """Yield an async checkpointer for FastAPI or other async runtimes."""
    if settings.AGENT_CHECKPOINT_BACKEND == "memory":
        yield InMemorySaver()
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(
        _checkpoint_url(settings, database_url)
    ) as checkpointer:
        if settings.AGENT_CHECKPOINT_SETUP:
            await checkpointer.setup()
        yield checkpointer
