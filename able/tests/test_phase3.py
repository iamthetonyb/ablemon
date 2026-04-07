"""
Tests for Phase 3 additions:
- Event bus (pub/sub, SSE bridge)
- Prometheus metrics exporter
- Policy engine (priority-based permissions)
"""

import asyncio
from unittest.mock import AsyncMock

import pytest


# ── Event Bus ────────────────────────────────────────────────────────────

class TestEventBus:
    """Unit tests for able.core.gateway.event_bus."""

    def test_event_bus_importable(self):
        from able.core.gateway.event_bus import EventBus, SSEBridge, Event
        assert EventBus is not None
        assert SSEBridge is not None

    def test_subscribe_and_emit(self):
        from able.core.gateway.event_bus import EventBus

        received = []

        async def handler(event):
            received.append(event)

        async def run():
            bus = EventBus()
            bus.subscribe("test.*", handler)
            await bus.emit("test.hello", {"msg": "world"})
            assert len(received) == 1
            assert received[0].topic == "test.hello"
            assert received[0].data["msg"] == "world"

        asyncio.run(run())

    def test_glob_pattern_matching(self):
        from able.core.gateway.event_bus import EventBus

        matched = []

        async def handler(event):
            matched.append(event.topic)

        async def run():
            bus = EventBus()
            bus.subscribe("routing.*", handler)
            await bus.emit("routing.decision", {})
            await bus.emit("routing.fallback", {})
            await bus.emit("buddy.xp", {})  # Should NOT match
            assert len(matched) == 2
            assert "buddy.xp" not in matched

        asyncio.run(run())

    def test_unsubscribe(self):
        from able.core.gateway.event_bus import EventBus

        count = []

        async def handler(event):
            count.append(1)

        async def run():
            bus = EventBus()
            sub = bus.subscribe("*", handler)
            await bus.emit("a", {})
            assert len(count) == 1
            bus.unsubscribe(sub)
            await bus.emit("b", {})
            assert len(count) == 1  # No more events

        asyncio.run(run())

    def test_handler_error_isolation(self):
        """One failing handler should not block others."""
        from able.core.gateway.event_bus import EventBus

        results = []

        async def bad_handler(event):
            raise ValueError("boom")

        async def good_handler(event):
            results.append("ok")

        async def run():
            bus = EventBus()
            bus.subscribe("*", bad_handler)
            bus.subscribe("*", good_handler)
            await bus.emit("test", {})
            assert results == ["ok"]
            assert bus.handler_errors == 1

        asyncio.run(run())

    def test_event_history(self):
        from able.core.gateway.event_bus import EventBus

        async def run():
            bus = EventBus(history_size=5)
            for i in range(10):
                await bus.emit(f"event.{i}", {"i": i})
            assert len(bus.recent_events) <= 5
            assert bus.events_emitted == 10

        asyncio.run(run())

    def test_stats(self):
        from able.core.gateway.event_bus import EventBus

        async def noop(event):
            pass

        async def run():
            bus = EventBus()
            bus.subscribe("*", noop)
            await bus.emit("x", {})
            stats = bus.stats()
            assert stats["events_emitted"] == 1
            assert stats["handlers_invoked"] == 1
            assert stats["subscriptions"] == 1

        asyncio.run(run())


class TestSSEBridge:
    def test_add_and_remove_subscriber(self):
        from able.core.gateway.event_bus import EventBus, SSEBridge

        bus = EventBus()
        bridge = SSEBridge(bus)
        q = bridge.add_subscriber()
        assert q is not None
        assert bridge.subscriber_count == 1
        bridge.remove_subscriber(q)
        assert bridge.subscriber_count == 0

    def test_max_subscribers(self):
        from able.core.gateway.event_bus import EventBus, SSEBridge

        bus = EventBus()
        bridge = SSEBridge(bus)
        bridge.MAX_SUBSCRIBERS = 3
        for _ in range(3):
            assert bridge.add_subscriber() is not None
        assert bridge.add_subscriber() is None  # At capacity
        assert bridge.subscriber_count == 3


# ── Prometheus Exporter ──────────────────────────────────────────────────

