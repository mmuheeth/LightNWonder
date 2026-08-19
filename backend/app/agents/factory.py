"""Factories for production-ready LangChain agents."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agents.errors import AgentDisabledError
from app.agents.model import build_chat_model
from app.config.agents import AgentSettings

ToolLike = BaseTool | Callable[..., Any]

__all__ = ["build_agent", "default_run_config"]


def default_run_config(
    settings: AgentSettings, *, thread_id: str | None = None
) -> RunnableConfig:
    """Return safe execution defaults for ``invoke``/``stream`` calls."""
    config: RunnableConfig = {"recursion_limit": settings.AGENT_RECURSION_LIMIT}
    if thread_id:
        config["configurable"] = {"thread_id": thread_id}
    return config


def build_agent(
    settings: AgentSettings,
    *,
    tools: Sequence[ToolLike] = (),
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
    middleware: Sequence[AgentMiddleware] = (),
) -> CompiledStateGraph:
    """Build a LangChain v1 agent backed by the LangGraph runtime.

    The function only assembles the graph. Callers own invocation, thread IDs,
    and the checkpointer context, which keeps FastAPI request lifecycles and
    background workflows independently testable.
    """
    if not settings.AGENT_ENABLED:
        raise AgentDisabledError(
            "Agent execution is disabled; set AGENT_ENABLED=true after adding "
            "a real provider credential."
        )

    return create_agent(
        model=build_chat_model(settings),
        tools=list(tools),
        system_prompt=settings.AGENT_SYSTEM_PROMPT,
        middleware=list(middleware),
        name=settings.AGENT_NAME,
        checkpointer=checkpointer,
        store=store,
    )
