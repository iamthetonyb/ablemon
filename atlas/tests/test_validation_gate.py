"""
Tests for the distillation validation gate and comparison runner.

Covers:
- ValidationGate: 4-stage pipeline, decision matrix, stage skipping
- ComparisonRunner: side-by-side comparison, grading, aggregation
- Data models: StageResult, ValidationResult, ComparisonReport
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure atlas package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.distillation.validation.validation_gate import (
    Decision,
    StageResult,
    StageStatus,
    ValidationConfig,
    ValidationGate,
    ValidationResult,
)
from core.distillation.validation.comparison_runner import (
    ComparisonReport,
    ComparisonRunner,
    PromptComparison,
    PromptResult,
)


# ═══════════════════════════════════════════════════════════════
# DATA MODEL TESTS
# ═══════════════════════════════════════════════════════════════


class TestStageResult:
    """Tests for StageResult dataclass."""

    def test_passed_property_true(self):
        r = StageResult(stage=1, name="test", status=StageStatus.PASSED)
        assert r.passed is True

    def test_passed_property_false(self):
        r = StageResult(stage=1, name="test", status=StageStatus.FAILED)
        assert r.passed is False

    def test_passed_property_skipped(self):
        r = StageResult(stage=1, name="test", status=StageStatus.SKIPPED)
        assert r.passed is False

    def test_defaults(self):
        r = StageResult(stage=1, name="test", status=StageStatus.PASSED)
        assert r.score == 0.0
        assert r.details == {}
        assert r.duration_ms == 0.0
        assert r.error == ""


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_passed_deploy(self):
        r = ValidationResult(decision=Decision.DEPLOY)
        assert r.passed is True

    def test_not_passed_iterate(self):
        r = ValidationResult(decision=Decision.ITERATE)
        assert r.passed is False

    def test_not_passed_block(self):
        r = ValidationResult(decision=Decision.BLOCK)
        assert r.passed is False

    def test_not_passed_keep_previous(self):
        r = ValidationResult(decision=Decision.KEEP_PREVIOUS)
        assert r.passed is False

    def test_summary(self):
        r = ValidationResult(
            model_name="test-model",
            decision=Decision.DEPLOY,
            stages=[
                StageResult(stage=1, name="s1", status=StageStatus.PASSED, score=0.9),
                StageResult(stage=2, name="s2", status=StageStatus.PASSED, score=0.85),
            ],
        )
        s = r.summary()
        assert "DEPLOY" in s
        assert "test-model" in s
        assert "S1" in s

    def test_to_dict(self):
        r = ValidationResult(
            model_name="test-model",
            decision=Decision.ITERATE,
            stages=[
                StageResult(stage=1, name="s1", status=StageStatus.FAILED, score=0.5),
            ],
        )
        d = r.to_dict()
        assert d["decision"] == "iterate"
        assert d["model_name"] == "test-model"
        assert len(d["stages"]) == 1
        assert d["stages"][0]["status"] == "failed"

    def test_has_id_and_timestamp(self):
        r = ValidationResult()
        assert r.id  # non-empty UUID
        assert r.timestamp  # non-empty ISO timestamp


class TestComparisonReport:
    """Tests for ComparisonReport dataclass."""

    def test_summary(self):
        r = ComparisonReport(
            teacher_model="teacher",
            student_model="student",
            teacher_avg_score=0.90,
            student_avg_score=0.80,
            quality_ratio=0.889,
            prompt_count=10,
        )
        s = r.summary()
        assert "teacher" in s.lower() or "Teacher" in s
        assert "student" in s.lower() or "Student" in s

    def test_to_dict(self):
        r = ComparisonReport(teacher_model="t", student_model="s")
        d = r.to_dict()
        assert "teacher_model" in d
        assert "student_model" in d
        assert "teacher_avg_score" in d


class TestPromptResult:
    """Tests for PromptResult dataclass."""

    def test_defaults(self):
        r = PromptResult()
        assert r.prompt == ""
        assert r.response == ""
        assert r.quality_score == 0.0
        assert r.hallucination_detected is False


# ═══════════════════════════════════════════════════════════════
# DECISION MATRIX TESTS
# ═══════════════════════════════════════════════════════════════


class TestDecisionMatrix:
    """Tests for the ValidationGate decision matrix."""

    def _gate(self) -> ValidationGate:
        return ValidationGate(config=ValidationConfig())

    def test_all_pass_deploys(self):
        gate = self._gate()
        stages = [
            StageResult(stage=1, name="s1", status=StageStatus.PASSED),
            StageResult(stage=2, name="s2", status=StageStatus.PASSED),
            StageResult(stage=3, name="s3", status=StageStatus.PASSED),
            StageResult(stage=4, name="s4", status=StageStatus.PASSED),
        ]
        assert gate._decide(stages) == Decision.DEPLOY

    def test_skipped_counts_as_pass(self):
        gate = self._gate()
        stages = [
            StageResult(stage=1, name="s1", status=StageStatus.PASSED),
            StageResult(stage=2, name="s2", status=StageStatus.SKIPPED),
            StageResult(stage=3, name="s3", status=StageStatus.PASSED),
            StageResult(stage=4, name="s4", status=StageStatus.SKIPPED),
        ]
        assert gate._decide(stages) == Decision.DEPLOY

    def test_stage3_fail_blocks(self):
        """Security failure is always a hard block."""
        gate = self._gate()
        stages = [
            StageResult(stage=1, name="s1", status=StageStatus.PASSED),
            StageResult(stage=2, name="s2", status=StageStatus.PASSED),
            StageResult(stage=3, name="s3", status=StageStatus.FAILED),
            StageResult(stage=4, name="s4", status=StageStatus.PASSED),
        ]
        assert gate._decide(stages) == Decision.BLOCK

    def test_stage3_fail_overrides_stage4_fail(self):
        """Security block takes priority over regression."""
        gate = self._gate()
        stages = [
            StageResult(stage=1, name="s1", status=StageStatus.PASSED),
            StageResult(stage=2, name="s2", status=StageStatus.PASSED),
            StageResult(stage=3, name="s3", status=StageStatus.FAILED),
            StageResult(stage=4, name="s4", status=StageStatus.FAILED),
        ]
        assert gate._decide(stages) == Decision.BLOCK

    def test_stage4_fail_keeps_previous(self):
        gate = self._gate()
        stages = [
            StageResult(stage=1, name="s1", status=StageStatus.PASSED),
            StageResult(stage=2, name="s2", status=StageStatus.PASSED),
            StageResult(stage=3, name="s3", status=StageStatus.PASSED),
            StageResult(stage=4, name="s4", status=StageStatus.FAILED),
        ]
        assert gate._decide(stages) == Decision.KEEP_PREVIOUS

    def test_stage1_fail_iterates(self):
        gate = self._gate()
        stages = [
            StageResult(stage=1, name="s1", status=StageStatus.FAILED),
            StageResult(stage=2, name="s2", status=StageStatus.PASSED),
            StageResult(stage=3, name="s3", status=StageStatus.PASSED),
            StageResult(stage=4, name="s4", status=StageStatus.PASSED),
        ]
        assert gate._decide(stages) == Decision.ITERATE

    def test_stage2_fail_iterates(self):
        gate = self._gate()
        stages = [
            StageResult(stage=1, name="s1", status=StageStatus.PASSED),
            StageResult(stage=2, name="s2", status=StageStatus.FAILED),
            StageResult(stage=3, name="s3", status=StageStatus.PASSED),
            StageResult(stage=4, name="s4", status=StageStatus.PASSED),
        ]
        assert gate._decide(stages) == Decision.ITERATE

    def test_stage1_and_2_fail_iterates(self):
        gate = self._gate()
        stages = [
            StageResult(stage=1, name="s1", status=StageStatus.FAILED),
            StageResult(stage=2, name="s2", status=StageStatus.FAILED),
            StageResult(stage=3, name="s3", status=StageStatus.PASSED),
            StageResult(stage=4, name="s4", status=StageStatus.PASSED),
        ]
        assert gate._decide(stages) == Decision.ITERATE


# ═══════════════════════════════════════════════════════════════
# STAGE EXECUTION TESTS
# ═══════════════════════════════════════════════════════════════


class TestStageExecution:
    """Tests for individual stage execution."""

    def test_unknown_stage_returns_error(self):
        gate = ValidationGate()
        result = asyncio.run(gate.run_stage(99, model_path="test"))
        assert result.status == StageStatus.ERROR
        assert "Unknown stage" in result.error

    def test_skipped_stage(self):
        gate = ValidationGate()
        result = asyncio.run(
            gate.run_stage(1, model_path="test", skip=True)
        )
        assert result.status == StageStatus.SKIPPED
        assert result.score == 1.0

    def test_stage2_skipped_without_runner(self):
        gate = ValidationGate()
        result = asyncio.run(
            gate.run_stage(2, model_path="test")
        )
        assert result.status == StageStatus.SKIPPED
        assert "No ComparisonRunner" in result.details.get("reason", "")

    def test_stage2_skipped_without_prompts(self):
        runner = ComparisonRunner()
        gate = ValidationGate(comparison_runner=runner)
        result = asyncio.run(
            gate.run_stage(2, model_path="test", test_prompts=None)
        )
        assert result.status == StageStatus.SKIPPED
        assert "No test prompts" in result.details.get("reason", "")

    def test_stage4_skipped_without_previous(self):
        gate = ValidationGate()
        result = asyncio.run(
            gate.run_stage(4, model_path="test", previous_model_path=None)
        )
        assert result.status == StageStatus.SKIPPED

    def test_stage4_skipped_without_runner(self):
        gate = ValidationGate()
        result = asyncio.run(
            gate.run_stage(4, model_path="test", previous_model_path="old-model")
        )
        assert result.status == StageStatus.SKIPPED
        assert "No ComparisonRunner" in result.details.get("reason", "")


# ═══════════════════════════════════════════════════════════════
# PROMPTFOO PARSING TESTS
# ═══════════════════════════════════════════════════════════════


class TestPromptfooParsing:
    """Tests for promptfoo output parsing."""

    def test_parse_valid_json(self):
        gate = ValidationGate()
        output = '{"results": [{"gradingResult": {"componentResults": [{"pass": true}, {"pass": true}, {"pass": false}]}}]}'
        result = gate._parse_promptfoo_output(output)
        assert result["pass_count"] == 2
        assert result["total_count"] == 3

    def test_parse_all_pass(self):
        gate = ValidationGate()
        output = '{"results": [{"gradingResult": {"componentResults": [{"pass": true}, {"pass": true}]}}]}'
        result = gate._parse_promptfoo_output(output)
        assert result["pass_count"] == 2
        assert result["total_count"] == 2

    def test_parse_empty_results(self):
        gate = ValidationGate()
        output = '{"results": []}'
        result = gate._parse_promptfoo_output(output)
        assert result["pass_count"] == 0
        assert result["total_count"] == 0

    def test_parse_invalid_json(self):
        gate = ValidationGate()
        result = gate._parse_promptfoo_output("not json at all")
        assert result["pass_count"] == 0
        assert "error" in result

    def test_parse_collects_failed_plugins(self):
        gate = ValidationGate()
        output = '{"results": [{"gradingResult": {"componentResults": [{"pass": false, "assertion": {"value": "prompt-injection"}}, {"pass": true}]}}]}'
        result = gate._parse_promptfoo_output(output)
        assert result["pass_count"] == 1
        assert result["total_count"] == 2
        assert "prompt-injection" in result["failed_plugins"]

    def test_missing_config_returns_zero(self):
        gate = ValidationGate()
        result = gate._run_promptfoo_eval(
            "/nonexistent/path/eval.yaml", "test-model"
        )
        assert result["pass_count"] == 0
        assert result["total_count"] == 0
        assert "not found" in result.get("error", "").lower()


# ═══════════════════════════════════════════════════════════════
# STAGE 1 EVAL TESTS
# ═══════════════════════════════════════════════════════════════


class TestStage1Eval:
    """Tests for Stage 1 — Promptfoo Eval Suite."""

    def test_stage1_passes_above_threshold(self):
        config = ValidationConfig(eval_pass_threshold=0.75)
        gate = ValidationGate(config=config)

        with patch.object(gate, "_run_promptfoo_eval") as mock_eval:
            mock_eval.return_value = {"pass_count": 8, "total_count": 10}
            result = asyncio.run(
                gate._run_stage_1_eval(model_path="test")
            )

        assert result.status == StageStatus.PASSED
        assert result.score == 0.8

    def test_stage1_fails_below_threshold(self):
        config = ValidationConfig(eval_pass_threshold=0.80)
        gate = ValidationGate(config=config)

        with patch.object(gate, "_run_promptfoo_eval") as mock_eval:
            mock_eval.return_value = {"pass_count": 7, "total_count": 10}
            result = asyncio.run(
                gate._run_stage_1_eval(model_path="test")
            )

        assert result.status == StageStatus.FAILED
        assert result.score == 0.7

    def test_stage1_handles_zero_tests(self):
        gate = ValidationGate()

        with patch.object(gate, "_run_promptfoo_eval") as mock_eval:
            mock_eval.return_value = {"pass_count": 0, "total_count": 0}
            result = asyncio.run(
                gate._run_stage_1_eval(model_path="test")
            )

        assert result.status == StageStatus.FAILED
        assert result.score == 0.0


# ═══════════════════════════════════════════════════════════════
# STAGE 2 COMPARISON TESTS
# ═══════════════════════════════════════════════════════════════


class TestStage2Comparison:
    """Tests for Stage 2 — Teacher-Student Comparison."""

    def test_stage2_passes_when_student_meets_threshold(self):
        report = ComparisonReport(
            teacher_avg_score=0.90,
            student_avg_score=0.80,
            student_hallucination_rate=0.05,
            prompt_count=10,
        )
        mock_runner = AsyncMock()
        mock_runner.run_comparison = AsyncMock(return_value=report)

        config = ValidationConfig(
            quality_threshold=0.85,
            max_hallucination_rate=0.10,
        )
        gate = ValidationGate(config=config, comparison_runner=mock_runner)

        result = asyncio.run(
            gate._run_stage_2_comparison(
                model_path="student",
                test_prompts=[{"prompt": "test"}],
            )
        )

        # 0.80 / 0.90 = 0.889 >= 0.85 threshold
        assert result.status == StageStatus.PASSED

    def test_stage2_fails_quality_below_threshold(self):
        report = ComparisonReport(
            teacher_avg_score=0.90,
            student_avg_score=0.50,
            student_hallucination_rate=0.05,
            prompt_count=10,
        )
        mock_runner = AsyncMock()
        mock_runner.run_comparison = AsyncMock(return_value=report)

        config = ValidationConfig(quality_threshold=0.85)
        gate = ValidationGate(config=config, comparison_runner=mock_runner)

        result = asyncio.run(
            gate._run_stage_2_comparison(
                model_path="student",
                test_prompts=[{"prompt": "test"}],
            )
        )

        # 0.50 / 0.90 = 0.556 < 0.85
        assert result.status == StageStatus.FAILED

    def test_stage2_fails_hallucination_rate(self):
        report = ComparisonReport(
            teacher_avg_score=0.90,
            student_avg_score=0.85,
            student_hallucination_rate=0.25,
            prompt_count=10,
        )
        mock_runner = AsyncMock()
        mock_runner.run_comparison = AsyncMock(return_value=report)

        config = ValidationConfig(max_hallucination_rate=0.10)
        gate = ValidationGate(config=config, comparison_runner=mock_runner)

        result = asyncio.run(
            gate._run_stage_2_comparison(
                model_path="student",
                test_prompts=[{"prompt": "test"}],
            )
        )

        assert result.status == StageStatus.FAILED
        assert result.details["hallucination_ok"] is False


# ═══════════════════════════════════════════════════════════════
# STAGE 3 RED TEAM TESTS
# ═══════════════════════════════════════════════════════════════


class TestStage3RedTeam:
    """Tests for Stage 3 — Red Team Security."""

    def test_stage3_passes_all_tests(self):
        gate = ValidationGate()

        with patch.object(gate, "_run_promptfoo_eval") as mock_eval:
            mock_eval.return_value = {"pass_count": 12, "total_count": 12}
            result = asyncio.run(
                gate._run_stage_3_redteam(model_path="test")
            )

        assert result.status == StageStatus.PASSED
        assert result.score == 1.0

    def test_stage3_fails_any_test(self):
        """Even a single red team failure should fail the stage."""
        gate = ValidationGate()

        with patch.object(gate, "_run_promptfoo_eval") as mock_eval:
            mock_eval.return_value = {
                "pass_count": 11,
                "total_count": 12,
                "failed_plugins": ["prompt-injection"],
            }
            result = asyncio.run(
                gate._run_stage_3_redteam(model_path="test")
            )

        assert result.status == StageStatus.FAILED
        assert "prompt-injection" in result.details["failed_plugins"]


# ═══════════════════════════════════════════════════════════════
# STAGE 4 REGRESSION TESTS
# ═══════════════════════════════════════════════════════════════


class TestStage4Regression:
    """Tests for Stage 4 — Regression Check."""

    def test_stage4_passes_no_regression(self):
        report = ComparisonReport(
            teacher_avg_score=0.80,  # previous student
            student_avg_score=0.82,  # new student (slightly better)
            teacher_avg_latency_ms=200,
            student_avg_latency_ms=210,
            prompt_count=10,
        )
        mock_runner = AsyncMock()
        mock_runner.run_comparison = AsyncMock(return_value=report)

        config = ValidationConfig(
            regression_tolerance=0.05,
            max_latency_increase=0.20,
        )
        gate = ValidationGate(config=config, comparison_runner=mock_runner)

        result = asyncio.run(
            gate._run_stage_4_regression(
                model_path="new-student",
                previous_model_path="old-student",
                test_prompts=[{"prompt": "test"}],
            )
        )

        assert result.status == StageStatus.PASSED

    def test_stage4_fails_quality_regression(self):
        report = ComparisonReport(
            teacher_avg_score=0.80,
            student_avg_score=0.60,  # 25% regression
            teacher_avg_latency_ms=200,
            student_avg_latency_ms=200,
            prompt_count=10,
        )
        mock_runner = AsyncMock()
        mock_runner.run_comparison = AsyncMock(return_value=report)

        config = ValidationConfig(regression_tolerance=0.05)
        gate = ValidationGate(config=config, comparison_runner=mock_runner)

        result = asyncio.run(
            gate._run_stage_4_regression(
                model_path="new-student",
                previous_model_path="old-student",
                test_prompts=[{"prompt": "test"}],
            )
        )

        assert result.status == StageStatus.FAILED
        assert result.details["regression_ok"] is False

    def test_stage4_fails_latency_regression(self):
        report = ComparisonReport(
            teacher_avg_score=0.80,
            student_avg_score=0.80,
            teacher_avg_latency_ms=200,
            student_avg_latency_ms=300,  # 50% increase
            prompt_count=10,
        )
        mock_runner = AsyncMock()
        mock_runner.run_comparison = AsyncMock(return_value=report)

        config = ValidationConfig(max_latency_increase=0.20)
        gate = ValidationGate(config=config, comparison_runner=mock_runner)

        result = asyncio.run(
            gate._run_stage_4_regression(
                model_path="new-student",
                previous_model_path="old-student",
                test_prompts=[{"prompt": "test"}],
            )
        )

        assert result.status == StageStatus.FAILED
        assert result.details["latency_ok"] is False


# ═══════════════════════════════════════════════════════════════
# FULL PIPELINE TESTS
# ═══════════════════════════════════════════════════════════════


class TestFullPipeline:
    """Tests for the full validation pipeline."""

    def test_full_pipeline_all_skip(self):
        """All stages skipped should deploy."""
        config = ValidationConfig(log_path="/dev/null")
        gate = ValidationGate(config=config)

        result = asyncio.run(
            gate.run_full_validation(
                model_path="test-model",
                model_name="test",
                skip_stages=[1, 2, 3, 4],
            )
        )

        assert result.decision == Decision.DEPLOY
        assert len(result.stages) == 4
        assert all(s.status == StageStatus.SKIPPED for s in result.stages)

    def test_full_pipeline_records_duration(self):
        config = ValidationConfig(log_path="/dev/null")
        gate = ValidationGate(config=config)

        result = asyncio.run(
            gate.run_full_validation(
                model_path="test",
                skip_stages=[1, 2, 3, 4],
            )
        )

        assert result.total_duration_ms > 0

    def test_full_pipeline_stage3_fail_blocks(self):
        """Full pipeline with Stage 3 failure should BLOCK."""
        config = ValidationConfig(log_path="/dev/null")
        gate = ValidationGate(config=config)

        with patch.object(gate, "_run_promptfoo_eval") as mock_eval:
            # Stage 1 passes, Stage 3 fails
            def side_effect(config_path, model_path):
                if "redteam" in config_path:
                    return {"pass_count": 10, "total_count": 12, "failed_plugins": ["injection"]}
                return {"pass_count": 8, "total_count": 10}

            mock_eval.side_effect = side_effect

            result = asyncio.run(
                gate.run_full_validation(
                    model_path="test",
                    skip_stages=[2, 4],
                )
            )

        assert result.decision == Decision.BLOCK


# ═══════════════════════════════════════════════════════════════
# COMPARISON RUNNER TESTS
# ═══════════════════════════════════════════════════════════════


class TestComparisonRunner:
    """Tests for the ComparisonRunner."""

    def test_empty_prompts(self):
        runner = ComparisonRunner(log_path="/dev/null")
        report = asyncio.run(
            runner.run_comparison("teacher", "student", [])
        )
        assert report.prompt_count == 0
        assert report.teacher_avg_score == 0.0
        assert report.student_avg_score == 0.0

    def test_no_providers_returns_errors(self):
        runner = ComparisonRunner(log_path="/dev/null")
        report = asyncio.run(
            runner.run_comparison(
                "teacher", "student",
                [{"prompt": "test prompt"}],
            )
        )
        assert report.prompt_count == 1
        # With no providers, results have errors
        assert len(report.comparisons) == 1
        assert report.comparisons[0].teacher.error != ""

    def test_with_mock_providers(self):
        async def teacher_fn(prompt: str) -> str:
            return "teacher response"

        async def student_fn(prompt: str) -> str:
            return "student response"

        runner = ComparisonRunner(
            teacher_provider=teacher_fn,
            student_provider=student_fn,
            log_path="/dev/null",
        )
        report = asyncio.run(
            runner.run_comparison(
                "teacher", "student",
                [{"prompt": "test"}],
            )
        )
        assert report.prompt_count == 1
        assert len(report.comparisons) == 1
        assert report.comparisons[0].teacher.response == "teacher response"
        assert report.comparisons[0].student.response == "student response"

    def test_with_judge_provider(self):
        async def teacher_fn(prompt: str) -> str:
            return "good response"

        async def student_fn(prompt: str) -> str:
            return "decent response"

        async def judge_fn(prompt: str) -> str:
            if "good response" in prompt:
                return "0.9"
            return "0.7"

        runner = ComparisonRunner(
            teacher_provider=teacher_fn,
            student_provider=student_fn,
            judge_provider=judge_fn,
            log_path="/dev/null",
        )
        report = asyncio.run(
            runner.run_comparison(
                "teacher", "student",
                [{"prompt": "evaluate this"}],
            )
        )
        assert report.teacher_avg_score > 0.0
        assert report.student_avg_score > 0.0

    def test_hallucination_detection_patterns(self):
        runner = ComparisonRunner(log_path="/dev/null")

        # Test pattern detection
        result = asyncio.run(
            runner._check_hallucination(
                "What is 2+2?",
                "As an AI language model, I can tell you that 2+2=4"
            )
        )
        assert result is True  # Contains hallucination marker

    def test_no_hallucination_for_clean_response(self):
        runner = ComparisonRunner(log_path="/dev/null")
        result = asyncio.run(
            runner._check_hallucination("What is 2+2?", "The answer is 4.")
        )
        assert result is False

    def test_grade_without_judge_returns_default(self):
        runner = ComparisonRunner(log_path="/dev/null")
        score = asyncio.run(
            runner._grade_output("prompt", "response")
        )
        assert score == 0.5

    def test_provider_error_captured(self):
        async def failing_provider(prompt: str) -> str:
            raise RuntimeError("Connection refused")

        runner = ComparisonRunner(
            teacher_provider=failing_provider,
            log_path="/dev/null",
        )
        result = asyncio.run(
            runner._run_model(failing_provider, "test")
        )
        assert "Connection refused" in result.error
        assert result.latency_ms > 0


# ═══════════════════════════════════════════════════════════════
# LOGGING TESTS
# ═══════════════════════════════════════════════════════════════


class TestLogging:
    """Tests for JSONL log output."""

    def test_validation_result_logged(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            config = ValidationConfig(log_path=log_path)
            gate = ValidationGate(config=config)

            result = asyncio.run(
                gate.run_full_validation(
                    model_path="test",
                    skip_stages=[1, 2, 3, 4],
                )
            )

            import json
            with open(log_path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["decision"] == "deploy"
        finally:
            os.unlink(log_path)

    def test_comparison_report_logged(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            async def noop_provider(prompt: str) -> str:
                return "ok"

            runner = ComparisonRunner(
                teacher_provider=noop_provider,
                student_provider=noop_provider,
                log_path=log_path,
            )
            report = asyncio.run(
                runner.run_comparison("teacher", "student", [{"prompt": "test"}])
            )

            import json
            with open(log_path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["teacher_model"] == "teacher"
        finally:
            os.unlink(log_path)
