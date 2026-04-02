"""
Tests for the distillation pipeline:
  - ReasoningExtractor
  - CorpusBuilder
  - DatasetVersioner
"""

import json
from pathlib import Path

import pytest

from able.core.distillation.reasoning_extractor import ExtractionResult, ReasoningExtractor
from able.core.distillation.corpus_builder import CorpusBuilder, CorpusBuildResult
from able.core.distillation.dataset_versioner import DatasetVersioner, DatasetVersion
from able.core.distillation.models import TrainingPair


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def extractor():
    return ReasoningExtractor()


@pytest.fixture
def tmp_corpus(tmp_path):
    """Temporary corpus directory for builder/versioner tests."""
    return str(tmp_path / "corpus")


@pytest.fixture
def builder(tmp_corpus):
    return CorpusBuilder(corpus_dir=tmp_corpus)


@pytest.fixture
def versioner(tmp_corpus):
    return DatasetVersioner(corpus_dir=tmp_corpus)


def _make_pairs(
    n: int,
    domain: str = "default",
    quality: float = 0.9,
    escalated: bool = False,
    accepted: bool = True,
) -> list[dict]:
    """Generate n synthetic training pairs."""
    pairs = []
    for i in range(n):
        pairs.append({
            "prompt": f"Question {i} about {domain}",
            "response": f"Answer {i} for {domain}",
            "domain": domain,
            "quality_score": quality,
            "escalated": escalated,
            "response_accepted": accepted,
        })
    return pairs


# ══════════════════════════════════════════════════════════════════════
# ReasoningExtractor Tests
# ══════════════════════════════════════════════════════════════════════


class TestReasoningExtractor:
    def test_extract_think_tags(self, extractor):
        text = "<think>The user wants X. I should do Y.</think>\nHere is the answer."
        result = extractor.extract(text)
        assert result.method == "think_tags"
        assert result.thinking == "The user wants X. I should do Y."
        assert result.answer == "Here is the answer."

    def test_extract_think_tags_multiline(self, extractor):
        text = "<think>\nLine 1\nLine 2\n</think>\nFinal answer here."
        result = extractor.extract(text)
        assert result.method == "think_tags"
        assert "Line 1" in result.thinking
        assert "Line 2" in result.thinking
        assert result.answer == "Final answer here."

    def test_extract_multiple_think_blocks(self, extractor):
        text = "<think>First thought</think>\nMiddle text.\n<think>Second thought</think>\nEnd."
        result = extractor.extract(text)
        assert result.method == "think_tags"
        assert "First thought" in result.thinking
        assert "Second thought" in result.thinking

    def test_extract_thinking_preamble(self, extractor):
        text = "Thinking: I need to analyze the data carefully.\n\nThe answer is 42."
        result = extractor.extract(text)
        assert result.method == "thinking_preamble"
        assert "analyze the data" in result.thinking
        assert result.answer == "The answer is 42."

    def test_extract_step_by_step(self, extractor):
        text = "Step 1: Gather data\nStep 2: Analyze\nStep 3: Conclude\nThe final result is X."
        result = extractor.extract(text)
        assert result.method == "step_by_step"
        assert "Step 1" in result.thinking
        assert "Step 3" in result.thinking
        assert "final result" in result.answer

    def test_extract_let_me_think(self, extractor):
        text = "Let me think about this carefully.\nThe answer is straightforward: do Y."
        result = extractor.extract(text)
        assert result.method == "step_by_step"
        assert "Let me think" in result.thinking

    def test_extract_tool_chain(self, extractor):
        text = "I'll use the search tool to find relevant data.\nCalling the API now.\nResults show that X is correct."
        result = extractor.extract(text)
        assert result.method == "tool_chain"
        assert "search tool" in result.thinking
        assert "Results show" in result.answer

    def test_extract_no_reasoning(self, extractor):
        text = "The capital of France is Paris."
        result = extractor.extract(text)
        assert result.method == "none"
        assert result.thinking is None
        assert result.answer == "The capital of France is Paris."

    def test_extract_empty_string(self, extractor):
        result = extractor.extract("")
        assert result.method == "none"
        assert result.thinking is None
        assert result.answer == ""

    def test_extract_none_input(self, extractor):
        result = extractor.extract(None)
        assert result.method == "none"

    def test_normalize(self, extractor):
        output = extractor.normalize("I need to think", "The answer is 42")
        assert output == "<think>I need to think</think>\nThe answer is 42"

    def test_normalize_strips_whitespace(self, extractor):
        output = extractor.normalize("  reasoning  ", "  answer  ")
        assert output == "<think>reasoning</think>\nanswer"


