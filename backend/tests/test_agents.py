"""Agent and workflow configuration stays safe and provider-neutral."""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START
from pydantic import SecretStr

from app.agents import (
    AgentConfigurationError,
    AgentDisabledError,
    build_agent,
    checkpoint_context,
    compile_workflow,
    default_run_config,
    new_workflow,
)
from app.config.agents import AgentSettings, is_placeholder_secret


def test_agent_defaults_are_disabled_and_placeholder_safe() -> None:
    settings = AgentSettings()

    assert settings.AGENT_ENABLED is False
    assert settings.AGENT_MODEL == "openai:gpt-4o-mini"
    assert settings.AGENT_CHECKPOINT_BACKEND == "memory"
    assert not settings.agent_is_configured
    assert is_placeholder_secret(SecretStr("dummy-replace-with-real-key"))


def test_agent_settings_accept_any_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODEL", "anthropic:claude-sonnet-4-6")
    monkeypatch.setenv("AGENT_API_KEY_PARAM", "api_key")
    monkeypatch.setenv("AGENT_API_KEY", "provider-secret")

    settings = AgentSettings(_env_file=None)

    assert settings.AGENT_MODEL == "anthropic:claude-sonnet-4-6"
    assert settings.AGENT_API_KEY is not None
    assert settings.agent_is_configured


def test_disabled_agent_fails_before_model_construction() -> None:
    with pytest.raises(AgentDisabledError):
        build_agent(AgentSettings())


def test_memory_checkpoint_context_is_ready_for_local_workflows() -> None:
    with checkpoint_context(AgentSettings()) as saver:
        assert isinstance(saver, InMemorySaver)


def test_postgres_checkpoint_requires_a_database_url() -> None:
    settings = AgentSettings(AGENT_CHECKPOINT_BACKEND="postgres")

    with (
        pytest.raises(AgentConfigurationError, match="requires AGENT_CHECKPOINT_URL"),
        checkpoint_context(settings),
    ):
        pass


def test_agent_factory_and_run_config_use_langgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AgentSettings(AGENT_ENABLED=True)
    fake_model = FakeListChatModel(responses=["test response"])
    monkeypatch.setattr("app.agents.factory.build_chat_model", lambda _: fake_model)

    agent = build_agent(settings, checkpointer=InMemorySaver())
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "hello"}]},
        default_run_config(settings, thread_id="test-thread"),
    )

    assert result["messages"][-1].content == "test response"


def test_custom_workflow_compiles_with_common_state() -> None:
    builder = new_workflow()
    builder.add_node("finish", lambda _: {"status": "done"})
    builder.add_edge(START, "finish")
    builder.add_edge("finish", END)

    graph = compile_workflow(builder, AgentSettings())
    result = graph.invoke({"messages": []})

    assert result["status"] == "done"
