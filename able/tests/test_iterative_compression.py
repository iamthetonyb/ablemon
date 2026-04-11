"""Tests for E8 — Iterative Context Compression.

Covers: existing summary detection, summary merging, section parsing,
iterative compaction preserving prior summaries, edge cases.
"""

import pytest

from able.core.session.context_compactor import ContextCompactor


@pytest.fixture
def compactor():
    return ContextCompactor()


# ── Summary detection ────────────────────────────────────────────

class TestSummaryDetection:

    def test_detects_existing_summary(self, compactor):
        msgs = [
            {
                "role": "system",
                "content": (
                    "[CONTEXT SUMMARY — 10 messages compacted]\n\n"
                    "**User requests:**\n  1. Fix auth bug\n\n"
                    "[END CONTEXT SUMMARY — conversation continues below]"
                ),
            },
        ]
        result = compactor._extract_existing_summary(msgs)
        assert result is not None
        assert "Fix auth bug" in result

    def test_returns_none_for_non_summary(self, compactor):
        msgs = [{"role": "system", "content": "You are a helpful assistant."}]
        assert compactor._extract_existing_summary(msgs) is None

    def test_returns_none_for_user_message(self, compactor):
        msgs = [{"role": "user", "content": "[CONTEXT SUMMARY foo"}]
        assert compactor._extract_existing_summary(msgs) is None

    def test_returns_none_for_empty(self, compactor):
        assert compactor._extract_existing_summary([]) is None

    def test_handles_malformed_summary(self, compactor):
        msgs = [{"role": "system", "content": "[CONTEXT SUMMARY — no end marker"}]
        result = compactor._extract_existing_summary(msgs)
        # Should return the full content as fallback
        assert result is not None


# ── Section parsing ──────────────────────────────────────────────

class TestSectionParsing:

    def test_parse_basic_sections(self):
        text = (
            "**User requests:**\n"
            "  1. Fix login\n"
            "  2. Add tests\n"
            "\n"
            "**Tool calls executed:**\n"
            "- read_file: auth.py\n"
        )
        sections = ContextCompactor._parse_summary_sections(text)
        assert "User requests" in sections
        assert len(sections["User requests"]) == 2
        assert "Tool calls executed" in sections
        assert len(sections["Tool calls executed"]) == 1

    def test_parse_empty_text(self):
        assert ContextCompactor._parse_summary_sections("") == {}

    def test_parse_no_sections(self):
        sections = ContextCompactor._parse_summary_sections("just some plain text")
        assert sections == {}


# ── Summary merging ──────────────────────────────────────────────

class TestSummaryMerging:

    def test_merge_distinct_sections(self, compactor):
        existing = "**User requests:**\n  1. Fix auth"
        new = "**Tool calls executed:**\n- read_file: auth.py"
        merged = compactor._merge_summaries(existing, new)
        assert "Fix auth" in merged
        assert "read_file" in merged

    def test_merge_overlapping_sections(self, compactor):
        existing = "**User requests:**\n  1. Fix auth\n  2. Add tests"
        new = "**User requests:**\n  1. Deploy service\n  2. Fix auth"
        merged = compactor._merge_summaries(existing, new)
        # New items should come first, deduped
        assert "Deploy service" in merged
        assert "Fix auth" in merged
        # "Fix auth" should only appear once
        assert merged.count("Fix auth") == 1

    def test_merge_respects_limits(self, compactor):
        # Create an existing summary with many items
        items = "\n".join(f"  {i}. Item {i}" for i in range(20))
        existing = f"**User requests:**\n{items}"
        new = "**User requests:**\n  1. New request"
        merged = compactor._merge_summaries(existing, new)
        # Should be capped at section limit (7 for User requests)
        lines = [l for l in merged.split("\n") if l.strip() and not l.startswith("**")]
        assert len(lines) <= 7

    def test_merge_empty_existing(self, compactor):
        merged = compactor._merge_summaries("", "**Issues noted:**\n  - Bug found")
        assert "Bug found" in merged

    def test_merge_empty_new(self, compactor):
        merged = compactor._merge_summaries("**Issues noted:**\n  - Old bug", "")
        assert "Old bug" in merged


# ── Iterative compaction integration ─────────────────────────────

class TestIterativeCompaction:

    def _make_messages(self, count, prefix="Message"):
        """Create test messages to fill context."""
        msgs = []
        for i in range(count):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({
                "role": role,
                "content": f"{prefix} {i}: " + "x" * 200,
            })
        return msgs

    def test_first_compaction_creates_summary(self, compactor):
        msgs = self._make_messages(20)
        result = compactor.compact_if_needed(msgs, context_limit=600)
        # Should have compacted
        assert len(result) < len(msgs)
        # First message should be a summary
        assert "[CONTEXT SUMMARY" in result[0]["content"]

    def test_second_compaction_merges_summary(self):
        """Simulate two rounds of compaction."""
        compactor = ContextCompactor(compact_threshold=0.5, compact_ratio=0.6)

        # Round 1: create initial messages and compact
        msgs = []
        for i in range(15):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({"role": role, "content": f"Round 1 msg {i}: " + "a" * 300})

        round1 = compactor.compact_if_needed(msgs, context_limit=800)
        assert "[CONTEXT SUMMARY" in round1[0]["content"]

        # Round 2: add more messages and compact again
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            round1.append({"role": role, "content": f"Round 2 msg {i}: " + "b" * 300})

        round2 = compactor.compact_if_needed(round1, context_limit=800)
        assert len(round2) < len(round1)
        # The summary should still be present
        assert "[CONTEXT SUMMARY" in round2[0]["content"]

    def test_compaction_without_prior_summary(self, compactor):
        """First compaction on messages that don't start with a summary."""
        msgs = [
            {"role": "user", "content": "Hello " + "x" * 500},
            {"role": "assistant", "content": "Hi there " + "y" * 500},
            {"role": "user", "content": "More questions " + "z" * 500},
            {"role": "assistant", "content": "More answers " + "w" * 500},
            {"role": "user", "content": "Final question " + "v" * 500},
        ]
        result = compactor.compact_if_needed(msgs, context_limit=400)
        if len(result) < len(msgs):
            assert "[CONTEXT SUMMARY" in result[0]["content"]

    def test_death_spiral_still_works_with_iterative(self):
        """Death spiral guard should still trigger with iterative compression."""
        compactor = ContextCompactor(max_attempts=2)
        msgs = [{"role": "user", "content": "x" * 10000}] * 5

        # First two compactions should work
        for _ in range(3):
            msgs = compactor.compact_if_needed(msgs, context_limit=50)
        # After max_attempts, it should stop trying
        assert compactor._compression_attempts <= 2
