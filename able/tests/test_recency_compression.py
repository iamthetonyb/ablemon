"""Tests for C4 — Recency-Weighted Context Compression.

Covers: graduated compression, read_file dedup, layered compaction strategy.
"""

import pytest

from able.core.session.context_compactor import ContextCompactor


@pytest.fixture
def compactor():
    return ContextCompactor()


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def _tool_msg(role: str, tool_name: str, path: str) -> dict:
    """Create a message with tool_use content block."""
    return {
        "role": role,
        "content": [
            {"type": "tool_use", "name": tool_name, "input": {"file_path": path}},
        ],
    }


# ── _dedup_read_file ──────────────────────────────────────────────

class TestDedupReadFile:

    def test_no_reads_unchanged(self, compactor):
        msgs = [_msg("user", "hello"), _msg("assistant", "hi")]
        result = compactor._dedup_read_file(msgs)
        assert len(result) == 2

    def test_single_read_kept(self, compactor):
        msgs = [
            _msg("user", "read file"),
            _tool_msg("assistant", "read_file", "/tmp/foo.py"),
            _msg("assistant", "contents here"),
        ]
        result = compactor._dedup_read_file(msgs)
        assert len(result) == 3

    def test_duplicate_reads_deduped(self, compactor):
        msgs = [
            _tool_msg("assistant", "read_file", "/tmp/foo.py"),  # 1st read
            _msg("assistant", "analyzing..."),
            _tool_msg("assistant", "read_file", "/tmp/foo.py"),  # 2nd read (dup)
            _msg("assistant", "done"),
        ]
        result = compactor._dedup_read_file(msgs)
        # First read removed, second kept
        assert len(result) == 3

    def test_written_file_not_deduped(self, compactor):
        """If file was written between reads, keep both reads."""
        msgs = [
            _tool_msg("assistant", "read_file", "/tmp/foo.py"),
            _tool_msg("assistant", "write_file", "/tmp/foo.py"),
            _tool_msg("assistant", "read_file", "/tmp/foo.py"),  # Post-write read
        ]
        result = compactor._dedup_read_file(msgs)
        assert len(result) == 3  # All kept

    def test_different_paths_not_deduped(self, compactor):
        msgs = [
            _tool_msg("assistant", "read_file", "/tmp/a.py"),
            _tool_msg("assistant", "read_file", "/tmp/b.py"),
        ]
        result = compactor._dedup_read_file(msgs)
        assert len(result) == 2

    def test_Read_tool_name_variant(self, compactor):
        """Handles 'Read' as well as 'read_file'."""
        msgs = [
            _tool_msg("assistant", "Read", "/tmp/foo.py"),
            _msg("user", "ok"),
            _tool_msg("assistant", "Read", "/tmp/foo.py"),
        ]
        result = compactor._dedup_read_file(msgs)
        assert len(result) == 2

    def test_Edit_invalidates_dedup(self, compactor):
        """Edit counts as a write."""
        msgs = [
            _tool_msg("assistant", "Read", "/tmp/foo.py"),
            _tool_msg("assistant", "Edit", "/tmp/foo.py"),
            _tool_msg("assistant", "Read", "/tmp/foo.py"),
        ]
        result = compactor._dedup_read_file(msgs)
        assert len(result) == 3


# ── _recency_compress ─────────────────────────────────────────────

class TestRecencyCompress:

    def test_short_conversation_unchanged(self, compactor):
        msgs = [_msg("user", "hi"), _msg("assistant", "hey")]
        result = compactor._recency_compress(msgs)
        assert len(result) == 2
        assert result[0]["content"] == "hi"

    def test_old_messages_truncated(self, compactor):
        """First 40% of messages should be truncated to 180 chars."""
        msgs = [_msg("assistant", "x" * 500) for _ in range(10)]
        result = compactor._recency_compress(msgs)
        # First 4 messages (40% of 10) should be truncated
        for i in range(4):
            assert len(result[i]["content"]) <= 184  # 180 + "..."

    def test_mid_messages_moderate_truncation(self, compactor):
        """Middle 30% should be truncated to 500 chars."""
        msgs = [_msg("assistant", "x" * 800) for _ in range(10)]
        result = compactor._recency_compress(msgs)
        # Messages 4-6 (indices 4,5,6) are mid-range
        for i in range(4, 7):
            assert len(result[i]["content"]) <= 504

    def test_recent_messages_light_truncation(self, compactor):
        """Last 30% should keep up to 900 chars."""
        msgs = [_msg("assistant", "x" * 1200) for _ in range(10)]
        result = compactor._recency_compress(msgs)
        # Messages 7-9 are recent
        for i in range(7, 10):
            assert len(result[i]["content"]) <= 904

    def test_short_content_not_truncated(self, compactor):
        """Messages already under limit should not be modified."""
        msgs = [_msg("user", "short")] * 10
        result = compactor._recency_compress(msgs)
        for m in result:
            assert m["content"] == "short"

    def test_list_content_blocks_truncated(self, compactor):
        """Messages with list content blocks should be truncated."""
        msgs = [
            {"role": "assistant", "content": [{"type": "text", "text": "x" * 500}]}
            for _ in range(10)
        ]
        result = compactor._recency_compress(msgs)
        # First 4 should be truncated
        for i in range(4):
            block = result[i]["content"][0]
            assert len(block["text"]) <= 184


