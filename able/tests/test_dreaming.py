"""Tests for C5 — Memory Dreaming / REM Cycle.

Covers: fact extraction, duplicate detection, stale pruning, full cycle.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from able.memory.dreaming import (
    REMCycle,
    REMReport,
    ExtractedFact,
    DuplicatePair,
)
from able.memory.temporal_graph import TemporalKnowledgeGraph


@pytest.fixture
def graph(tmp_path):
    return TemporalKnowledgeGraph(db_path=tmp_path / "test_graph.db")


@pytest.fixture
def cycle(graph):
    return REMCycle(graph=graph, memory=None)


# ── Fact extraction ───────────────────────────────────────────────

class TestFactExtraction:

    def test_is_pattern(self):
        facts = REMCycle.extract_facts("Acme Corp is a fintech startup.")
        assert len(facts) >= 1
        assert any(f.subject == "Acme Corp" for f in facts)

    def test_uses_pattern(self):
        facts = REMCycle.extract_facts("Tony uses Python for most projects.")
        assert any(f.predicate == "uses" for f in facts)

    def test_works_with_pattern(self):
        facts = REMCycle.extract_facts("Alice works with React and TypeScript.")
        assert any(f.predicate == "works_with" for f in facts)

    def test_possessive_pattern(self):
        facts = REMCycle.extract_facts("Client Acme's rate is $150/hr.")
        assert any(f.subject == "Client Acme" for f in facts)

    def test_kv_pattern(self):
        facts = REMCycle.extract_facts("\nstack: Python + React\nbudget: $5000/mo\n")
        assert len(facts) >= 2
        assert any(f.predicate == "has" for f in facts)

    def test_empty_text(self):
        facts = REMCycle.extract_facts("")
        assert facts == []

    def test_no_matches(self):
        facts = REMCycle.extract_facts("hello world this is just lowercase text")
        assert facts == []

    def test_dedup_within_extraction(self):
        """Same fact mentioned twice should only be extracted once."""
        text = "Acme Corp is a fintech startup. Acme Corp is a fintech startup."
        facts = REMCycle.extract_facts(text)
        subjects = [f.subject for f in facts if f.subject == "Acme Corp"]
        assert len(subjects) == 1

    def test_confidence_set(self):
        facts = REMCycle.extract_facts("Client Bob is a developer.")
        for f in facts:
            assert 0.0 < f.confidence <= 1.0

    def test_long_object_truncated(self):
        facts = REMCycle.extract_facts("Acme Corp is " + "x" * 500)
        for f in facts:
            assert len(f.object) <= 200


# ── Text similarity ───────────────────────────────────────────────

class TestTextSimilarity:

    def test_identical_strings(self):
        assert REMCycle._text_similarity("hello", "hello") == 1.0

    def test_completely_different(self):
        sim = REMCycle._text_similarity("abc", "xyz")
        assert sim < 0.3

    def test_similar_strings(self):
        sim = REMCycle._text_similarity("Python developer", "Python developers")
        assert sim > 0.7

    def test_empty_strings(self):
        assert REMCycle._text_similarity("", "") == 0.0
        assert REMCycle._text_similarity("hello", "") == 0.0

    def test_case_insensitive(self):
        sim = REMCycle._text_similarity("Hello World", "hello world")
        assert sim == 1.0


# ── Duplicate detection ──────────────────────────────────────────

class TestDuplicateDetection:

    def test_no_duplicates_in_empty_graph(self, cycle):
        dups = cycle._find_duplicates()
        assert dups == []

    def test_exact_duplicate_detected(self, cycle, graph):
        # Add same fact twice (different IDs)
        graph.add_triple("client_x", "rate", "$100/hr")
        # Manually insert second without auto-invalidation
        conn = graph._connect()
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO triples (subject, predicate, object, valid_from) VALUES (?, ?, ?, ?)",
                ("client_x", "rate", "$100/hr", now),
            )
            conn.commit()
        finally:
            conn.close()
        dups = cycle._find_duplicates()
        assert len(dups) >= 1
        assert dups[0].similarity >= 0.9

    def test_different_objects_not_duplicated(self, cycle, graph):
        graph.add_triple("client_x", "rate", "$100/hr")
        graph.add_triple("client_y", "rate", "$200/hr")
        dups = cycle._find_duplicates()
        # Different subjects, so no dups
        assert len(dups) == 0


# ── Merge duplicate ──────────────────────────────────────────────

class TestMergeDuplicate:

    def test_merge_keeps_longer(self, cycle, graph):
        graph.add_triple("project", "desc", "short")
        conn = graph._connect()
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO triples (subject, predicate, object, valid_from) VALUES (?, ?, ?, ?)",
                ("project", "desc", "much longer description with more detail", now),
            )
            conn.commit()
        finally:
            conn.close()

        dups = cycle._find_duplicates()
        if dups:
            result = cycle._merge_duplicate(dups[0])
            assert result is True
            # One should be invalidated
            current = graph.query_entity("project")
            assert len(current) == 1
            assert "longer" in current[0].object


# ── Stale pruning ────────────────────────────────────────────────

class TestStalePruning:

    def test_prune_stale_via_graph(self, graph):
        pruned = graph.prune_stale(days=90)
        assert pruned == 0  # Nothing to prune


# ── Full REM cycle ───────────────────────────────────────────────

class TestFullCycle:

    @pytest.mark.asyncio
    async def test_cycle_runs_with_mock_memory(self, graph):
        """Cycle should complete with a mock memory that returns nothing."""
        mock_mem = MagicMock()
        mock_mem.db.get_recent.return_value = []
        cycle = REMCycle(graph=graph, memory=mock_mem)
        report = await cycle.run()
        assert isinstance(report, REMReport)
        assert report.facts_extracted == 0  # Mock returns empty
        assert report.duration_ms > 0

    @pytest.mark.asyncio
    async def test_cycle_with_prefilled_graph(self, cycle, graph):
        """Cycle should prune and detect duplicates in existing graph."""
        graph.add_triple("test", "key", "value1")
        graph.add_triple("test", "key2", "value2")
        report = await cycle.run()
        assert isinstance(report, REMReport)
        assert report.stale_pruned == 0  # Nothing stale yet

    @pytest.mark.asyncio
    async def test_report_summary_format(self, cycle):
        report = await cycle.run()
        summary = report.summary()
        assert "REM cycle" in summary
        assert "facts" in summary
        assert "duplicates" in summary

    @pytest.mark.asyncio
    async def test_cycle_handles_errors_gracefully(self):
        """Errors in one phase should not crash the whole cycle."""
        broken_graph = MagicMock()
        broken_graph.stats.side_effect = RuntimeError("DB locked")
        broken_graph.prune_stale.side_effect = RuntimeError("DB locked")
        cycle = REMCycle(graph=broken_graph, memory=None)
        report = await cycle.run()
        assert len(report.errors) > 0
        assert report.duration_ms > 0


# ── REMReport ─────────────────────────────────────────────────────

class TestREMReport:

    def test_default_values(self):
        report = REMReport()
        assert report.facts_extracted == 0
        assert report.errors == []

    def test_summary_string(self):
        report = REMReport(facts_extracted=5, facts_added=3, duplicates_merged=1)
        s = report.summary()
        assert "5" in s
        assert "3" in s
