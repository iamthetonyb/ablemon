"""Tests for F4 — Prompt Cache Diagnostics.

Covers: turn recording, hit/miss/partial classification, fingerprint breaks,
break cause heuristics, report aggregation, savings estimation, summary.
"""

import time
import pytest

from able.core.gateway.cache_diagnostics import (
    CacheBreak,
    CacheDiagnostics,
    CacheReport,
    CacheTurn,
)


@pytest.fixture
def diag():
    return CacheDiagnostics(session_id="test-session")


# ── CacheTurn classification ────────────────────────────────────

class TestCacheTurn:

    def test_hit(self):
        t = CacheTurn(fingerprint="a", creation_tokens=0, read_tokens=1000)
        assert t.is_hit
        assert not t.is_miss
        assert not t.is_partial_hit

    def test_miss(self):
        t = CacheTurn(fingerprint="a", creation_tokens=500, read_tokens=0)
        assert t.is_miss
        assert not t.is_hit
        assert not t.is_partial_hit

    def test_partial_hit(self):
        t = CacheTurn(fingerprint="a", creation_tokens=100, read_tokens=900)
        assert t.is_partial_hit
        assert not t.is_hit
        assert not t.is_miss

    def test_empty(self):
        t = CacheTurn(fingerprint="a", creation_tokens=0, read_tokens=0)
        assert not t.is_hit
        assert not t.is_miss
        assert not t.is_partial_hit


# ── Recording turns ─────────────────────────────────────────────

class TestRecordTurn:

    def test_single_turn(self, diag):
        diag.record_turn(fingerprint="abc", creation=500, read=0)
        assert diag.turn_count == 1

    def test_multiple_turns(self, diag):
        diag.record_turn(fingerprint="abc", creation=500, read=0)
        diag.record_turn(fingerprint="abc", creation=0, read=500)
        diag.record_turn(fingerprint="abc", creation=0, read=500)
        assert diag.turn_count == 3

    def test_no_break_same_fingerprint(self, diag):
        diag.record_turn(fingerprint="abc", creation=500, read=0)
        diag.record_turn(fingerprint="abc", creation=0, read=500)
        r = diag.report()
        assert len(r.breaks) == 0


# ── Fingerprint breaks ──────────────────────────────────────────

class TestFingerprints:

    def test_break_on_fingerprint_change(self, diag):
        diag.record_turn(fingerprint="aaa", creation=500, read=0)
        diag.record_turn(fingerprint="bbb", creation=500, read=0)
        r = diag.report()
        assert len(r.breaks) == 1
        assert r.breaks[0].old_fingerprint == "aaa"
        assert r.breaks[0].new_fingerprint == "bbb"

    def test_no_break_on_empty_fingerprint(self, diag):
        diag.record_turn(fingerprint="aaa", creation=500, read=0)
        diag.record_turn(fingerprint="", creation=0, read=0)
        r = diag.report()
        assert len(r.breaks) == 0

    def test_multiple_breaks(self, diag):
        diag.record_turn(fingerprint="aaa", creation=500, read=0)
        diag.record_turn(fingerprint="bbb", creation=500, read=0)
        diag.record_turn(fingerprint="ccc", creation=500, read=0)
        r = diag.report()
        assert len(r.breaks) == 2

    def test_stabilize_fingerprint(self, diag):
        fp1 = diag.stabilize_fingerprint("You are ABLE", [
            {"role": "user", "content": "hello"},
        ])
        fp2 = diag.stabilize_fingerprint("You are ABLE", [
            {"role": "user", "content": "hello"},
        ])
        assert fp1 == fp2
        assert len(fp1) == 16  # SHA-256 truncated

    def test_fingerprint_changes_with_content(self, diag):
        fp1 = diag.stabilize_fingerprint("System A", [])
        fp2 = diag.stabilize_fingerprint("System B", [])
        assert fp1 != fp2

    def test_fingerprint_handles_list_content(self, diag):
        fp = diag.stabilize_fingerprint("sys", [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        ])
        assert len(fp) == 16


# ── Break cause heuristics ──────────────────────────────────────

