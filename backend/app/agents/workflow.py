"""Small, typed helpers for composing custom LangGraph workflows."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.config.agents import AgentSettings

__all__ = ["WorkflowState", "compile_workflow", "new_workflow"]


class WorkflowState(MessagesState, total=False):
    """Common state extension for application workflows; extend with a richer TypedDict as needed."""

    run_id: str
    status: str
    metadata: dict[str, str]


def new_workflow() -> StateGraph[WorkflowState]:
    """Return an empty workflow builder ready for nodes and edges."""
    return StateGraph(WorkflowState)


def compile_workflow(
    builder: StateGraph[WorkflowState],
    settings: AgentSettings,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph:
    """Compile a workflow with shared durability and recursion safeguards."""
    compile_kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    if store is not None:
        compile_kwargs["store"] = store
    # ``recursion_limit`` belongs to invocation config, not compile() — see default_run_config.
    del settings
    return builder.compile(**compile_kwargs)
