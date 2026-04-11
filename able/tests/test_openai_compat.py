"""Tests for E5 — OpenAI-Compatible API Server.

Covers: request parsing, response formatting, model mapping, auth,
SQLite persistence, input validation, streaming format.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from able.core.gateway.openai_compat import (
    CompatRequest,
    CompatResponse,
    CompatResponseDB,
    OpenAICompatServer,
    MODEL_TIER_MAP,
)


# ── Request parsing ──────────────────────────────────────────────

class TestRequestParsing:

    def test_minimal_request(self):
        req = CompatRequest.from_dict({
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert req.model == "able-auto"
        assert len(req.messages) == 1
        assert req.stream is False

    def test_full_request(self):
        req = CompatRequest.from_dict({
            "model": "able-t4",
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "hello"},
            ],
            "temperature": 0.9,
            "max_tokens": 1000,
            "stream": True,
        })
        assert req.model == "able-t4"
        assert req.temperature == 0.9
        assert req.max_tokens == 1000
        assert req.stream is True

    def test_empty_messages_rejected(self):
        with pytest.raises(ValueError, match="required"):
            CompatRequest.from_dict({"messages": []})

    def test_missing_role_rejected(self):
        with pytest.raises(ValueError, match="missing 'role'"):
            CompatRequest.from_dict({
                "messages": [{"content": "no role"}],
            })

    def test_invalid_role_rejected(self):
        with pytest.raises(ValueError, match="invalid role"):
            CompatRequest.from_dict({
                "messages": [{"role": "narrator", "content": "hi"}],
            })

    def test_unknown_fields_stripped(self):
        req = CompatRequest.from_dict({
            "messages": [{"role": "user", "content": "hi"}],
            "logprobs": True,  # Not in _ALLOWED_FIELDS
            "seed": 42,
        })
        assert req.model == "able-auto"  # Parsed without error

    def test_max_tokens_capped(self):
        req = CompatRequest.from_dict({
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 999999,
        })
        assert req.max_tokens <= 16384

    def test_too_many_messages_rejected(self):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(101)]
        with pytest.raises(ValueError, match="Too many"):
            CompatRequest.from_dict({"messages": messages})


# ── Response formatting ──────────────────────────────────────────

class TestResponseFormatting:

    def test_to_dict_structure(self):
        resp = CompatResponse(
            id="chatcmpl-test",
            model="able-auto",
            content="Hello world",
            input_tokens=10,
            output_tokens=5,
        )
        d = resp.to_dict()
        assert d["object"] == "chat.completion"
        assert d["choices"][0]["message"]["content"] == "Hello world"
        assert d["usage"]["total_tokens"] == 15

    def test_stream_chunk_format(self):
        resp = CompatResponse(id="test", model="able-auto", content="")
        chunk = resp.to_stream_chunk("Hello")
        assert chunk.startswith("data: ")
        assert "Hello" in chunk
        assert "chat.completion.chunk" in chunk

    def test_stream_finish_chunk(self):
        resp = CompatResponse(id="test", model="able-auto", content="")
        chunk = resp.to_stream_chunk(finish=True)
        assert '"finish_reason": "stop"' in chunk


# ── Model mapping ────────────────────────────────────────────────

class TestModelMapping:

    def test_auto_maps_to_none(self):
        assert MODEL_TIER_MAP["able-auto"] is None

    def test_tier_mapping(self):
        assert MODEL_TIER_MAP["able-t1"] == 1
        assert MODEL_TIER_MAP["able-t4"] == 4

    def test_openai_compat_mapping(self):
        assert MODEL_TIER_MAP["gpt-4"] == 4
        assert MODEL_TIER_MAP["gpt-4o-mini"] == 1

    def test_claude_compat_mapping(self):
        assert MODEL_TIER_MAP["claude-3-opus"] == 4


# ── Authentication ───────────────────────────────────────────────

class TestAuth:

    def test_no_auth_required_by_default(self):
        server = OpenAICompatServer()
        assert server._check_auth(None) is True

    def test_valid_token(self):
        server = OpenAICompatServer(auth_token="secret-123")
        assert server._check_auth("Bearer secret-123") is True

    def test_invalid_token(self):
        server = OpenAICompatServer(auth_token="secret-123")
        assert server._check_auth("Bearer wrong-token") is False

    def test_missing_bearer_prefix(self):
        server = OpenAICompatServer(auth_token="secret-123")
        assert server._check_auth("secret-123") is False

    def test_no_header(self):
        server = OpenAICompatServer(auth_token="secret-123")
        assert server._check_auth(None) is False


# ── SQLite persistence ───────────────────────────────────────────

class TestPersistence:

    def test_record_and_stats(self, tmp_path):
        db = CompatResponseDB(db_path=str(tmp_path / "test.db"))
        req = CompatRequest(messages=[{"role": "user", "content": "hi"}])
        resp = CompatResponse(
            id="test-1", model="able-auto", content="response",
            input_tokens=5, output_tokens=10,
        )
        db.record(req, resp, duration_ms=42.0)
        stats = db.stats()
        assert stats["total_completions"] == 1
        assert stats["total_input_tokens"] == 5
        assert stats["total_output_tokens"] == 10

    def test_get_recent(self, tmp_path):
        db = CompatResponseDB(db_path=str(tmp_path / "test.db"))
        for i in range(3):
            req = CompatRequest(messages=[{"role": "user", "content": f"msg {i}"}])
            resp = CompatResponse(id=f"test-{i}", model="able-auto", content=f"resp {i}")
            db.record(req, resp, duration_ms=10.0)
        recent = db.get_recent(limit=2)
        assert len(recent) == 2


# ── Server handler ───────────────────────────────────────────────

class TestServerHandler:

    @pytest.mark.asyncio
    async def test_completions_without_gateway(self):
        server = OpenAICompatServer()
        result = await server.handle_completions({
            "messages": [{"role": "user", "content": "test message"}],
        })
        assert "choices" in result
        assert "Echo" in result["choices"][0]["message"]["content"]

    @pytest.mark.asyncio
    async def test_completions_auth_failure(self):
        server = OpenAICompatServer(auth_token="secret")
        result = await server.handle_completions(
            {"messages": [{"role": "user", "content": "test"}]},
            auth_header="Bearer wrong",
        )
        assert "error" in result
        assert result["error"]["code"] == 401

    @pytest.mark.asyncio
    async def test_completions_invalid_request(self):
        server = OpenAICompatServer()
        result = await server.handle_completions({"messages": []})
        assert "error" in result
        assert result["error"]["code"] == 400

    @pytest.mark.asyncio
    async def test_models_endpoint(self):
        server = OpenAICompatServer()
        result = await server.handle_models()
        assert result["object"] == "list"
        assert len(result["data"]) >= 5

    @pytest.mark.asyncio
    async def test_server_stats(self):
        server = OpenAICompatServer()
        await server.handle_completions({
            "messages": [{"role": "user", "content": "test"}],
        })
        stats = server.stats()
        assert stats["request_count"] == 1
