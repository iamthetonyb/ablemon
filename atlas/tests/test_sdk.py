"""Tests for the ATLAS SDK."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.sdk.errors import (
    APIError,
    ATLASError,
    AuthError,
    BudgetExhausted,
    ContextOverflow,
    RateLimitError,
    ToolError,
)
from atlas.sdk.hooks import HookManager
from atlas.sdk.session import Session, SessionMessage
from atlas.sdk.tool import Tool, ToolDefinition


# ── Error types ───────────────────────────────────────────────────


class TestErrors:
    def test_base_error_not_retryable(self):
        e = ATLASError("fail")
        assert e.is_retryable() is False
        assert e.is_context_limit() is False

    def test_api_error_retryable_status_codes(self):
        for code in (429, 500, 502, 503, 529):
            e = APIError("fail", status_code=code, provider="test")
            assert e.is_retryable() is True
            assert e.provider == "test"

    def test_api_error_non_retryable(self):
        e = APIError("bad request", status_code=400)
        assert e.is_retryable() is False

    def test_rate_limit_always_retryable(self):
        e = RateLimitError("slow down", retry_after=5.0)
        assert e.is_retryable() is True
        assert e.retry_after == 5.0

    def test_context_overflow(self):
        e = ContextOverflow("too big")
        assert e.is_context_limit() is True
        assert e.is_retryable() is False

    def test_tool_error_retryable_flag(self):
        e1 = ToolError("timeout", tool_name="search", retryable=True)
        assert e1.is_retryable() is True
        assert e1.tool_name == "search"

        e2 = ToolError("perm denied", tool_name="write", retryable=False)
        assert e2.is_retryable() is False

    def test_budget_exhausted(self):
        e = BudgetExhausted("out of money")
        assert e.is_retryable() is False

    def test_auth_error(self):
        e = AuthError("bad token")
        assert e.is_retryable() is False

    def test_all_are_atlas_errors(self):
        for cls in (APIError, AuthError, RateLimitError, ContextOverflow, ToolError, BudgetExhausted):
            assert issubclass(cls, ATLASError)


# ── Tool decorator ────────────────────────────────────────────────


class TestToolDecorator:
    def test_basic_tool(self):
        @Tool(name="greet", description="Say hello")
        def greet(name: str) -> str:
            return f"Hello {name}"

        assert hasattr(greet, "_tool_definition")
        td = greet._tool_definition
        assert td.name == "greet"
        assert td.description == "Say hello"
        assert td.parameters["properties"]["name"]["type"] == "string"
        assert "name" in td.parameters["required"]

    def test_default_name_from_function(self):
        @Tool(description="does stuff")
        def my_func(x: int) -> str:
            return str(x)

        assert my_func._tool_definition.name == "my_func"

    def test_optional_params_not_required(self):
        @Tool(name="search")
        def search(query: str, limit: int = 10) -> str:
            return query

        td = search._tool_definition
        assert "query" in td.parameters["required"]
        assert "limit" not in td.parameters["required"]
        assert td.parameters["properties"]["limit"]["default"] == 10

    def test_type_mapping(self):
        @Tool(name="types")
        def types_func(s: str, i: int, f: float, b: bool) -> str:
            return ""

        props = types_func._tool_definition.parameters["properties"]
        assert props["s"]["type"] == "string"
        assert props["i"]["type"] == "integer"
        assert props["f"]["type"] == "number"
        assert props["b"]["type"] == "boolean"

    def test_openai_schema_format(self):
        @Tool(name="test", description="A test tool")
        def test_tool(q: str) -> str:
            return q

        schema = test_tool._tool_definition.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test"
        assert schema["function"]["description"] == "A test tool"
        assert "parameters" in schema["function"]

    def test_read_only_and_destructive_flags(self):
        @Tool(name="reader", is_read_only=True)
        def reader() -> str:
            return ""

        @Tool(name="deleter", is_destructive=True)
        def deleter() -> str:
            return ""

        assert reader._tool_definition.is_read_only is True
        assert reader._tool_definition.is_destructive is False
        assert deleter._tool_definition.is_destructive is True
        assert deleter._tool_definition.is_read_only is False

    def test_function_still_callable(self):
        @Tool(name="add", description="Add numbers")
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5


# ── HookManager ──────────────────────────────────────────────────


class TestHookManager:
    def test_register_and_trigger(self):
        hm = HookManager()
        called = []

        @hm.on("pre_tool_use")
        def log_tool(**kwargs):
            called.append(kwargs.get("tool_name"))

        result = asyncio.run(
            hm.trigger("pre_tool_use", tool_name="search")
        )
        assert result is True
        assert called == ["search"]

    def test_async_hook(self):
        hm = HookManager()
        called = []

        @hm.on("post_tool_use")
        async def async_hook(**kwargs):
            called.append("async")

        asyncio.run(hm.trigger("post_tool_use"))
        assert called == ["async"]

    def test_hook_deny(self):
        hm = HookManager()

        @hm.on("pre_tool_use")
        def deny(**kwargs):
            return False

        result = asyncio.run(
            hm.trigger("pre_tool_use", tool_name="danger")
        )
        assert result is False

    def test_hook_error_swallowed(self):
        hm = HookManager()

        @hm.on("on_error")
        def bad_hook(**kwargs):
            raise RuntimeError("boom")

        # Should not raise, just log a warning
        result = asyncio.run(
            hm.trigger("on_error")
        )
        assert result is True

    def test_no_hooks_returns_true(self):
        hm = HookManager()
        result = asyncio.run(
            hm.trigger("nonexistent_event")
        )
        assert result is True

    def test_multiple_hooks_same_event(self):
        hm = HookManager()
        order = []

        @hm.on("test")
        def first(**kw):
            order.append(1)

        @hm.on("test")
        def second(**kw):
            order.append(2)

        asyncio.run(hm.trigger("test"))
        assert order == [1, 2]


# ── Session ──────────────────────────────────────────────────────


class TestSession:
    def test_session_creation(self):
        agent = MagicMock()
        s = Session(agent=agent, session_id="test-123")
        assert s.session_id == "test-123"
        assert s.messages == []
        assert s.total_cost_usd == 0.0
        assert s.tools_used == []

    def test_session_auto_id(self):
        agent = MagicMock()
        s = Session(agent=agent)
        assert len(s.session_id) == 8

    def test_message_tracking(self):
        agent = MagicMock()
        s = Session(agent=agent, session_id="track")
        s.messages.append(SessionMessage(role="user", content="hello"))
        s.messages.append(SessionMessage(role="assistant", content="hi there"))
        assert len(s.messages) == 2
        assert s.messages[0].role == "user"
        assert s.messages[1].content == "hi there"

    def test_export_jsonl(self):
        agent = MagicMock()
        s = Session(agent=agent, session_id="export")
        s.messages.append(SessionMessage(role="user", content="test input"))
        s.messages.append(SessionMessage(role="assistant", content="test output"))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            result = s.export_jsonl(path)
            assert result == path
            assert path.exists()

            lines = path.read_text().strip().split("\n")
            assert len(lines) == 2

            first = json.loads(lines[0])
            assert first["role"] == "user"
            assert first["content"] == "test input"
            assert first["session_id"] == "export"

            second = json.loads(lines[1])
            assert second["role"] == "assistant"

    def test_export_creates_parent_dirs(self):
        agent = MagicMock()
        s = Session(agent=agent, session_id="nested")
        s.messages.append(SessionMessage(role="user", content="hi"))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deep" / "nested" / "dir" / "session.jsonl"
            s.export_jsonl(path)
            assert path.exists()


# ── ATLASAgent ───────────────────────────────────────────────────


class TestATLASAgent:
    def test_creation_defaults(self):
        from atlas.sdk.agent import ATLASAgent

        agent = ATLASAgent(name="test-agent")
        assert agent.name == "test-agent"
        assert agent.system_prompt == ""
        assert agent.tenant_id == "tony"
        assert agent.tier == "auto"
        assert agent.offline is False
        assert agent._tools == []
        assert agent._max_tool_iterations == 20
        assert agent._max_consecutive_failures == 3

    def test_creation_with_tools(self):
        from atlas.sdk.agent import ATLASAgent

        @Tool(name="search", description="Search")
        def search(q: str) -> str:
            return q

        agent = ATLASAgent(name="tooled", tools=[search])
        assert len(agent._tools) == 1
        assert agent._tools[0].name == "search"

    def test_creation_with_tool_definition_directly(self):
        from atlas.sdk.agent import ATLASAgent

        td = ToolDefinition(
            name="direct",
            description="Direct tool",
            handler=lambda: "ok",
            parameters={"type": "object", "properties": {}, "required": []},
        )
        agent = ATLASAgent(name="direct", tools=[td])
        assert len(agent._tools) == 1
        assert agent._tools[0].name == "direct"

    def test_creation_with_hooks(self):
        from atlas.sdk.agent import ATLASAgent

        hooks = HookManager()
        agent = ATLASAgent(name="hooked", hooks=hooks)
        assert agent._hooks is hooks

    def test_session_returns_session(self):
        from atlas.sdk.agent import ATLASAgent

        agent = ATLASAgent(name="sess")
        s = agent.session("my-session")
        assert isinstance(s, Session)
        assert s.session_id == "my-session"
        assert s.agent is agent

    def test_on_decorator_registers_hook(self):
        from atlas.sdk.agent import ATLASAgent

        agent = ATLASAgent(name="hooks")
        called = []

        @agent.on("on_error")
        def handle_error(**kw):
            called.append(True)

        asyncio.run(
            agent._hooks.trigger("on_error")
        )
        assert called == [True]

    def test_resolve_tier_explicit(self):
        from atlas.sdk.agent import ATLASAgent

        agent = ATLASAgent(name="t", tier=2)
        assert agent._resolve_tier("anything") == 2

    def test_resolve_tier_override(self):
        from atlas.sdk.agent import ATLASAgent

        agent = ATLASAgent(name="t", tier=1)
        assert agent._resolve_tier("anything", tier_override=4) == 4

    def test_resolve_tier_offline(self):
        from atlas.sdk.agent import ATLASAgent

        agent = ATLASAgent(name="t", offline=True)
        assert agent._resolve_tier("anything") == 5

    def test_resolve_tier_defaults_to_1_without_scorer(self):
        from atlas.sdk.agent import ATLASAgent

        agent = ATLASAgent(name="t", tier="auto")
        # _scorer is None by default (no lazy init yet)
        assert agent._resolve_tier("hello") == 1

    def test_run_no_provider_returns_message(self):
        from atlas.sdk.agent import ATLASAgent

        agent = ATLASAgent(name="noprov", tier=1)
        # Force providers to stay None
        agent._provider_registry = None

        result = asyncio.run(agent.run("test"))
        assert "No provider available" in result

    def test_consecutive_failure_cap(self):
        """Verify the agent tracks consecutive tool failures."""
        from atlas.sdk.agent import ATLASAgent

        agent = ATLASAgent(name="failcap")
        # The cap is set at init
        assert agent._max_consecutive_failures == 3
