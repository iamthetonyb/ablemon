"""Tests for C4 — Recency-Weighted Context Compression.

Covers: age-tiered truncation, read_file deduplication, hard cap,
stats, list content handling, edge cases.
"""

import pytest

from able.core.session.recency_compressor import (
    CompressionStats,
    RecencyCompressor,
)


@pytest.fixture
def rc():
    return RecencyCompressor()


def _make_msgs(count, content_len=500):
    msgs = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"Msg {i}: " + "x" * content_len})
    return msgs


# ── Age-tiered truncation ──────────────────────────────────────

class TestTruncation:

    def test_old_messages_truncated(self, rc):
        msgs = _make_msgs(10, content_len=500)
        result = rc.compress(msgs)
        # First 40% (4 msgs) → 180 char limit
        assert len(result[0]["content"]) <= 184  # 180 + "..."

    def test_mid_messages_truncated(self, rc):
        msgs = _make_msgs(10, content_len=500)
        result = rc.compress(msgs)
        # Messages 4-6 (mid tier) → 450 char limit
        assert len(result[5]["content"]) <= 454

    def test_recent_messages_preserved(self, rc):
        msgs = _make_msgs(10, content_len=500)
        result = rc.compress(msgs)
        # Last 30% → 900 char limit (500 < 900, so no truncation)
        assert len(result[-1]["content"]) == len(msgs[-1]["content"])

    def test_short_messages_untouched(self, rc):
        msgs = _make_msgs(10, content_len=50)
        result = rc.compress(msgs)
        for orig, comp in zip(msgs, result):
            assert comp["content"] == orig["content"]

    def test_empty_messages(self, rc):
        assert rc.compress([]) == []

    def test_single_message(self, rc):
        msgs = [{"role": "user", "content": "hello"}]
        result = rc.compress(msgs)
        assert len(result) == 1
        assert result[0]["content"] == "hello"

    def test_custom_limits(self):
        rc = RecencyCompressor(old_limit=50, mid_limit=100, recent_limit=200)
        msgs = _make_msgs(10, content_len=500)
        result = rc.compress(msgs)
        assert len(result[0]["content"]) <= 54


# ── Read file deduplication ─────────────────────────────────────

class TestDedupReads:

    def test_removes_duplicate_reads(self, rc):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "a.py"}},
            ]},
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "a.py"}},
            ]},
        ]
        result = rc.compress(msgs)
        assert len(result) == 2  # First read removed
        assert rc.last_stats.deduped_reads == 1

    def test_keeps_reads_if_written_between(self, rc):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "a.py"}},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Write", "input": {"path": "a.py"}},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "a.py"}},
            ]},
        ]
        result = rc.compress(msgs)
        assert len(result) == 3  # All kept

    def test_keeps_different_paths(self, rc):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "a.py"}},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "b.py"}},
            ]},
        ]
        result = rc.compress(msgs)
        assert len(result) == 2

    def test_dedup_disabled(self):
        rc = RecencyCompressor(dedup_reads=False)
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "a.py"}},
            ]},
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "a.py"}},
            ]},
        ]
        result = rc.compress(msgs)
        assert len(result) == 3  # No dedup

    def test_no_tool_messages(self, rc):
        msgs = _make_msgs(5)
        result = rc.compress(msgs)
        assert len(result) == 5


# ── Hard cap ────────────────────────────────────────────────────

class TestHardCap:

    def test_hard_cap_removes_old(self):
        rc = RecencyCompressor(hard_cap=500)
        msgs = _make_msgs(10, content_len=200)
        result = rc.compress(msgs)
        total = sum(len(m["content"]) for m in result)
        assert total <= 500

    def test_hard_cap_disabled_by_default(self):
        rc = RecencyCompressor()
        msgs = _make_msgs(10, content_len=200)
        result = rc.compress(msgs)
        assert len(result) == 10  # Nothing removed by hard cap


# ── List content handling ───────────────────────────────────────

class TestListContent:

    def test_truncates_text_blocks(self, rc):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "x" * 500},
            ]},
        ] * 10
        result = rc.compress(msgs)
        # Old messages should have truncated text
        first_block = result[0]["content"][0]
        assert len(first_block["text"]) <= 184

    def test_preserves_non_text_blocks(self, rc):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "test", "input": {}},
            ]},
        ]
        result = rc.compress(msgs)
        assert result[0]["content"][0]["type"] == "tool_use"


# ── Stats ───────────────────────────────────────────────────────

class TestStats:

    def test_compression_stats(self, rc):
        msgs = _make_msgs(10, content_len=500)
        rc.compress(msgs)
        stats = rc.last_stats
        assert stats is not None
        assert stats.input_messages == 10
        assert stats.output_messages == 10
        assert stats.compression_ratio > 0

    def test_no_stats_before_compress(self, rc):
        assert rc.last_stats is None
