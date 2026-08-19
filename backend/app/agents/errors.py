"""Errors raised while constructing the optional agent runtime."""


class AgentConfigurationError(RuntimeError):
    """The agent settings cannot produce a safe, runnable runtime."""


class AgentDisabledError(AgentConfigurationError):
    """Agent execution was requested while AGENT_ENABLED is false."""