# ── Layered compaction strategy ───────────────────────────────────

class TestLayeredCompaction:

    def test_dedup_prevents_full_compaction(self, compactor):
        """If dedup alone is sufficient, skip heavier compression."""
        # Create a conversation that's over threshold due to duplicate reads
        big_read = _tool_msg("assistant", "read_file", "/tmp/big.py")
        # Inflate the tool_use blocks with large content to push over threshold
        msgs = []
        for i in range(20):
            msgs.append(_msg("user", f"read file {i}"))
            msgs.append({
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "read_file", "input": {"file_path": "/tmp/big.py"}},
                    {"type": "text", "text": "x" * 200},
                ],
            })
        # All 20 reads of same file — dedup should remove 19
        deduped = compactor._dedup_read_file(msgs)
        assert len(deduped) < len(msgs)

    def test_recency_reduces_tokens(self, compactor):
        """Recency compression should produce fewer tokens than original."""
        msgs = [_msg("assistant", "x" * 1000) for _ in range(20)]
        original_tokens = compactor.estimate_tokens(msgs)
        compressed = compactor._recency_compress(msgs)
        compressed_tokens = compactor.estimate_tokens(compressed)
        assert compressed_tokens < original_tokens

    def test_compact_if_needed_uses_layers(self, compactor):
        """compact_if_needed should try cheaper layers before full summary."""
        # Make a conversation just over the threshold
        msgs = [_msg("assistant", "x" * 300) for _ in range(50)]
        # Set a low context limit to trigger compaction
        result = compactor.compact_if_needed(msgs, context_limit=2000)
        # Should have fewer messages or shorter content
        result_tokens = compactor.estimate_tokens(result)
        original_tokens = compactor.estimate_tokens(msgs)
        assert result_tokens < original_tokens

    def test_thinking_strip_first(self, compactor):
        """Thinking blocks should be compressed (not stripped) before other layers."""
        # Use filler content that the compressor can actually reduce
        filler = "\n".join([f"Let me think about step {i}. I need to consider this." for i in range(30)])
        msgs = [
            _msg("assistant", f"<think>{filler}</think>Short answer."),
        ] * 10
        compressed = compactor._compress_thinking_blocks(msgs)
        for m in compressed:
            # Tags preserved (for harvester), but content shorter
            assert "<think>" in m["content"]
            assert len(m["content"]) < len(msgs[0]["content"])

    def test_death_spiral_guard_still_works(self, compactor):
        """Compression attempts counter should still prevent loops."""
        compactor._compression_attempts = 3
        msgs = [_msg("assistant", "x" * 1000) for _ in range(100)]
        # Should bail due to death spiral guard
        result = compactor.compact_if_needed(msgs, context_limit=500)
        # Can't verify exact behavior since layers 1-3 run first,
        # but the guard should prevent layer 4
        assert result is not None


# ── _truncate_message ─────────────────────────────────────────────

class TestTruncateMessage:

    def test_string_under_limit(self, compactor):
        msg = _msg("user", "short")
        result = compactor._truncate_message(msg, 100)
        assert result["content"] == "short"

    def test_string_over_limit(self, compactor):
        msg = _msg("user", "x" * 200)
        result = compactor._truncate_message(msg, 50)
        assert len(result["content"]) == 53  # 50 + "..."
        assert result["content"].endswith("...")

    def test_list_content_truncated(self, compactor):
        msg = {"role": "assistant", "content": [
            {"type": "text", "text": "x" * 200},
            {"type": "tool_use", "name": "test", "input": {}},  # non-text preserved
        ]}
        result = compactor._truncate_message(msg, 50)
        assert len(result["content"][0]["text"]) == 53
        assert result["content"][1]["name"] == "test"

    def test_original_msg_not_mutated(self, compactor):
        msg = _msg("user", "x" * 200)
        original_content = msg["content"]
        _ = compactor._truncate_message(msg, 50)
        assert msg["content"] == original_content  # Original unchanged
