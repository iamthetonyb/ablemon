"""Tests for D10 — Plugin Lifecycle Hooks.

Covers: registration, firing, isolation, context injection,
priority ordering, stats, unregister, async hooks.
"""

import pytest
import asyncio

from able.core.hooks.plugin_hooks import (
    HookRegistry,
    HookFireResult,
    HookResult,
    VALID_HOOKS,
)


@pytest.fixture
def registry():
    return HookRegistry()


# ── Registration ─────────────────────────────────────────────────

class TestRegistration:

    def test_register_valid_event(self, registry):
        registry.register("pre_tool_call", lambda **kw: None, "test-plugin")
        assert registry.registered_count("pre_tool_call") == 1

    def test_register_invalid_event(self, registry):
        with pytest.raises(ValueError, match="Invalid hook event"):
            registry.register("invalid_event", lambda **kw: None)

    def test_register_multiple_hooks(self, registry):
        registry.register("pre_tool_call", lambda **kw: None, "plugin-a")
        registry.register("pre_tool_call", lambda **kw: None, "plugin-b")
        assert registry.registered_count("pre_tool_call") == 2

    def test_register_across_events(self, registry):
        registry.register("pre_tool_call", lambda **kw: None, "test")
        registry.register("post_tool_call", lambda **kw: None, "test")
        assert registry.registered_count() == 2

    def test_unregister(self, registry):
        registry.register("pre_tool_call", lambda **kw: None, "test")
        removed = registry.unregister("pre_tool_call", "test")
        assert removed == 1
        assert registry.registered_count("pre_tool_call") == 0

    def test_unregister_nonexistent(self, registry):
        removed = registry.unregister("pre_tool_call", "nope")
        assert removed == 0


# ── Firing ───────────────────────────────────────────────────────

class TestFiring:

    @pytest.mark.asyncio
    async def test_fire_sync_hook(self, registry):
        called = []
        registry.register("pre_tool_call", lambda **kw: called.append(True), "test")
        result = await registry.fire("pre_tool_call")
        assert len(called) == 1
        assert result.succeeded == 1

    @pytest.mark.asyncio
    async def test_fire_async_hook(self, registry):
        called = []

        async def async_hook(**kw):
            called.append(True)

        registry.register("post_tool_call", async_hook, "test")
        result = await registry.fire("post_tool_call")
        assert len(called) == 1
        assert result.succeeded == 1

    @pytest.mark.asyncio
    async def test_fire_no_hooks(self, registry):
        result = await registry.fire("pre_tool_call")
        assert result.succeeded == 0
        assert result.failed == 0

    @pytest.mark.asyncio
    async def test_fire_invalid_event(self, registry):
        result = await registry.fire("not_a_real_event")
        assert result.succeeded == 0

    @pytest.mark.asyncio
    async def test_fire_passes_kwargs(self, registry):
        received = {}

        def capture(**kw):
            received.update(kw)

        registry.register("pre_tool_call", capture, "test")
        await registry.fire("pre_tool_call", tool_name="read_file", args={"path": "/tmp"})
        assert received["tool_name"] == "read_file"

    @pytest.mark.asyncio
    async def test_correlation_id_auto_generated(self, registry):
        registry.register("pre_tool_call", lambda **kw: None, "test")
        result = await registry.fire("pre_tool_call")
        assert result.correlation_id
        assert len(result.correlation_id) > 0

    @pytest.mark.asyncio
    async def test_correlation_id_passed_through(self, registry):
        received_id = []

        def capture(**kw):
            received_id.append(kw.get("correlation_id"))

        registry.register("pre_tool_call", capture, "test")
        await registry.fire("pre_tool_call", correlation_id="test-123")
        assert received_id[0] == "test-123"


# ── Isolation ────────────────────────────────────────────────────

class TestIsolation:

    @pytest.mark.asyncio
    async def test_failing_hook_doesnt_break_others(self, registry):
        called = []

        def failing(**kw):
            raise RuntimeError("boom")

        def succeeding(**kw):
            called.append(True)

        registry.register("pre_tool_call", failing, "bad-plugin")
        registry.register("pre_tool_call", succeeding, "good-plugin")
        result = await registry.fire("pre_tool_call")
        assert result.succeeded == 1
        assert result.failed == 1
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_error_message_captured(self, registry):
        def failing(**kw):
            raise ValueError("specific error")

        registry.register("post_llm_call", failing, "test")
        result = await registry.fire("post_llm_call")
        assert result.results[0].error == "specific error"


# ── Context injection ────────────────────────────────────────────

class TestContextInjection:

    @pytest.mark.asyncio
    async def test_pre_llm_call_returns_context(self, registry):
        def inject(**kw):
            return {"extra_system": "Be careful with auth code"}

        registry.register("pre_llm_call", inject, "security-plugin")
        result = await registry.fire("pre_llm_call")
        assert len(result.context_injections) == 1
        assert result.context_injections[0]["extra_system"] == "Be careful with auth code"

    @pytest.mark.asyncio
    async def test_non_pre_llm_ignores_return(self, registry):
        def inject(**kw):
            return {"should": "be ignored"}

        registry.register("post_tool_call", inject, "test")
        result = await registry.fire("post_tool_call")
        assert result.context_injections == []


# ── Priority ─────────────────────────────────────────────────────

class TestPriority:

    @pytest.mark.asyncio
    async def test_priority_ordering(self, registry):
        order = []
        registry.register("pre_tool_call", lambda **kw: order.append("b"), "b", priority=10)
        registry.register("pre_tool_call", lambda **kw: order.append("a"), "a", priority=1)
        registry.register("pre_tool_call", lambda **kw: order.append("c"), "c", priority=5)
        await registry.fire("pre_tool_call")
        assert order == ["a", "c", "b"]


# ── Stats & clear ────────────────────────────────────────────────

class TestStatsAndClear:

    @pytest.mark.asyncio
    async def test_stats(self, registry):
        registry.register("pre_tool_call", lambda **kw: None, "test")
        await registry.fire("pre_tool_call")
        stats = registry.stats()
        assert stats["fires"] == 1
        assert stats["successes"] == 1
        assert stats["registered"] == 1

    def test_clear_specific_event(self, registry):
        registry.register("pre_tool_call", lambda **kw: None, "a")
        registry.register("post_tool_call", lambda **kw: None, "b")
        registry.clear("pre_tool_call")
        assert registry.registered_count("pre_tool_call") == 0
        assert registry.registered_count("post_tool_call") == 1

    def test_clear_all(self, registry):
        registry.register("pre_tool_call", lambda **kw: None, "a")
        registry.register("post_tool_call", lambda **kw: None, "b")
        registry.clear()
        assert registry.registered_count() == 0

    def test_valid_hooks_constant(self):
        assert "pre_tool_call" in VALID_HOOKS
        assert "post_llm_call" in VALID_HOOKS
        assert "on_session_start" in VALID_HOOKS
        assert len(VALID_HOOKS) == 8
