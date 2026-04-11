"""Tests for F3 — Multi-Platform Approval Routing.

Covers: channel registration, priority ordering, approval flow,
timeout handling, fallback chain, stats.
"""

import asyncio
import pytest

from able.core.approval.multi_platform import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalRouter,
)


@pytest.fixture
def router():
    return ApprovalRouter(default_timeout_s=2.0)


def _make_request(action="rm -rf /tmp"):
    return ApprovalRequest(id="req-1", action=action, risk_level="medium")


# ── Channel registration ────────────────────────────────────────

class TestRegistration:

    def test_register_channel(self, router):
        async def noop(req):
            return None
        router.register_channel("test", noop, priority=1)
        assert "test" in router.active_channels

    def test_unregister_channel(self, router):
        async def noop(req):
            return None
        router.register_channel("test", noop)
        router.unregister_channel("test")
        assert "test" not in router.active_channels

    def test_set_inactive(self, router):
        async def noop(req):
            return None
        router.register_channel("test", noop)
        router.set_channel_active("test", False)
        assert "test" not in router.active_channels


# ── Approval flow ───────────────────────────────────────────────

class TestApprovalFlow:

    @pytest.mark.asyncio
    async def test_approved(self, router):
        async def approve(req):
            return ApprovalResponse(
                request_id=req.id,
                decision=ApprovalDecision.APPROVED,
            )
        router.register_channel("cli", approve, priority=1)
        resp = await router.request_approval(_make_request())
        assert resp.decision == ApprovalDecision.APPROVED
        assert resp.channel == "cli"

    @pytest.mark.asyncio
    async def test_denied(self, router):
        async def deny(req):
            return ApprovalResponse(
                request_id=req.id,
                decision=ApprovalDecision.DENIED,
                reason="Too risky",
            )
        router.register_channel("cli", deny, priority=1)
        resp = await router.request_approval(_make_request())
        assert resp.decision == ApprovalDecision.DENIED

    @pytest.mark.asyncio
    async def test_no_channels(self, router):
        resp = await router.request_approval(_make_request())
        assert resp.decision == ApprovalDecision.TIMEOUT
        assert "No active" in resp.reason


# ── Timeout handling ────────────────────────────────────────────

class TestTimeout:

    @pytest.mark.asyncio
    async def test_channel_timeout(self, router):
        async def slow(req):
            await asyncio.sleep(10)
            return None
        router.register_channel("slow", slow, priority=1)
        resp = await router.request_approval(_make_request(), timeout_s=0.1)
        assert resp.decision == ApprovalDecision.TIMEOUT

    @pytest.mark.asyncio
    async def test_none_response_tries_next(self, router):
        async def skip(req):
            return None
        async def approve(req):
            return ApprovalResponse(
                request_id=req.id,
                decision=ApprovalDecision.APPROVED,
            )
        router.register_channel("skip", skip, priority=1)
        router.register_channel("approve", approve, priority=2)
        resp = await router.request_approval(_make_request())
        assert resp.decision == ApprovalDecision.APPROVED
        assert resp.channel == "approve"


# ── Fallback chain ──────────────────────────────────────────────

class TestFallback:

    @pytest.mark.asyncio
    async def test_priority_order(self, router):
        call_order = []

        async def first(req):
            call_order.append("first")
            return ApprovalResponse(
                request_id=req.id,
                decision=ApprovalDecision.APPROVED,
            )

        async def second(req):
            call_order.append("second")
            return ApprovalResponse(
                request_id=req.id,
                decision=ApprovalDecision.APPROVED,
            )

        router.register_channel("second", second, priority=2)
        router.register_channel("first", first, priority=1)
        await router.request_approval(_make_request())
        assert call_order == ["first"]  # Stopped at first success

    @pytest.mark.asyncio
    async def test_error_tries_next(self, router):
        async def crash(req):
            raise RuntimeError("channel down")
        async def fallback(req):
            return ApprovalResponse(
                request_id=req.id,
                decision=ApprovalDecision.APPROVED,
            )
        router.register_channel("crash", crash, priority=1)
        router.register_channel("fallback", fallback, priority=2)
        resp = await router.request_approval(_make_request())
        assert resp.decision == ApprovalDecision.APPROVED
        assert resp.channel == "fallback"


# ── Stats ───────────────────────────────────────────────────────

class TestStats:

    @pytest.mark.asyncio
    async def test_stats_tracking(self, router):
        async def approve(req):
            return ApprovalResponse(
                request_id=req.id,
                decision=ApprovalDecision.APPROVED,
            )
        router.register_channel("cli", approve)
        await router.request_approval(_make_request())
        s = router.stats
        assert s.total_requests == 1
        assert s.approved == 1
        assert s.by_channel.get("cli") == 1

    def test_pending_count(self, router):
        assert router.pending_count == 0
