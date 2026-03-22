"""Tests for the distillation validation gate.

Covers:
- GateDecision logic for all 4 outcomes (DEPLOY, ITERATE, BLOCK, KEEP_PREVIOUS)
- ComparisonRunner output scoring heuristics
- Domain breakdown in validation results
- YAML eval config validity
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from atlas.core.distillation.validation import (
    ComparisonRunner,
    GateDecision,
    StageResult,
    ValidationGate,
    ValidationResult,
)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

EVAL_DIR = Path(__file__).resolve().parent.parent / "evals"


def _make_stage(stage: int, name: str, passed: bool, rate: float = 1.0, **kw) -> StageResult:
    return StageResult(
        stage=stage,
        name=name,
        passed=passed,
        pass_rate=rate,
        details=kw.get("details", {}),
        errors=kw.get("errors", []),
    )


# ──────────────────────────────────────────────
# ValidationGate decision logic
# ──────────────────────────────────────────────

class TestValidationGateDecisions:
    """Test that the decision matrix maps stage results correctly."""

    def setup_method(self):
        self.gate = ValidationGate(eval_dir=str(EVAL_DIR))

    def test_all_pass_returns_deploy(self):
        stages = [
            _make_stage(1, "Evals", True, 0.95),
            _make_stage(2, "Comparison", True, 0.90),
            _make_stage(3, "Security", True, 0.98),
            _make_stage(4, "Regression", True, 0.92),
        ]
        assert self.gate._decide(stages) == GateDecision.DEPLOY

    def test_stage1_fail_returns_iterate(self):
        stages = [
            _make_stage(1, "Evals", False, 0.60),
            _make_stage(2, "Comparison", True, 0.90),
            _make_stage(3, "Security", True, 0.98),
            _make_stage(4, "Regression", True, 0.92),
        ]
        assert self.gate._decide(stages) == GateDecision.ITERATE

    def test_stage2_fail_returns_iterate(self):
        stages = [
            _make_stage(1, "Evals", True, 0.85),
            _make_stage(2, "Comparison", False, 0.55),
            _make_stage(3, "Security", True, 0.98),
            _make_stage(4, "Regression", True, 0.92),
        ]
        assert self.gate._decide(stages) == GateDecision.ITERATE

    def test_stage3_fail_returns_block(self):
        stages = [
            _make_stage(1, "Evals", True, 0.85),
            _make_stage(2, "Comparison", True, 0.90),
            _make_stage(3, "Security", False, 0.50),
            _make_stage(4, "Regression", True, 0.92),
        ]
        assert self.gate._decide(stages) == GateDecision.BLOCK

    def test_stage4_fail_returns_keep_previous(self):
        stages = [
            _make_stage(1, "Evals", True, 0.85),
            _make_stage(2, "Comparison", True, 0.90),
            _make_stage(3, "Security", True, 0.98),
            _make_stage(4, "Regression", False, 0.70),
        ]
        assert self.gate._decide(stages) == GateDecision.KEEP_PREVIOUS

    def test_stage3_fail_takes_priority_over_stage4(self):
        """Security block must override regression result."""
        stages = [
            _make_stage(1, "Evals", True, 0.85),
            _make_stage(2, "Comparison", True, 0.90),
            _make_stage(3, "Security", False, 0.40),
            _make_stage(4, "Regression", False, 0.70),
        ]
        assert self.gate._decide(stages) == GateDecision.BLOCK

    def test_stage1_and_stage2_both_fail_returns_iterate(self):
        stages = [
            _make_stage(1, "Evals", False, 0.50),
            _make_stage(2, "Comparison", False, 0.45),
            _make_stage(3, "Security", True, 0.99),
            _make_stage(4, "Regression", True, 0.92),
        ]
        assert self.gate._decide(stages) == GateDecision.ITERATE


# ──────────────────────────────────────────────
# Full pipeline integration (mocked stages)
# ──────────────────────────────────────────────

class TestValidationGateRun:
    """Test the full run() pipeline with mocked sub-stages."""

    def setup_method(self):
        self.gate = ValidationGate(eval_dir=str(EVAL_DIR))

    @pytest.mark.asyncio
    async def test_run_deploy_all_pass(self):
        """All stages pass -> DEPLOY."""
        with patch.object(self.gate, "_run_promptfoo", return_value=(7, 7, [])):
            with patch.object(
                self.gate._comparison_runner,
                "compare",
                new_callable=AsyncMock,
                return_value={
                    "total": 5,
                    "model_a_wins": 1,
                    "model_b_wins": 2,
                    "ties": 2,
                    "quality_delta": 0.05,
                    "per_prompt": [],
                },
            ):
                result = await self.gate.run(
                    candidate_model="qwen3.5-27b-atlas-v1",
                    test_data_path=None,
                    previous_model=None,
                    teacher_model="opus-4.6",
                )

        assert result.decision == GateDecision.DEPLOY
        assert result.model_name == "qwen3.5-27b-atlas"
        assert result.model_version == "v1"
        assert len(result.stages) == 4
        assert result.overall_pass_rate > 0

    @pytest.mark.asyncio
    async def test_run_block_on_security_failure(self):
        """Stage 3 security failure -> BLOCK, only 3 stages run."""

        def mock_promptfoo(config_path, model):
            if "security-redteam" in str(config_path):
                return (2, 10, [])  # Only 20% pass — below 95% threshold
            return (7, 7, [])

        with patch.object(self.gate, "_run_promptfoo", side_effect=mock_promptfoo):
            result = await self.gate.run(
                candidate_model="qwen3.5-27b-atlas-v1",
                test_data_path=None,
                previous_model=None,
            )

        assert result.decision == GateDecision.BLOCK
        # Stage 4 should NOT be present — security failure stops the pipeline.
        assert len(result.stages) == 3
        assert any("MUST NOT" in r for r in result.recommendations)


# ──────────────────────────────────────────────
# ComparisonRunner scoring
# ──────────────────────────────────────────────

class TestComparisonRunnerScoring:
    """Test the heuristic output scoring in ComparisonRunner."""

    def setup_method(self):
        self.runner = ComparisonRunner()

    def test_empty_output_scores_zero(self):
        assert self.runner._score_output("any prompt", "") == 0.0
        assert self.runner._score_output("any prompt", "   ") == 0.0

    def test_short_output_scores_low(self):
        score = self.runner._score_output("explain X", "X is a thing.")
        assert 0.0 < score < 0.5

    def test_substantial_output_scores_higher(self):
        long_output = (
            "Because of the architecture constraints, the system uses a queue-based "
            "approach. First, requests are validated. Second, they enter the processing "
            "pipeline. However, if the queue is full, the system applies backpressure. "
            "Therefore, the client must implement retry logic with exponential backoff. "
            "This means the system can handle burst traffic without degrading.\n\n"
            "- Step 1: Validate input\n"
            "- Step 2: Enqueue\n"
            "- Step 3: Process asynchronously\n"
        )
        score = self.runner._score_output("explain the queue system", long_output)
        assert score > 0.5

    def test_hallucination_markers_reduce_score(self):
        clean = "The server runs on port 5432. Because PostgreSQL defaults to this port."
        hallucinated = (
            "As an AI, I cannot verify this. My training data suggests that "
            "as of my last update, the server reportedly runs on some port. "
            "I believe that maybe it's 5432."
        )
        clean_score = self.runner._score_output("what port?", clean)
        hall_score = self.runner._score_output("what port?", hallucinated)
        assert clean_score > hall_score

    def test_relevance_boosts_score(self):
        prompt = "explain PostgreSQL indexing strategies"
        relevant = "PostgreSQL supports B-tree, hash, GiST, and GIN indexing strategies."
        irrelevant = "The weather today is sunny with a chance of rain."

        rel_score = self.runner._score_output(prompt, relevant)
        irr_score = self.runner._score_output(prompt, irrelevant)
        assert rel_score > irr_score

    def test_structured_output_bonus(self):
        plain = "Install it. Configure it. Run it."
        structured = (
            "## Installation\n"
            "- Run `pip install package`\n"
            "- Configure via `config.yaml`\n\n"
            "```bash\npython main.py\n```\n"
        )
        plain_score = self.runner._score_output("how to install", plain)
        struct_score = self.runner._score_output("how to install", structured)
        assert struct_score > plain_score

    def test_score_bounded_zero_to_one(self):
        """Score should never exceed [0, 1]."""
        extreme = (
            "Because therefore however consequently thus first second finally "
            "in contrast as a result this means which leads to the reason " * 20
        )
        score = self.runner._score_output("test", extreme)
        assert 0.0 <= score <= 1.0


# ──────────────────────────────────────────────
# ComparisonRunner.compare()
# ──────────────────────────────────────────────

class TestComparisonRunnerCompare:
    """Test the compare() method with a mock _generate."""

    @pytest.mark.asyncio
    async def test_compare_returns_correct_structure(self):
        class MockRunner(ComparisonRunner):
            async def _generate(self, model: str, prompt: str) -> str:
                if model == "teacher":
                    return "Detailed expert answer because of deep reasoning."
                return "Short answer."

        runner = MockRunner()
        result = await runner.compare(
            prompts=["explain X", "explain Y"],
            model_a="teacher",
            model_b="student",
        )

        assert result["total"] == 2
        assert result["model_a_wins"] + result["model_b_wins"] + result["ties"] == 2
        assert "quality_delta" in result
        assert len(result["per_prompt"]) == 2

    @pytest.mark.asyncio
    async def test_compare_tie_within_margin(self):
        class EqualRunner(ComparisonRunner):
            async def _generate(self, model: str, prompt: str) -> str:
                return "Both models give the same answer because of reasons."

        runner = EqualRunner(tie_margin=0.10)
        result = await runner.compare(
            prompts=["test"],
            model_a="a",
            model_b="b",
        )
        assert result["ties"] == 1


# ──────────────────────────────────────────────
# Domain breakdown
# ──────────────────────────────────────────────

class TestDomainBreakdown:
    """Verify domain_breakdown populates from stage 1 details."""

    def test_domain_breakdown_present_in_result(self):
        gate = ValidationGate()
        result = gate._build_result(
            decision=GateDecision.DEPLOY,
            stages=[
                _make_stage(
                    1,
                    "Evals",
                    True,
                    0.90,
                    details={
                        "domain_scores": {
                            "tool_use": 0.85,
                            "skill_adherence": 0.92,
                            "reasoning": 0.88,
                        }
                    },
                ),
            ],
            domain_breakdown={"tool_use": 0.85, "skill_adherence": 0.92, "reasoning": 0.88},
            model_name="test",
            model_version="v1",
            recommendations=[],
        )
        assert result.domain_breakdown["tool_use"] == 0.85
        assert result.domain_breakdown["reasoning"] == 0.88
        assert len(result.domain_breakdown) == 3


# ──────────────────────────────────────────────
# Recommendations
# ──────────────────────────────────────────────

class TestRecommendations:

    def test_deploy_recommendation(self):
        gate = ValidationGate()
        stages = [_make_stage(1, "Evals", True)]
        recs = gate._generate_recommendations(stages, GateDecision.DEPLOY)
        assert any("Safe to deploy" in r for r in recs)

    def test_failed_stage1_recommendation(self):
        gate = ValidationGate()
        stages = [
            _make_stage(
                1, "Evals", False, 0.6,
                details={"domain_scores": {"tool_use": 0.5, "reasoning": 0.9}},
            )
        ]
        recs = gate._generate_recommendations(stages, GateDecision.ITERATE)
        assert any("tool_use" in r for r in recs)

    def test_security_failure_recommendation(self):
        gate = ValidationGate()
        stages = [_make_stage(3, "Security", False, 0.4)]
        recs = gate._generate_recommendations(stages, GateDecision.BLOCK)
        assert any("MUST NOT deploy" in r for r in recs)


# ──────────────────────────────────────────────
# Eval YAML config validity
# ──────────────────────────────────────────────

_DISTILLATION_YAMLS = [
    "eval-distillation-tool-use.yaml",
    "eval-distillation-skill-adherence.yaml",
    "eval-distillation-reasoning.yaml",
    "eval-distillation-security-redteam.yaml",
]


class TestEvalYamlConfigs:
    """Verify the promptfoo YAML configs are valid and well-structured."""

    @pytest.mark.parametrize("filename", _DISTILLATION_YAMLS)
    def test_yaml_is_parseable(self, filename):
        path = EVAL_DIR / filename
        assert path.exists(), f"{filename} not found at {path}"
        with open(path) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict)

    @pytest.mark.parametrize("filename", _DISTILLATION_YAMLS)
    def test_yaml_has_required_keys(self, filename):
        path = EVAL_DIR / filename
        with open(path) as f:
            data = yaml.safe_load(f)
        assert "description" in data
        assert "providers" in data
        assert "tests" in data
        assert "prompts" in data

    @pytest.mark.parametrize("filename", _DISTILLATION_YAMLS)
    def test_yaml_has_tests_with_assertions(self, filename):
        path = EVAL_DIR / filename
        with open(path) as f:
            data = yaml.safe_load(f)
        tests = data["tests"]
        assert len(tests) >= 5, f"{filename} should have at least 5 tests"
        for test in tests:
            assert "assert" in test, f"Test missing assertions in {filename}"
            assert "vars" in test, f"Test missing vars in {filename}"

    def test_security_redteam_has_at_least_7_vectors(self):
        path = EVAL_DIR / "eval-distillation-security-redteam.yaml"
        with open(path) as f:
            data = yaml.safe_load(f)
        assert len(data["tests"]) >= 7, "Security red-team needs at least 7 attack vectors"


# ──────────────────────────────────────────────
# Model ID parsing
# ──────────────────────────────────────────────

class TestModelIdParsing:

    def test_versioned_model(self):
        name, version = ValidationGate._parse_model_id("qwen3.5-27b-atlas-v1")
        assert name == "qwen3.5-27b-atlas"
        assert version == "v1"

    def test_unversioned_model(self):
        name, version = ValidationGate._parse_model_id("opus-4.6")
        assert name == "opus-4.6"
        assert version == "unknown"

    def test_multi_v_model(self):
        name, version = ValidationGate._parse_model_id("model-v2-fine-v3")
        assert name == "model-v2-fine"
        assert version == "v3"
