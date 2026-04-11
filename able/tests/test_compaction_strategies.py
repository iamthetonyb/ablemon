"""Tests for F1 — Pluggable Compaction Strategy Registry.

Covers: built-in strategies, registry, selection, chain selection, stats.
"""

import pytest

from able.core.session.compaction_strategies import (
    AggressiveSummaryStrategy,
    CompactionStrategyRegistry,
    DedupReadFileStrategy,
    StripThinkingStrategy,
    TruncateOldStrategy,
)


@pytest.fixture
def registry():
    return CompactionStrategyRegistry()


def _make_msgs(count, content_len=200):
    msgs = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"Message {i}: " + "x" * content_len})
    return msgs


# ── Strip thinking ───────────────────────────────────────────────

class TestStripThinking:

    def test_removes_think_blocks(self):
        s = StripThinkingStrategy()
        msgs = [
            {"role": "assistant", "content": "<think>internal</think>Visible answer"},
            {"role": "user", "content": "question"},
        ]
        result = s.compact(msgs)
        assert "<think>" not in result[0]["content"]
        assert "Visible answer" in result[0]["content"]

    def test_leaves_non_assistant(self):
        s = StripThinkingStrategy()
        msgs = [{"role": "user", "content": "<think>not stripped</think>"}]
        result = s.compact(msgs)
        assert "<think>" in result[0]["content"]

    def test_no_change_when_no_thinking(self):
        s = StripThinkingStrategy()
        msgs = [{"role": "assistant", "content": "plain answer"}]
        result = s.compact(msgs)
        assert result[0]["content"] == "plain answer"


# ── Truncate old ─────────────────────────────────────────────────

class TestTruncateOld:

    def test_truncates_old_messages(self):
        s = TruncateOldStrategy(old_limit=50, mid_limit=100, recent_limit=200)
        msgs = _make_msgs(10, content_len=300)
        result = s.compact(msgs)
        # First 40% (4 msgs) should be truncated to ~50 chars
        assert len(result[0]["content"]) <= 54  # 50 + "..."

    def test_preserves_recent(self):
        s = TruncateOldStrategy(old_limit=50, mid_limit=100, recent_limit=5000)
        msgs = _make_msgs(10, content_len=300)
        result = s.compact(msgs)
        # Last 30% should be mostly preserved
        assert len(result[-1]["content"]) > 100

    def test_no_change_for_short_list(self):
        s = TruncateOldStrategy()
        msgs = _make_msgs(3)
        result = s.compact(msgs)
        assert len(result) == 3


# ── Aggressive summary ───────────────────────────────────────────

class TestAggressiveSummary:

    def test_reduces_message_count(self):
        s = AggressiveSummaryStrategy()
        msgs = _make_msgs(10)
        result = s.compact(msgs)
        assert len(result) < len(msgs)
        assert "[Compacted" in result[0]["content"]

    def test_preserves_min_messages(self):
        s = AggressiveSummaryStrategy(compact_ratio=0.9)
        msgs = _make_msgs(10)
        result = s.compact(msgs)
        assert len(result) >= 4  # 3 tail + 1 summary

    def test_no_change_for_tiny(self):
        s = AggressiveSummaryStrategy()
        msgs = _make_msgs(2)
        result = s.compact(msgs)
        assert len(result) == 2


# ── Dedup reads ──────────────────────────────────────────────────

class TestDedupReads:

    def test_removes_duplicate_reads(self):
        s = DedupReadFileStrategy()
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "auth.py"}},
            ]},
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "auth.py"}},
            ]},
        ]
        result = s.compact(msgs)
        assert len(result) == 2  # First read removed, second kept

    def test_keeps_reads_if_file_written(self):
        s = DedupReadFileStrategy()
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "auth.py"}},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Write", "input": {"path": "auth.py"}},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "auth.py"}},
            ]},
        ]
        result = s.compact(msgs)
        assert len(result) == 3  # All kept bc file was written

    def test_no_tool_messages(self):
        s = DedupReadFileStrategy()
        msgs = _make_msgs(5)
        result = s.compact(msgs)
        assert len(result) == 5  # No change


# ── Registry ─────────────────────────────────────────────────────

class TestRegistry:

    def test_builtins_loaded(self, registry):
        assert len(registry.available()) == 4

    def test_get_strategy(self, registry):
        s = registry.get("strip-thinking")
        assert s is not None
        assert s.name == "strip-thinking"

    def test_get_nonexistent(self, registry):
        assert registry.get("nope") is None

    def test_register_custom(self, registry):
        class Custom:
            name = "custom"
            aggressiveness = 0.5
            def compact(self, messages, budget_tokens=0, estimate_fn=None):
                return messages
        registry.register(Custom())
        assert registry.get("custom") is not None

    def test_select_gentle(self, registry):
        s = registry.select(budget_ratio=0.8)
        assert s.aggressiveness <= 0.2

    def test_select_aggressive(self, registry):
        s = registry.select(budget_ratio=0.1)
        assert s.aggressiveness >= 0.7

    def test_select_tool_heavy(self, registry):
        s = registry.select(budget_ratio=0.6, content_type="tool-heavy")
        assert s.name == "dedup-reads"

    def test_select_chain_gentle(self, registry):
        chain = registry.select_chain(budget_ratio=0.8)
        assert len(chain) <= 2

    def test_select_chain_aggressive(self, registry):
        chain = registry.select_chain(budget_ratio=0.1)
        assert len(chain) == 4

    def test_stats(self, registry):
        stats = registry.stats()
        assert stats["count"] == 4
        assert "strip-thinking" in stats["strategies"]