# ══════════════════════════════════════════════════════════════════════
# CorpusBuilder Tests
# ══════════════════════════════════════════════════════════════════════


class TestCorpusBuilder:
    def test_filters_low_quality(self, builder):
        pairs = _make_pairs(5, quality=0.5) + _make_pairs(3, quality=0.9)
        filtered = builder._filter_pairs(pairs)
        assert len(filtered) == 3

    def test_filters_escalated(self, builder):
        pairs = _make_pairs(3, escalated=True) + _make_pairs(2)
        filtered = builder._filter_pairs(pairs)
        assert len(filtered) == 2

    def test_filters_rejected_responses(self, builder):
        pairs = _make_pairs(3, accepted=False) + _make_pairs(2)
        filtered = builder._filter_pairs(pairs)
        assert len(filtered) == 2

    def test_deduplicates_by_content_hash(self, builder):
        pairs = [
            {"prompt": "hello", "response": "world"},
            {"prompt": "hello", "response": "world"},  # exact duplicate
            {"prompt": "hello", "response": "world2"},  # different response
        ]
        deduped = builder._deduplicate(pairs)
        assert len(deduped) == 2

    def test_domain_balance_caps_overrepresented(self, builder):
        # 10 coding + 2 security = 12 total
        # max 30% of 12 = 3.6 -> 3
        pairs = _make_pairs(10, domain="coding") + _make_pairs(2, domain="security")
        balanced = builder._balance_domains(pairs)
        domain_counts = {}
        for p in balanced:
            d = p["domain"]
            domain_counts[d] = domain_counts.get(d, 0) + 1
        # Coding should be capped
        assert domain_counts["coding"] <= int(12 * 0.30) + 1  # allow rounding
        assert domain_counts["security"] == 2

    def test_domain_balance_preserves_small_domains(self, builder):
        pairs = _make_pairs(3, domain="a") + _make_pairs(3, domain="b") + _make_pairs(3, domain="c")
        balanced = builder._balance_domains(pairs)
        # All within 30% of 9 = 2.7 -> 2 cap, but 3 domains * 3 each
        # Each domain has 3 out of 9 = 33%, just slightly over
        assert len(balanced) >= 6  # At least 2 from each domain

    def test_split_dataset_ratios(self, builder):
        pairs = _make_pairs(100)
        train, val, test = builder._split_dataset(pairs)
        total = len(train) + len(val) + len(test)
        assert total == 100
        # Allow some tolerance for rounding
        assert len(train) >= 80
        assert len(val) >= 5
        assert len(test) >= 1

    def test_split_preserves_all_pairs(self, builder):
        pairs = _make_pairs(50, domain="a") + _make_pairs(50, domain="b")
        train, val, test = builder._split_dataset(pairs)
        assert len(train) + len(val) + len(test) == 100

    def test_build_full_writes_jsonl(self, builder, tmp_corpus):
        # Use 4 domains so balancer doesn't cap (5 each = 25% < 30%)
        pairs = (
            _make_pairs(5, domain="a")
            + _make_pairs(5, domain="b")
            + _make_pairs(5, domain="c")
            + _make_pairs(5, domain="d")
        )
        result = builder.build_full(pairs)
        assert result.total == 20
        assert result.version == "v001"

        output_dir = Path(result.output_dir)
        assert (output_dir / "train.jsonl").exists()
        assert (output_dir / "val.jsonl").exists()
        assert (output_dir / "test.jsonl").exists()

        # Verify JSONL is valid
        with open(output_dir / "train.jsonl") as f:
            for line in f:
                json.loads(line.strip())  # Should not raise

    def test_build_full_writes_pair_records_and_chatml(self, builder):
        pairs = [
            TrainingPair(
                id="p1",
                prompt="Explain dependency injection in Python applications with a concrete example.",
                response="Dependency injection passes collaborators into an object instead of constructing them internally.",
                domain="coding",
                quality_score=0.92,
                source="able_cli",
                teacher_model="gpt-5.4",
            ),
            TrainingPair(
                id="p2",
                prompt="Explain CSRF mitigation strategies for a web application.",
                response="Use same-site cookies, synchronizer tokens, and explicit origin validation.",
                domain="security",
                quality_score=0.95,
                source="claude_code",
                teacher_model="claude-opus-4-6",
            ),
            TrainingPair(
                id="p3",
                prompt="Explain how schema migrations should be rolled out safely in production.",
                response="Prefer additive migrations first, backfill, then cut traffic, then remove old columns later.",
                domain="devops",
                quality_score=0.94,
                source="claude_code",
                teacher_model="claude-opus-4-6",
            ),
            TrainingPair(
                id="p4",
                prompt="Explain how to summarize a long research report without losing key findings.",
                response="Anchor the summary around findings, evidence, and open questions rather than section order.",
                domain="research",
                quality_score=0.91,
                source="chatgpt",
                teacher_model="gpt-5.4",
            ),
        ]
        result = builder.build_full(pairs)
        output_dir = Path(result.output_dir)
        assert (output_dir / "train_pairs.jsonl").exists()
        assert (output_dir / "val_pairs.jsonl").exists()
        assert (output_dir / "test_pairs.jsonl").exists()

        with open(output_dir / "train.jsonl") as f:
            train_record = json.loads(next(line for line in f if line.strip()))
        assert "conversations" in train_record
        assert train_record["conversations"][0]["role"] == "system"

    def test_load_latest_pairs_reads_pair_artifacts(self, builder):
        pairs = _make_pairs(8, domain="coding", quality=0.9) + _make_pairs(
            8, domain="security", quality=0.92
        )
        builder.build_full(pairs)
        loaded = builder._load_latest_pairs("default")
        assert loaded
        assert "prompt" in loaded[0]
        assert "response" in loaded[0]

    def test_build_full_writes_metadata(self, builder, tmp_corpus):
        pairs = _make_pairs(20)
        result = builder.build_full(pairs)
        output_dir = Path(result.output_dir)

        # Metadata should exist in yaml or json format
        has_meta = (output_dir / "metadata.yaml").exists() or (
            output_dir / "metadata.json"
        ).exists()
        assert has_meta

    def test_build_empty_input(self, builder):
        result = builder.build_full([])
        assert result.total == 0
        assert result.tier == "seed"
        assert result.train_count == 0

    def test_build_increments_version(self, builder):
        result1 = builder.build_full(_make_pairs(10))
        result2 = builder.build_full(_make_pairs(10))
        assert result1.version == "v001"
        assert result2.version == "v002"

    def test_build_nightly_merges(self, builder):
        # First build with diverse domains
        builder.build_full(
            _make_pairs(5, domain="coding") + _make_pairs(5, domain="research")
        )
        # Nightly with new domain pairs
        result = builder.build_nightly(_make_pairs(5, domain="security"))
        # Should contain pairs from all 3 domains (after balancing/dedup)
        assert result.total >= 3  # At least some from each domain survive
        assert len(result.domains) == 3  # All three domains present
        assert "security" in result.domains

    def test_corpus_tier_classification(self, builder):
        assert builder._classify_tier(100) == "seed"
        assert builder._classify_tier(1999) == "seed"
        assert builder._classify_tier(2000) == "growth"
        assert builder._classify_tier(9999) == "growth"
        assert builder._classify_tier(10000) == "full"

    def test_get_stats_empty(self, builder):
        stats = builder.get_stats()
        assert stats["versions"] == 0

    def test_get_stats_after_build(self, builder):
        # Use 4 domains so balancer doesn't cap (5 each = 25% < 30%)
        pairs = (
            _make_pairs(5, domain="a")
            + _make_pairs(5, domain="b")
            + _make_pairs(5, domain="c")
            + _make_pairs(5, domain="d")
        )
        builder.build_full(pairs)
        stats = builder.get_stats()
        assert stats["versions"] == 1
        assert stats["latest"] == "v001"
        assert stats["total_pairs"] == 20

    def test_custom_quality_threshold(self, tmp_corpus):
        builder = CorpusBuilder(corpus_dir=tmp_corpus, quality_threshold=0.95)
        pairs = _make_pairs(5, quality=0.9) + _make_pairs(3, quality=0.96)
        filtered = builder._filter_pairs(pairs)
        assert len(filtered) == 3

    def test_reasoning_enrichment(self, builder):
        pairs = [
            {
                "prompt": "Think step by step",
                "response": "<think>Analyzing...</think>\nThe answer is 42.",
                "domain": "default",
                "quality_score": 0.9,
            }
        ]
        enriched = builder._enrich_reasoning(pairs)
        assert enriched[0]["reasoning_method"] == "think_tags"
        assert enriched[0]["thinking"] == "Analyzing..."
        assert enriched[0]["clean_answer"] == "The answer is 42."

    def test_per_tenant_isolation(self, builder, tmp_corpus):
        pairs_a = _make_pairs(10, domain="a")
        pairs_b = _make_pairs(10, domain="b")

        result_a = builder.build_full(pairs_a, tenant_id="tenant_a")
        result_b = builder.build_full(pairs_b, tenant_id="tenant_b")

        assert "tenant_a" in result_a.output_dir
        assert "tenant_b" in result_b.output_dir
        assert result_a.output_dir != result_b.output_dir


