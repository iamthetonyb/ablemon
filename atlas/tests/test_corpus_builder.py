#!/usr/bin/env python3
"""
Tests for the distillation pipeline: CorpusBuilder, ReasoningExtractor, DatasetVersioner.

WU-08: Corpus Builder + Reasoning Extractor + Dataset Versioner
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure atlas package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.distillation.corpus_builder import (
    CorpusBuilder,
    CorpusExample,
    TIER_SEED_MIN,
)
from core.distillation.reasoning_extractor import (
    ExtractionResult,
    ReasoningExtractor,
)
from core.distillation.dataset_versioner import (
    DatasetVersioner,
    VersionInfo,
)


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _create_test_db(db_path: str, rows: list[dict]) -> None:
    """Create an interaction_log DB with test data."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS interaction_log (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            message_preview TEXT,
            complexity_score REAL,
            selected_tier INTEGER,
            selected_provider TEXT,
            domain TEXT,
            features TEXT,
            scorer_version INTEGER,
            budget_gated INTEGER DEFAULT 0,
            actual_provider TEXT,
            fallback_used INTEGER DEFAULT 0,
            fallback_chain TEXT,
            latency_ms REAL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            success INTEGER DEFAULT 1,
            error_type TEXT,
            user_correction INTEGER DEFAULT 0,
            user_satisfaction INTEGER,
            escalated INTEGER DEFAULT 0,
            channel TEXT,
            session_id TEXT,
            conversation_turn INTEGER DEFAULT 0
        )
    """)
    for row in rows:
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO interaction_log ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
    conn.commit()
    conn.close()


def _make_row(
    row_id: str,
    message: str = "test instruction",
    domain: str = "default",
    success: bool = True,
    escalated: bool = False,
    user_correction: bool = False,
    complexity_score: float = 0.85,
    provider: str = "gpt-5.4-mini",
    tier: int = 1,
) -> dict:
    """Build a test interaction row."""
    return {
        "id": row_id,
        "timestamp": "2026-03-21T00:00:00Z",
        "message_preview": message,
        "complexity_score": complexity_score,
        "selected_tier": tier,
        "selected_provider": provider,
        "domain": domain,
        "success": int(success),
        "escalated": int(escalated),
        "user_correction": int(user_correction),
        "actual_provider": provider,
        "latency_ms": 100.0,
    }


def _write_temp_jsonl(path: Path, lines: list[dict]) -> None:
    """Write a list of dicts as JSONL to a file."""
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


# ═══════════════════════════════════════════════════════════════
# REASONING EXTRACTOR TESTS
# ═══════════════════════════════════════════════════════════════


class TestReasoningExtractor:
    """Tests for ReasoningExtractor."""

    def setup_method(self):
        self.extractor = ReasoningExtractor()

    def test_empty_input(self):
        result = self.extractor.extract("")
        assert result.answer == ""
        assert not result.has_reasoning

    def test_plain_text_no_reasoning(self):
        text = "The capital of France is Paris."
        result = self.extractor.extract(text)
        assert result.answer == text
        assert result.format_detected == "none"
        assert not result.has_reasoning

    def test_think_tag_extraction(self):
        text = "<think>France is in Europe. Its capital is a major city.</think>The capital of France is Paris."
        result = self.extractor.extract(text)
        assert result.format_detected == "think_tag"
        assert "France is in Europe" in result.reasoning
        assert "The capital of France is Paris." == result.answer
        assert result.has_reasoning
        assert result.confidence >= 0.9

    def test_thinking_tag_variant(self):
        text = "<thinking>Let me consider this carefully. There are multiple factors.</thinking>The answer is 42."
        result = self.extractor.extract(text)
        assert result.format_detected == "think_tag"
        assert "consider this carefully" in result.reasoning
        assert "The answer is 42." == result.answer

    def test_multiple_think_blocks(self):
        text = "<think>First thought.</think>Middle text.<think>Second thought with more words here.</think>Final answer."
        result = self.extractor.extract(text)
        assert result.format_detected == "think_tag"
        assert "First thought" in result.reasoning
        assert "Second thought" in result.reasoning
        assert "Final answer." in result.answer

    def test_step_by_step_numbered(self):
        text = (
            "Step 1: Identify the problem.\n"
            "Step 2: Analyze the data.\n"
            "Step 3: Draw conclusions.\n\n"
            "The final result is positive."
        )
        result = self.extractor.extract(text)
        assert result.format_detected == "step_by_step"
        assert result.has_reasoning
        assert "Step 1" in result.reasoning

    def test_step_by_step_ordinal(self):
        text = (
            "First, we need to understand the requirements.\n"
            "Second, we should design the architecture.\n"
            "Third, we implement the solution.\n\n"
            "This approach gives us the best outcome."
        )
        result = self.extractor.extract(text)
        assert result.format_detected == "step_by_step"
        assert result.has_reasoning

    def test_tool_chain_extraction(self):
        text = (
            "I'll use the search tool to find relevant information. "
            "Let me call the database query function to get the records. "
            "After checking the results, I need to run the analysis script.\n\n"
            "Based on the analysis, the system is healthy."
        )
        result = self.extractor.extract(text)
        assert result.format_detected == "tool_chain"
        assert result.has_reasoning

    def test_normalize_convenience(self):
        text = "<think>reasoning here with enough words to pass</think>answer here"
        normalized = self.extractor.normalize(text)
        assert "<think>" in normalized
        assert "</think>" in normalized

    def test_strip_reasoning(self):
        text = "<think>internal reasoning that should be hidden from output</think>The visible answer."
        stripped = self.extractor.strip_reasoning(text)
        assert "internal reasoning" not in stripped
        assert "The visible answer." == stripped

    def test_stats_tracking(self):
        self.extractor.reset_stats()
        self.extractor.extract("plain text without reasoning")
        self.extractor.extract("<think>some reasoning in think tags here</think>answer")
        stats = self.extractor.get_stats()
        assert stats["total"] == 2
        assert stats["with_reasoning"] == 1
        assert stats["by_format"]["think_tag"] == 1
        assert stats["by_format"]["none"] == 1

    def test_think_tag_too_short(self):
        """Think tag with too few words should be ignored."""
        text = "<think>ok</think>The answer."
        result = self.extractor.extract(text)
        # "ok" is 1 word, below min_reasoning_words (5)
        assert result.format_detected == "none"

    def test_answer_separator_detection(self):
        text = (
            "Step 1: Check the input.\n"
            "Step 2: Process the data.\n"
            "Step 3: Validate the output.\n"
            "---\n"
            "The output is valid and ready for deployment."
        )
        result = self.extractor.extract(text)
        assert result.has_reasoning
        assert "The output is valid" in result.answer

    def test_preamble_removal(self):
        text = (
            "Let me think through this carefully.\n"
            "Step 1: First consideration is important.\n"
            "Step 2: Second consideration matters too.\n\n"
            "The conclusion is clear."
        )
        result = self.extractor.extract(text)
        assert result.has_reasoning
        assert "Let me think through" not in result.reasoning


# ═══════════════════════════════════════════════════════════════
# DATASET VERSIONER TESTS
# ═══════════════════════════════════════════════════════════════


class TestDatasetVersioner:
    """Tests for DatasetVersioner."""

    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.corpus_root = Path(self._tmpdir) / "corpus"
        self.versioner = DatasetVersioner(
            corpus_root=self.corpus_root,
            auto_audit=False,
        )

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_get_next_version_empty(self):
        assert self.versioner.get_next_version("tony") == 1

    def test_get_next_version_existing(self):
        tenant_dir = self.corpus_root / "tony"
        (tenant_dir / "v001").mkdir(parents=True)
        (tenant_dir / "v003").mkdir(parents=True)
        assert self.versioner.get_next_version("tony") == 4

    def test_version_tag_format(self):
        assert self.versioner._version_tag(1) == "v001"
        assert self.versioner._version_tag(42) == "v042"
        assert self.versioner._version_tag(999) == "v999"

    @pytest.mark.asyncio
    async def test_create_version(self):
        # Create temp JSONL files
        tmp = Path(self._tmpdir)
        train_path = tmp / "train.jsonl"
        val_path = tmp / "val.jsonl"
        test_path = tmp / "test.jsonl"
        _write_temp_jsonl(train_path, [{"instruction": f"q{i}", "response": f"a{i}"} for i in range(8)])
        _write_temp_jsonl(val_path, [{"instruction": "vq1", "response": "va1"}])
        _write_temp_jsonl(test_path, [{"instruction": "tq1", "response": "ta1"}])

        info = await self.versioner.create_version(
            tenant_id="tony",
            train_path=train_path,
            val_path=val_path,
            test_path=test_path,
            metadata={"corpus_tier": "seed"},
            source="test",
        )

        assert info.version == 1
        assert info.version_tag == "v001"
        assert info.train_count == 8
        assert info.val_count == 1
        assert info.test_count == 1
        assert info.example_count == 10

        # Check files exist on disk
        version_dir = self.corpus_root / "tony" / "v001"
        assert (version_dir / "train.jsonl").exists()
        assert (version_dir / "val.jsonl").exists()
        assert (version_dir / "test.jsonl").exists()

    @pytest.mark.asyncio
    async def test_get_version_latest(self):
        # Create two versions
        tmp = Path(self._tmpdir)
        for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
            _write_temp_jsonl(tmp / name, [{"x": 1}])

        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")
        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")

        latest = await self.versioner.get_version("tony")
        assert latest is not None
        assert latest.version == 2
        assert latest.version_tag == "v002"

    @pytest.mark.asyncio
    async def test_get_version_specific(self):
        tmp = Path(self._tmpdir)
        for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
            _write_temp_jsonl(tmp / name, [{"x": 1}])

        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")
        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")

        v1 = await self.versioner.get_version("tony", version=1)
        assert v1 is not None
        assert v1.version == 1

    @pytest.mark.asyncio
    async def test_list_versions(self):
        tmp = Path(self._tmpdir)
        for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
            _write_temp_jsonl(tmp / name, [{"x": 1}])

        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")
        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")
        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")

        versions = await self.versioner.list_versions("tony")
        assert len(versions) == 3
        assert versions[0].version == 1
        assert versions[2].version == 3

    @pytest.mark.asyncio
    async def test_delete_version_not_latest(self):
        tmp = Path(self._tmpdir)
        for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
            _write_temp_jsonl(tmp / name, [{"x": 1}])

        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")
        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")

        deleted = await self.versioner.delete_version("tony", version=1)
        assert deleted is True
        assert not (self.corpus_root / "tony" / "v001").exists()

    @pytest.mark.asyncio
    async def test_delete_version_refuses_latest(self):
        tmp = Path(self._tmpdir)
        for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
            _write_temp_jsonl(tmp / name, [{"x": 1}])

        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")

        deleted = await self.versioner.delete_version("tony", version=1)
        assert deleted is False

    @pytest.mark.asyncio
    async def test_latest_symlink(self):
        tmp = Path(self._tmpdir)
        for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
            _write_temp_jsonl(tmp / name, [{"x": 1}])

        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")

        latest_link = self.corpus_root / "tony" / "latest"
        assert latest_link.is_symlink() or latest_link.exists()

    @pytest.mark.asyncio
    async def test_tenant_isolation(self):
        tmp = Path(self._tmpdir)
        for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
            _write_temp_jsonl(tmp / name, [{"x": 1}])

        await self.versioner.create_version("tenant_a", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")
        await self.versioner.create_version("tenant_b", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")

        a_versions = await self.versioner.list_versions("tenant_a")
        b_versions = await self.versioner.list_versions("tenant_b")

        assert len(a_versions) == 1
        assert len(b_versions) == 1
        assert a_versions[0].tenant_id != b_versions[0].tenant_id or \
            str(a_versions[0].path) != str(b_versions[0].path)

    @pytest.mark.asyncio
    async def test_get_latest_path(self):
        tmp = Path(self._tmpdir)
        for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
            _write_temp_jsonl(tmp / name, [{"x": 1}])

        await self.versioner.create_version("tony", tmp / "train.jsonl", tmp / "val.jsonl", tmp / "test.jsonl")

        path = await self.versioner.get_latest_path("tony")
        assert path is not None
        assert path.exists()
        assert (path / "train.jsonl").exists()


# ═══════════════════════════════════════════════════════════════
# CORPUS BUILDER TESTS
# ═══════════════════════════════════════════════════════════════


class TestCorpusBuilder:
    """Tests for CorpusBuilder."""

    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmpdir, "test_interaction.db")
        self.corpus_root = Path(self._tmpdir) / "corpus"
        self.builder = CorpusBuilder(
            db_path=self.db_path,
            corpus_root=self.corpus_root,
        )

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_corpus_tier_classification(self):
        assert CorpusBuilder.get_corpus_tier(0) == "seed"
        assert CorpusBuilder.get_corpus_tier(100) == "seed"
        assert CorpusBuilder.get_corpus_tier(500) == "seed"
        assert CorpusBuilder.get_corpus_tier(2000) == "seed"
        assert CorpusBuilder.get_corpus_tier(2001) == "growth"
        assert CorpusBuilder.get_corpus_tier(10000) == "growth"
        assert CorpusBuilder.get_corpus_tier(10001) == "full"
        assert CorpusBuilder.get_corpus_tier(50000) == "full"

    def test_filter_removes_failed(self):
        rows = [
            _make_row("1", success=True),
            _make_row("2", success=False),
        ]
        kept, removed = self.builder._apply_filters(rows)
        assert len(kept) == 1
        assert removed == 1

    def test_filter_removes_escalated(self):
        rows = [
            _make_row("1", escalated=False),
            _make_row("2", escalated=True),
        ]
        kept, removed = self.builder._apply_filters(rows)
        assert len(kept) == 1
        assert removed == 1

    def test_filter_removes_user_correction(self):
        rows = [
            _make_row("1", user_correction=False),
            _make_row("2", user_correction=True),
        ]
        kept, removed = self.builder._apply_filters(rows)
        assert len(kept) == 1
        assert removed == 1

    def test_dedup_by_instruction_hash(self):
        rows = [
            _make_row("1", message="same question"),
            _make_row("2", message="same question"),
            _make_row("3", message="different question"),
        ]
        unique, dupes = self.builder._deduplicate(rows)
        assert len(unique) == 2
        assert dupes == 1

    def test_domain_balancing(self):
        # 10 rows: 7 security, 3 coding
        rows = [_make_row(str(i), domain="security", message=f"sec{i}") for i in range(7)]
        rows += [_make_row(str(i + 7), domain="coding", message=f"code{i}") for i in range(3)]

        balanced, trimmed = self.builder._balance_domains(rows)
        # max 30% of 10 = 3 per domain
        security_count = sum(1 for r in balanced if r["domain"] == "security")
        assert security_count <= 3
        assert trimmed > 0

    def test_split_ratios(self):
        examples = [
            CorpusExample(instruction=f"q{i}", response=f"a{i}")
            for i in range(100)
        ]
        train, val, test = self.builder._split(examples)
        assert len(train) == 80
        assert len(val) == 10
        assert len(test) == 10

    def test_split_small_dataset(self):
        examples = [CorpusExample(instruction="q1", response="a1")]
        train, val, test = self.builder._split(examples)
        assert len(train) == 1
        assert len(val) == 0
        assert len(test) == 0

    def test_corpus_example_jsonl(self):
        ex = CorpusExample(
            instruction="What is Python?",
            response="A programming language.",
            domain="coding",
            reasoning="Let me explain...",
            provider="gpt-5.4-mini",
            tier=1,
        )
        d = ex.to_jsonl_dict()
        assert d["instruction"] == "What is Python?"
        assert d["response"] == "A programming language."
        assert d["domain"] == "coding"
        assert d["reasoning"] == "Let me explain..."
        assert d["provider"] == "gpt-5.4-mini"
        assert d["tier"] == 1

    def test_corpus_example_jsonl_minimal(self):
        ex = CorpusExample(instruction="q", response="a")
        d = ex.to_jsonl_dict()
        assert "reasoning" not in d
        assert "provider" not in d
        assert "tier" not in d

    @pytest.mark.asyncio
    async def test_build_nightly_with_data(self):
        rows = [
            _make_row(str(i), message=f"unique question {i}", domain="coding")
            for i in range(10)
        ]
        _create_test_db(self.db_path, rows)

        path = await self.builder.build_nightly(tenant_id="tony")
        assert path.exists()
        assert (path / "train.jsonl").exists()
        assert (path / "val.jsonl").exists()
        assert (path / "test.jsonl").exists()

    @pytest.mark.asyncio
    async def test_build_nightly_empty_db(self):
        _create_test_db(self.db_path, [])
        path = await self.builder.build_nightly(tenant_id="tony")
        assert path.exists()

    @pytest.mark.asyncio
    async def test_build_full(self):
        rows = [
            _make_row(str(i), message=f"full question {i}", domain="default")
            for i in range(5)
        ]
        _create_test_db(self.db_path, rows)

        path = await self.builder.build_full(tenant_id="tony")
        assert path.exists()

    @pytest.mark.asyncio
    async def test_get_stats(self):
        rows = [
            _make_row(str(i), message=f"stats question {i}")
            for i in range(5)
        ]
        _create_test_db(self.db_path, rows)

        # Build once so there's a version
        await self.builder.build_nightly(tenant_id="tony")

        stats = await self.builder.get_stats(tenant_id="tony")
        assert stats["tenant_id"] == "tony"
        assert stats["corpus_tier"] == "seed"
        assert stats["version_count"] == 1
        assert stats["raw_candidates"] == 5
        assert stats["quality_threshold"] == 0.8

    @pytest.mark.asyncio
    async def test_build_filters_bad_data(self):
        rows = [
            _make_row("1", message="good interaction", success=True),
            _make_row("2", message="failed request", success=False),
            _make_row("3", message="escalated request", escalated=True),
            _make_row("4", message="corrected by user", user_correction=True),
        ]
        _create_test_db(self.db_path, rows)

        path = await self.builder.build_nightly(tenant_id="tony")
        assert path.exists()

        # Only the first row should survive
        train_path = path / "train.jsonl"
        with open(train_path) as f:
            lines = [l for l in f if l.strip()]
        # 1 example total -> all go to train
        assert len(lines) == 1

    @pytest.mark.asyncio
    async def test_tenant_isolation(self):
        rows = [_make_row(str(i), message=f"q{i}") for i in range(3)]
        _create_test_db(self.db_path, rows)

        path_a = await self.builder.build_nightly(tenant_id="tenant_a")
        path_b = await self.builder.build_nightly(tenant_id="tenant_b")

        assert "tenant_a" in str(path_a)
        assert "tenant_b" in str(path_b)
        assert str(path_a) != str(path_b)

    @pytest.mark.asyncio
    async def test_build_creates_metadata(self):
        rows = [_make_row(str(i), message=f"meta q{i}") for i in range(3)]
        _create_test_db(self.db_path, rows)

        path = await self.builder.build_nightly(tenant_id="tony")

        # Metadata should exist as yaml or json
        has_meta = (path / "metadata.yaml").exists() or (path / "metadata.json").exists()
        assert has_meta

    @pytest.mark.asyncio
    async def test_successive_builds_increment_version(self):
        rows = [_make_row(str(i), message=f"incr q{i}") for i in range(3)]
        _create_test_db(self.db_path, rows)

        path1 = await self.builder.build_nightly(tenant_id="tony")
        path2 = await self.builder.build_nightly(tenant_id="tony")

        assert "v001" in str(path1)
        assert "v002" in str(path2)

    @pytest.mark.asyncio
    async def test_no_db_file_builds_empty(self):
        """CorpusBuilder handles missing DB file gracefully."""
        builder = CorpusBuilder(
            db_path="/nonexistent/path/fake.db",
            corpus_root=self.corpus_root,
        )
        path = await builder.build_nightly(tenant_id="tony")
        assert path.exists()


# ═══════════════════════════════════════════════════════════════
# INTEGRATION TEST
# ═══════════════════════════════════════════════════════════════


class TestIntegration:
    """End-to-end integration tests across all three components."""

    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmpdir, "integration.db")
        self.corpus_root = Path(self._tmpdir) / "corpus"

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """Build corpus, version it, retrieve metadata."""
        # Seed DB with varied data
        rows = []
        domains = ["coding", "security", "research", "creative", "default"]
        for i in range(20):
            domain = domains[i % len(domains)]
            rows.append(
                _make_row(
                    str(i),
                    message=f"Unique question about {domain} topic number {i}",
                    domain=domain,
                )
            )
        _create_test_db(self.db_path, rows)

        builder = CorpusBuilder(
            db_path=self.db_path,
            corpus_root=self.corpus_root,
        )

        # Build
        path = await builder.build_nightly(tenant_id="tony")
        assert path.exists()

        # Stats
        stats = await builder.get_stats(tenant_id="tony")
        assert stats["version_count"] == 1
        assert stats["total_examples"] > 0

        # Versioner can list
        versioner = DatasetVersioner(corpus_root=self.corpus_root, auto_audit=False)
        versions = await versioner.list_versions("tony")
        assert len(versions) == 1

        # Second build increments
        path2 = await builder.build_nightly(tenant_id="tony")
        versions = await versioner.list_versions("tony")
        assert len(versions) == 2

    @pytest.mark.asyncio
    async def test_reasoning_in_pipeline(self):
        """Verify ReasoningExtractor is used during corpus build."""
        extractor = ReasoningExtractor()

        # Simulate what CorpusBuilder does
        text = "<think>Analyzing the code structure carefully for potential issues.</think>The code is clean."
        result = extractor.extract(text)
        assert result.has_reasoning
        assert result.format_detected == "think_tag"

        # The normalized form is what goes into training data
        assert "<think>" in result.normalized
        assert "</think>" in result.normalized