class TestPrometheusExporter:
    def test_exporter_importable(self):
        from able.core.routing.prometheus_exporter import export_prometheus
        assert callable(export_prometheus)

    def test_export_returns_text(self):
        from able.core.routing.prometheus_exporter import export_prometheus
        text = export_prometheus(db_path="/nonexistent/path.db")
        assert isinstance(text, str)
        assert "# HELP" in text
        assert "# TYPE" in text

    def test_export_has_expected_metrics(self):
        from able.core.routing.prometheus_exporter import export_prometheus
        text = export_prometheus(db_path="/nonexistent/path.db")
        expected = [
            "able_interactions_total",
            "able_cost_usd_total",
            "able_tokens_total",
            "able_fallback_total",
            "able_corpus_pairs_total",
            "able_scorer_version",
            "able_build_info",
        ]
        for metric in expected:
            assert metric in text, f"Missing metric: {metric}"

    def test_export_with_provider_health(self):
        from able.core.routing.prometheus_exporter import export_prometheus, _cache
        _cache["expires"] = 0  # Clear cache from prior test
        health = {"nvidia-nim": True, "openrouter": False}
        text = export_prometheus(
            db_path="/nonexistent/path.db",
            provider_health=health,
        )
        assert "able_provider_healthy" in text
        assert 'provider="nvidia-nim"' in text


# ── Policy Engine ────────────────────────────────────────────────────────

class TestPolicyEngine:
    def test_policy_engine_importable(self):
        from able.core.security.policy_engine import PolicyEngine, PolicyAction
        assert PolicyEngine is not None

    def test_load_from_yaml(self):
        from able.core.security.policy_engine import PolicyEngine
        engine = PolicyEngine.from_yaml("config/tool_permissions.yaml")
        assert engine.policy_count > 0
        stats = engine.stats()
        assert stats["allow"] > 0
        assert stats["deny"] > 0

    def test_evaluate_allow(self):
        from able.core.security.policy_engine import PolicyEngine, PolicyAction
        engine = PolicyEngine.from_yaml("config/tool_permissions.yaml")
        result = engine.evaluate("ls -la")
        assert result.action == PolicyAction.ALLOW
        assert result.matched

    def test_evaluate_deny(self):
        from able.core.security.policy_engine import PolicyEngine, PolicyAction
        engine = PolicyEngine.from_yaml("config/tool_permissions.yaml")
        result = engine.evaluate("rm -rf /")
        assert result.action == PolicyAction.DENY

    def test_evaluate_require_approval(self):
        from able.core.security.policy_engine import PolicyEngine, PolicyAction
        engine = PolicyEngine.from_yaml("config/tool_permissions.yaml")
        result = engine.evaluate("git push origin main")
        assert result.action == PolicyAction.REQUIRE_APPROVAL

    def test_evaluate_unknown_defaults_to_approval(self):
        from able.core.security.policy_engine import PolicyEngine, PolicyAction
        engine = PolicyEngine.from_yaml("config/tool_permissions.yaml")
        result = engine.evaluate("some_totally_unknown_command --flag")
        assert result.action == PolicyAction.REQUIRE_APPROVAL
        assert not result.matched

    def test_priority_ordering(self):
        """Higher priority rules should win over lower priority."""
        from able.core.security.policy_engine import (
            PolicyEngine, PolicyRecord, PolicyAction,
        )
        policies = [
            PolicyRecord(pattern="git push*", action=PolicyAction.REQUIRE_APPROVAL, priority=50),
            PolicyRecord(pattern="git push --force*", action=PolicyAction.DENY, priority=100),
        ]
        engine = PolicyEngine(policies)
        # Force push should be denied (priority 100 > 50)
        result = engine.evaluate("git push --force origin main")
        assert result.action == PolicyAction.DENY

    def test_add_and_remove_policy(self):
        from able.core.security.policy_engine import (
            PolicyEngine, PolicyRecord, PolicyAction,
        )
        engine = PolicyEngine([])
        engine.add_policy(PolicyRecord(
            pattern="test_cmd", action=PolicyAction.ALLOW, priority=50,
        ))
        assert engine.policy_count == 1
        removed = engine.remove_pattern("test_cmd")
        assert removed == 1
        assert engine.policy_count == 0

    def test_missing_yaml_returns_empty(self):
        from able.core.security.policy_engine import PolicyEngine
        engine = PolicyEngine.from_yaml("/nonexistent/path.yaml")
        assert engine.policy_count == 0
