"""Tests for the Managed Agent provider."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from able.core.providers.managed_agent_provider import (
    BETA_HEADER,
    COST_PER_SESSION_HOUR,
    ManagedAgentProvider,
    ManagedAgentSession,
    SSE_MAX_RECONNECTS,
)
from able.core.providers.base import Message, Role, ProviderError


# ── ManagedAgentSession ─────────────────────────────────────────


def test_session_duration_hours():
    import time

    session = ManagedAgentSession(
        session_id="s-1", agent_id="a-1", started_at=time.time() - 3600
    )
    assert 0.99 <= session.duration_hours <= 1.01


def test_session_estimated_cost():
    import time

    session = ManagedAgentSession(
        session_id="s-1", agent_id="a-1", started_at=time.time() - 7200
    )
    expected = 2.0 * COST_PER_SESSION_HOUR
    assert abs(session.estimated_cost - expected) < 0.01


def test_session_idle_stopped_checks_type():
    session = ManagedAgentSession(session_id="s-1", agent_id="a-1")
    assert session.is_idle_stopped is False

    session.stop_reason = {"type": "idle"}
    assert session.is_idle_stopped is True

    # Bare string 'idle' should NOT trigger — must be in .type
    session.stop_reason = {"reason": "idle"}
    assert session.is_idle_stopped is False


def test_session_idle_stopped_other_types():
    session = ManagedAgentSession(session_id="s-1", agent_id="a-1")
    session.stop_reason = {"type": "completed"}
    assert session.is_idle_stopped is False


# ── ManagedAgentProvider init ────────────────────────────────────


def test_provider_name():
    provider = ManagedAgentProvider(api_key="test-key")
    assert provider.name == "managed_agent"


def test_provider_config():
    provider = ManagedAgentProvider(api_key="test-key", model="claude-sonnet-4-6")
    assert provider.config.model == "claude-sonnet-4-6"
    assert provider.config.cost_per_million_input == 0.0
    assert provider.config.cost_per_million_output == 0.0


def test_provider_count_tokens():
    provider = ManagedAgentProvider(api_key="test-key")
    tokens = provider.count_tokens("Hello world, this is a test.")
    assert tokens > 0
    assert isinstance(tokens, int)


def test_provider_beta_header():
    provider = ManagedAgentProvider(api_key="test-key")
    headers = provider._headers()
    assert headers["anthropic-beta"] == BETA_HEADER
    assert headers["x-api-key"] == "test-key"


# ── Custom tool registration ────────────────────────────────────


def test_add_custom_tool():
    provider = ManagedAgentProvider(api_key="test-key")
    assert len(provider._custom_tools) == 0

    provider.add_custom_tool(
        name="my_tool",
        description="A test tool",
        input_schema={"type": "object", "properties": {}},
        credential_env="MY_API_KEY",
    )
    assert len(provider._custom_tools) == 1
    assert provider._custom_tools[0]["name"] == "my_tool"
    assert provider._custom_tools[0]["_able_credential_env"] == "MY_API_KEY"


def test_add_custom_tool_without_credential():
    provider = ManagedAgentProvider(api_key="test-key")
    provider.add_custom_tool(
        name="no_cred",
        description="No credential needed",
        input_schema={"type": "object"},
    )
    assert "_able_credential_env" not in provider._custom_tools[0]


def test_custom_tools_in_constructor():
    tools = [{"type": "custom", "name": "pre-existing"}]
    provider = ManagedAgentProvider(api_key="test-key", custom_tools=tools)
    assert len(provider._custom_tools) == 1


# ── Session stats update ────────────────────────────────────────


def test_update_session_stats():
    provider = ManagedAgentProvider(api_key="test-key")
    session = ManagedAgentSession(session_id="s-1", agent_id="a-1")

    provider._update_session_stats(session, {"usage": {"input_tokens": 100, "output_tokens": 50}})
    assert session.total_input_tokens == 100
    assert session.total_output_tokens == 50

    provider._update_session_stats(session, {"usage": {"input_tokens": 200, "output_tokens": 100}})
    assert session.total_input_tokens == 300
    assert session.total_output_tokens == 150


def test_update_session_stats_no_usage():
    provider = ManagedAgentProvider(api_key="test-key")
    session = ManagedAgentSession(session_id="s-1", agent_id="a-1")

    provider._update_session_stats(session, {"type": "text"})
    assert session.total_input_tokens == 0
    assert session.total_output_tokens == 0


# ── Provider lazy import ─────────────────────────────────────────


def test_provider_importable_from_init():
    from able.core.providers import ManagedAgentProvider as P
    assert P is not None


# ── Provider registry wiring ─────────────────────────────────────


def test_registry_recognizes_managed_agent_type(monkeypatch):
    """Verify the provider registry can instantiate managed_agent type."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    from able.core.routing.provider_registry import ProviderRegistry, ProviderTierConfig

    config = ProviderTierConfig(
        name="managed-agent-test",
        tier=4,
        provider_type="managed_agent",
        endpoint="https://api.anthropic.com/v1",
        model_id="claude-sonnet-4-6",
        cost_per_m_input=0.0,
        cost_per_m_output=0.0,
        max_context=200000,
        supports_tools=True,
        supports_vision=True,
        throughput_tps=30,
        enabled=True,
        api_key_env="ANTHROPIC_API_KEY",
    )

    registry = ProviderRegistry.__new__(ProviderRegistry)
    provider = registry._instantiate_provider(config)
    assert provider is not None
    assert provider.name == "managed_agent"


# ── Constants ────────────────────────────────────────────────────


def test_constants():
    assert COST_PER_SESSION_HOUR == 0.08
    assert SSE_MAX_RECONNECTS == 5
    assert "managed-agents" in BETA_HEADER