# ══════════════════════════════════════════════════════════════════════
# DatasetVersioner Tests
# ══════════════════════════════════════════════════════════════════════


class TestDatasetVersioner:
    def test_create_version(self, versioner, tmp_corpus):
        metadata = {"total": 100, "domains": {"coding": 50, "security": 50}, "avg_quality": 0.92}
        version = versioner.create_version("v001", metadata)
        assert version.version == "v001"
        assert version.pair_count == 100
        assert version.avg_quality == 0.92

    def test_create_version_creates_directory(self, versioner, tmp_corpus):
        versioner.create_version("v001", {"total": 10})
        version_dir = Path(tmp_corpus) / "default" / "v001"
        assert version_dir.exists()

    def test_list_versions_empty(self, versioner):
        versions = versioner.list_versions()
        assert versions == []

    def test_list_versions_ordered(self, versioner):
        versioner.create_version("v002", {"total": 20, "created_at": "2026-03-20T00:00:00+00:00"})
        versioner.create_version("v001", {"total": 10, "created_at": "2026-03-19T00:00:00+00:00"})
        versioner.create_version("v003", {"total": 30, "created_at": "2026-03-21T00:00:00+00:00"})

        versions = versioner.list_versions()
        assert len(versions) == 3
        assert versions[0].version == "v001"
        assert versions[-1].version == "v003"

    def test_get_latest(self, versioner):
        versioner.create_version("v001", {"total": 10, "created_at": "2026-03-19T00:00:00+00:00"})
        versioner.create_version("v002", {"total": 20, "created_at": "2026-03-20T00:00:00+00:00"})

        latest = versioner.get_latest()
        assert latest is not None
        assert latest.version == "v002"

    def test_get_latest_empty(self, versioner):
        assert versioner.get_latest() is None

    def test_update_symlink(self, versioner, tmp_corpus):
        versioner.create_version("v001", {"total": 10})
        versioner.create_version("v002", {"total": 20})

        link_path = Path(tmp_corpus) / "default" / "latest"
        # After v002 creation, symlink should point to v002
        if link_path.is_symlink():
            target = link_path.resolve()
            assert target.name == "v002"

    def test_symlink_updates_on_new_version(self, versioner, tmp_corpus):
        versioner.create_version("v001", {"total": 10})
        link_path = Path(tmp_corpus) / "default" / "latest"

        if link_path.is_symlink():
            assert link_path.resolve().name == "v001"

        versioner.create_version("v002", {"total": 20})
        if link_path.is_symlink():
            assert link_path.resolve().name == "v002"

    def test_diff_versions(self, versioner):
        versioner.create_version(
            "v001",
            {"total": 100, "avg_quality": 0.85, "domains": {"coding": 60, "security": 40}},
        )
        versioner.create_version(
            "v002",
            {"total": 150, "avg_quality": 0.90, "domains": {"coding": 70, "security": 50, "research": 30}},
        )

        diff = versioner.diff_versions("v001", "v002")
        assert diff["pair_count_delta"] == 50
        assert diff["quality_delta"] == pytest.approx(0.05, abs=0.001)
        assert "research" in diff["domains_added"]
        assert diff["domains_removed"] == []
        assert diff["domains_changed"]["coding"] == 10

    def test_diff_missing_version(self, versioner):
        versioner.create_version("v001", {"total": 10})
        diff = versioner.diff_versions("v001", "v999")
        assert "error" in diff

    def test_per_tenant_isolation(self, versioner, tmp_corpus):
        versioner.create_version("v001", {"total": 10}, tenant_id="alpha")
        versioner.create_version("v001", {"total": 20}, tenant_id="beta")

        alpha_versions = versioner.list_versions("alpha")
        beta_versions = versioner.list_versions("beta")

        assert len(alpha_versions) == 1
        assert len(beta_versions) == 1
        assert alpha_versions[0].pair_count == 10
        assert beta_versions[0].pair_count == 20
