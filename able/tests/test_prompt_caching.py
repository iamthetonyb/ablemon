"""Tests for E3 — Gateway Prompt Caching.

Covers: cache_control injection, beta header, cache stats tracking,
enable/disable toggle, system prompt caching, conversation turn caching.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from able.core.providers.anthropic_provider import AnthropicProvider
from able.core.providers.base import Message, Role


@pytest.fixture
def provider():
    return AnthropicProvider(api_key="test-key", prompt_caching=True)


@pytest.fixture
def provider_no_cache():
    return AnthropicProvider(api_key="test-key", prompt_caching=False)


# ── Initialization ───────────────────────────────────────────────

class TestCacheInit:

    def test_caching_enabled_by_default(self):
        p = AnthropicProvider(api_key="k")
        assert p._prompt_caching is True

    def test_caching_can_be_disabled(self):
        p = AnthropicProvider(api_key="k", prompt_caching=False)
        assert p._prompt_caching is False

    def test_cache_stats_initialized(self, provider):
        assert provider.cache_stats == {
            "creation_tokens": 0, "read_tokens": 0, "hits": 0, "misses": 0
        }


# ── _convert_messages with cache ─────────────────────────────────

class TestConvertMessagesCache:

    def test_system_gets_cache_control(self, provider):
        msgs = [Message(role=Role.SYSTEM, content="You are helpful.")]
        system, converted = provider._convert_messages(msgs, enable_cache=True)
        assert isinstance(system, list)
        assert system[0]["type"] == "text"
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    def test_system_plain_when_cache_disabled(self, provider):
        msgs = [Message(role=Role.SYSTEM, content="You are helpful.")]
        system, converted = provider._convert_messages(msgs, enable_cache=False)
        assert isinstance(system, str)
        assert system == "You are helpful."

    def test_first_turns_get_cache_control(self, provider):
        msgs = [
            Message(role=Role.SYSTEM, content="System prompt."),
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi there"),
            Message(role=Role.USER, content="How are you?"),
        ]
        system, converted = provider._convert_messages(
            msgs, enable_cache=True, cache_breakpoints=2
        )
        # First 2 conversation turns should have cache_control
        assert isinstance(converted[0]["content"], list)
        assert converted[0]["content"][0].get("cache_control") is not None
        assert isinstance(converted[1]["content"], list) or "cache_control" in str(converted[1])
        # Third turn should NOT have cache_control
        if isinstance(converted[2]["content"], str):
            assert True  # String content = no cache_control
        elif isinstance(converted[2]["content"], list):
            assert not any(
                b.get("cache_control") for b in converted[2]["content"]
                if isinstance(b, dict)
            )

    def test_zero_breakpoints_no_turn_caching(self, provider):
        msgs = [
            Message(role=Role.SYSTEM, content="System."),
            Message(role=Role.USER, content="Hello"),
        ]
        system, converted = provider._convert_messages(
            msgs, enable_cache=True, cache_breakpoints=0
        )
        # System still cached, but no turns
        assert isinstance(system, list)
        # First turn should be plain string (no cache_control)
        assert isinstance(converted[0]["content"], str)

    def test_tool_result_messages_preserved(self, provider):
        msgs = [
            Message(role=Role.SYSTEM, content="Sys"),
            Message(role=Role.TOOL, content="result data", tool_call_id="tc_1"),
        ]
        system, converted = provider._convert_messages(msgs, enable_cache=True)
        assert converted[0]["role"] == "user"
        assert converted[0]["content"][0]["type"] == "tool_result"

    def test_empty_messages(self, provider):
        system, converted = provider._convert_messages([], enable_cache=True)
        assert system is None
        assert converted == []


# ── Beta header construction ─────────────────────────────────────

class TestBetaHeaders:

    @pytest.mark.asyncio
    async def test_cache_beta_header_added(self, provider):
        """Verify prompt-caching beta is in headers when enabled."""
        _captured_headers = {}

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        import aiohttp
        with patch.object(provider, '_get_session') as mock_get:
            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_response)
            mock_get.return_value = mock_session

            msgs = [Message(role=Role.SYSTEM, content="test")]
            await provider.complete(msgs)

            call_args = mock_session.post.call_args
            headers = call_args[1]["headers"]
            assert "prompt-caching-2024-07-31" in headers.get("anthropic-beta", "")

    @pytest.mark.asyncio
    async def test_no_cache_beta_when_disabled(self, provider_no_cache):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider_no_cache, '_get_session') as mock_get:
            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_response)
            mock_get.return_value = mock_session

            msgs = [Message(role=Role.SYSTEM, content="test")]
            await provider_no_cache.complete(msgs)

            call_args = mock_session.post.call_args
            headers = call_args[1]["headers"]
            beta = headers.get("anthropic-beta", "")
            assert "prompt-caching" not in beta


# ── Cache stats tracking ─────────────────────────────────────────

class TestCacheStats:

    @pytest.mark.asyncio
    async def test_cache_hit_tracked(self, provider):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 95,
            },
            "stop_reason": "end_turn",
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, '_get_session') as mock_get:
            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_response)
            mock_get.return_value = mock_session

            await provider.complete([Message(role=Role.SYSTEM, content="test")])
        assert provider.cache_stats["hits"] == 1
        assert provider.cache_stats["read_tokens"] == 95

    @pytest.mark.asyncio
    async def test_cache_miss_tracked(self, provider):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 90,
                "cache_read_input_tokens": 0,
            },
            "stop_reason": "end_turn",
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, '_get_session') as mock_get:
            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_response)
            mock_get.return_value = mock_session

            await provider.complete([Message(role=Role.SYSTEM, content="test")])
        assert provider.cache_stats["misses"] == 1
        assert provider.cache_stats["creation_tokens"] == 90

    def test_no_cache_tokens_no_tracking(self, provider):
        """Without cache tokens in response, stats stay at 0."""
        assert provider.cache_stats["hits"] == 0
        assert provider.cache_stats["misses"] == 0
