"""Tests for F6 — Gateway Hardening.

Covers: startup bounding, disconnect tracking, graceful shutdown,
health checks, stats.
"""

import pytest
import asyncio
import time

from able.core.gateway.hardening import (
    GatewayHarness,
    HarnessStats,
    ShutdownPhase,
)


@pytest.fixture
def harness():
    return GatewayHarness(
        max_startup_s=1.0,
        max_consecutive_disconnects=3,
    )


# ── Startup bounding ─────────────────────────────────────────────

class TestStartup:

    def test_healthy_after_startup(self, harness):
        harness.begin_startup()
        harness.startup_complete()
        assert harness.is_healthy()

    def test_not_expired_during_startup(self, harness):
        harness.begin_startup()
        assert not harness.is_startup_expired()

    def test_expired_after_timeout(self):
        h = GatewayHarness(max_startup_s=0.01)
        h.begin_startup()
        time.sleep(0.02)
        assert h.is_startup_expired()
        assert not h.is_healthy()

    def test_not_expired_if_complete(self):
        h = GatewayHarness(max_startup_s=0.01)
        h.begin_startup()
        h.startup_complete()
        time.sleep(0.02)
        assert not h.is_startup_expired()  # Already complete

    def test_not_expired_before_start(self, harness):
        assert not harness.is_startup_expired()


# ── Disconnect tracking ──────────────────────────────────────────

class TestDisconnects:

    def test_record_disconnect(self, harness):
        harness.record_disconnect()
        stats = harness.stats()
        assert stats.consecutive_disconnects == 1

    def test_success_resets_consecutive(self, harness):
        harness.record_disconnect()
        harness.record_disconnect()
        harness.record_success()
        stats = harness.stats()
        assert stats.consecutive_disconnects == 0

    def test_abort_on_max_disconnects(self, harness):
        for _ in range(3):
            harness.record_disconnect()
        assert harness.should_abort()
        assert not harness.is_healthy()

    def test_no_abort_under_threshold(self, harness):
        harness.record_disconnect()
        harness.record_disconnect()
        assert not harness.should_abort()

    def test_disconnect_window(self):
        h = GatewayHarness(
            max_consecutive_disconnects=10,
            disconnect_window_s=0.01,
        )
        h.record_disconnect()
        time.sleep(0.02)
        h.record_disconnect()  # Prunes the old one
        stats = h.stats()
        assert stats.total_disconnects == 1  # Old one pruned


# ── Graceful shutdown ────────────────────────────────────────────

class TestGracefulShutdown:

    @pytest.mark.asyncio
    async def test_shutdown_executes_phases(self, harness):
        completed = []

        async def phase1():
            completed.append("phase1")

        async def phase2():
            completed.append("phase2")

        harness.register_shutdown_phase("phase1", phase1)
        harness.register_shutdown_phase("phase2", phase2)
        results = await harness.graceful_shutdown()
        assert results["phase1"] is True
        assert results["phase2"] is True
        assert completed == ["phase1", "phase2"]

    @pytest.mark.asyncio
    async def test_shutdown_timeout_phase(self, harness):
        async def slow():
            await asyncio.sleep(10)

        harness.register_shutdown_phase("slow", slow, timeout_s=0.01)
        results = await harness.graceful_shutdown()
        assert results["slow"] is False

    @pytest.mark.asyncio
    async def test_critical_phase_stops_shutdown(self, harness):
        completed = []

        async def critical_fail():
            raise RuntimeError("critical failure")

        async def never_reached():
            completed.append("reached")

        harness.register_shutdown_phase("critical", critical_fail, critical=True)
        harness.register_shutdown_phase("after", never_reached)
        results = await harness.graceful_shutdown()
        assert results["critical"] is False
        assert "after" not in results
        assert "reached" not in completed

    @pytest.mark.asyncio
    async def test_non_critical_failure_continues(self, harness):
        completed = []

        async def fail():
            raise RuntimeError("non-critical")

        async def success():
            completed.append("done")

        harness.register_shutdown_phase("fail", fail, critical=False)
        harness.register_shutdown_phase("success", success)
        results = await harness.graceful_shutdown()
        assert results["fail"] is False
        assert results["success"] is True
        assert "done" in completed

    @pytest.mark.asyncio
    async def test_empty_shutdown(self, harness):
        results = await harness.graceful_shutdown()
        assert results == {}


# ── Stats ────────────────────────────────────────────────────────

class TestStats:

    def test_initial_stats(self, harness):
        stats = harness.stats()
        assert isinstance(stats, HarnessStats)
        assert stats.startup_duration_ms == 0
        assert stats.abort_triggered is False

    def test_stats_after_startup(self, harness):
        harness.begin_startup()
        harness.startup_complete()
        stats = harness.stats()
        assert stats.startup_duration_ms > 0

    @pytest.mark.asyncio
    async def test_stats_after_shutdown(self, harness):
        await harness.graceful_shutdown()
        stats = harness.stats()
        assert stats.shutdown_complete is True