class TestBreakCauses:

    def test_warmup_cause(self, diag):
        """First break is classified as warmup."""
        diag.record_turn(fingerprint="aaa", creation=500, read=0)
        diag.record_turn(fingerprint="bbb", creation=500, read=0)
        r = diag.report()
        assert r.breaks[0].cause == "warmup"

    def test_prefix_changed_cause(self, diag):
        """Later breaks classified as prefix_changed."""
        for i in range(3):
            diag.record_turn(fingerprint=f"fp{i}", creation=100, read=0)
        r = diag.report()
        # Third break (index 2) should be prefix_changed
        assert r.breaks[-1].cause == "prefix_changed"


# ── Report aggregation ──────────────────────────────────────────

class TestReport:

    def test_empty_report(self, diag):
        r = diag.report()
        assert r.total_turns == 0
        assert r.hit_rate == 0.0
        assert r.healthy  # Not enough data

    def test_all_hits(self, diag):
        diag.record_turn(fingerprint="a", creation=500, read=0)  # Miss (warmup)
        diag.record_turn(fingerprint="a", creation=0, read=500)  # Hit
        diag.record_turn(fingerprint="a", creation=0, read=500)  # Hit
        r = diag.report()
        assert r.hits == 2
        assert r.misses == 1
        assert r.hit_rate == pytest.approx(2 / 3, abs=0.01)

    def test_all_misses(self, diag):
        for _ in range(4):
            diag.record_turn(fingerprint="a", creation=500, read=0)
        r = diag.report()
        assert r.misses == 4
        assert r.hit_rate == 0.0
        assert not r.healthy  # 4 turns, 0% hit rate

    def test_partial_hits(self, diag):
        diag.record_turn(fingerprint="a", creation=100, read=400)
        diag.record_turn(fingerprint="a", creation=100, read=400)
        diag.record_turn(fingerprint="a", creation=100, read=400)
        r = diag.report()
        assert r.partial_hits == 3
        assert r.effective_rate == pytest.approx(0.5, abs=0.01)

    def test_empty_turns(self, diag):
        diag.record_turn(fingerprint="a", creation=0, read=0)
        r = diag.report()
        assert r.empty_turns == 1

    def test_total_tokens(self, diag):
        diag.record_turn(fingerprint="a", creation=500, read=0)
        diag.record_turn(fingerprint="a", creation=0, read=1000)
        r = diag.report()
        assert r.total_creation_tokens == 500
        assert r.total_read_tokens == 1000


# ── Savings estimation ──────────────────────────────────────────

class TestSavings:

    def test_savings_all_reads(self, diag):
        """All reads = maximum savings (90%)."""
        diag.record_turn(fingerprint="a", creation=0, read=10000)
        r = diag.report()
        assert r.estimated_savings_pct == pytest.approx(0.9, abs=0.01)

    def test_savings_all_creation(self, diag):
        """All creation = negative savings (costs more)."""
        diag.record_turn(fingerprint="a", creation=10000, read=0)
        r = diag.report()
        assert r.estimated_savings_pct == 0.0  # Clamped to 0

    def test_savings_empty(self, diag):
        r = diag.report()
        assert r.estimated_savings_pct == 0.0


# ── Summary ─────────────────────────────────────────────────────

class TestSummary:

    def test_summary_format(self, diag):
        diag.record_turn(fingerprint="a", creation=500, read=0)
        diag.record_turn(fingerprint="a", creation=0, read=500)
        r = diag.report()
        s = r.summary()
        assert "2 turns" in s
        assert "hit_rate=" in s

    def test_summary_with_breaks(self, diag):
        diag.record_turn(fingerprint="a", creation=500, read=0)
        diag.record_turn(fingerprint="b", creation=500, read=0)
        r = diag.report()
        s = r.summary()
        assert "break" in s.lower()


# ── Reset ───────────────────────────────────────────────────────

class TestReset:

    def test_reset(self, diag):
        diag.record_turn(fingerprint="a", creation=500, read=0)
        diag.reset()
        r = diag.report()
        assert r.total_turns == 0
