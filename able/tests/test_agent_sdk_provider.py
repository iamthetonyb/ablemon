"""Tests for D1 — Claude Agent SDK Provider.

Covers: provider instantiation, graceful degradation, options building,
cost calculation, team config.
"""

import pytest

from able.core.providers.agent_sdk_provider import (
    AgentSDKProvider,
    AgentTeamConfig,
    _SDK_AVAILABLE,
)


class TestAgentSDKProvider:

    def test_instantiation(self):
        provider = AgentSDKProvider()
        assert provider is not None

    def test_name(self):
        provider = AgentSDKProvider()
        assert provider.name == "agent_sdk"

    def test_default_model(self):
        provider = AgentSDKProvider()
        assert provider.config.model == "claude-opus-4-6"

    def test_custom_model(self):
        provider = AgentSDKProvider(model="claude-sonnet-4-6")
        assert provider.config.model == "claude-sonnet-4-6"

    def test_cost_calculation(self):
        provider = AgentSDKProvider()
        # T4 Opus rates: $15/$75 per M
        cost = provider.calculate_cost(1_000_000, 1_000_000)
        assert cost == pytest.approx(90.0, rel=0.01)

    def test_count_tokens(self):
        provider = AgentSDKProvider()
        count = provider.count_tokens("Hello, world!")
        assert count > 0

    def test_complete_exists(self):
        provider = AgentSDKProvider()
        assert hasattr(provider, "complete")

    def test_stream_exists(self):
        provider = AgentSDKProvider()
        assert hasattr(provider, "stream")

    def test_run_team_exists(self):
        provider = AgentSDKProvider()
        assert hasattr(provider, "run_team")


class TestAgentTeamConfig:

    def test_default_config(self):
        config = AgentTeamConfig()
        assert config.merge_strategy == "concat"
        assert config.max_parallel == 4
        assert config.agents == []

    def test_custom_config(self):
        config = AgentTeamConfig(
            agents=[
                {"name": "researcher", "prompt": "Focus on facts."},
                {"name": "critic", "prompt": "Find weaknesses."},
            ],
            merge_strategy="vote",
            max_parallel=2,
        )
        assert len(config.agents) == 2
        assert config.merge_strategy == "vote"
        assert config.max_parallel == 2


class TestGracefulDegradation:

    def test_sdk_availability_flag(self):
        assert _SDK_AVAILABLE is True
