"""Tests for E7 — DSPy + GEPA Evolutionary Skill Optimization.

Covers: config, evolver instantiation, guardrails, lowest-scoring identification,
graceful degradation.
"""

import pytest
from pathlib import Path

from able.core.evolution.skill_evolver import (
    EvolutionConfig,
    EvolvedSkill,
    SkillEvolver,
    _DSPY_AVAILABLE,
    _check_size,
    _check_semantic_preservation,
)


# ── Config ────────────────────────────────────────────────────


class TestEvolutionConfig:

    def test_defaults(self):
        cfg = EvolutionConfig()
        assert cfg.max_iterations > 0
        assert cfg.population_size > 0
        assert cfg.max_skill_bytes == 15_360
        assert cfg.require_pr_review is True

    def test_custom(self):
        cfg = EvolutionConfig(
            max_iterations=5,
            target_metric="accuracy",
        )
        assert cfg.max_iterations == 5
        assert cfg.target_metric == "accuracy"


# ── EvolvedSkill ──────────────────────────────────────────────


class TestEvolvedSkill:

    def test_success(self):
        es = EvolvedSkill(
            original_path="skills/test/SKILL.md",
            evolved_content="improved content",
            score_improvement=0.15,
            changes_summary="better prompts",
            optimizer_used="GEPA",
            guardrail_passed=True,
        )
        assert es.guardrail_passed
        assert es.score_improvement == 0.15

    def test_failure(self):
        es = EvolvedSkill(
            original_path="skills/test/SKILL.md",
            evolved_content="",
            score_improvement=0.0,
            changes_summary="",
            optimizer_used="none",
            guardrail_passed=False,
            failure_reason="Size exceeded 15KB",
        )
        assert not es.guardrail_passed
        assert "Size" in es.failure_reason


# ── Guardrails (module-level functions) ───────────────────────


class TestGuardrails:

    def test_size_check_pass(self):
        passed, msg = _check_size("Good content", 15_360)
        assert passed

    def test_size_check_fail(self):
        big = "x" * 20_000
        passed, msg = _check_size(big, 15_360)
        assert not passed

    def test_semantic_preservation_pass(self):
        original = "# Skill\n## Triggers\n- research\n- investigate\n- look up\n"
        evolved = "# Skill\n## Triggers\n- research\n- investigate\n- deep dive\n"
        passed, msg = _check_semantic_preservation(original, evolved)
        assert passed

    def test_semantic_preservation_fail(self):
        original = "# Skill\n## Triggers\n- research\n- investigate\n- look up\n"
        evolved = "# Skill\n## Triggers\n- deploy\n- ship\n- push\n"
        passed, msg = _check_semantic_preservation(original, evolved)
        assert not passed


# ── SkillEvolver ──────────────────────────────────────────────


class TestSkillEvolver:

    def test_instantiation(self):
        evolver = SkillEvolver()
        assert evolver is not None

    def test_dspy_available(self):
        assert _DSPY_AVAILABLE is True

    def test_has_evolve_skill(self):
        evolver = SkillEvolver()
        assert hasattr(evolver, "evolve_skill")

    def test_has_identify_lowest(self):
        evolver = SkillEvolver()
        assert hasattr(evolver, "identify_lowest_scoring_skills")


# ── Lowest-scoring identification ─────────────────────────────


class TestIdentifyLowest:

    def test_identify_lowest_scoring(self, tmp_path):
        # Create real skill files so Path.exists() passes
        (tmp_path / "a.md").write_text("skill a")
        (tmp_path / "b.md").write_text("skill b")
        (tmp_path / "c.md").write_text("skill c")

        evolver = SkillEvolver()
        eval_data = [
            {"skill_path": str(tmp_path / "a.md"), "composite_score": 0.9, "trace_count": 10},
            {"skill_path": str(tmp_path / "b.md"), "composite_score": 0.3, "trace_count": 8},
            {"skill_path": str(tmp_path / "c.md"), "composite_score": 0.5, "trace_count": 15},
        ]
        lowest = evolver.identify_lowest_scoring_skills(
            eval_data, top_n=2, min_traces=5,
        )
        assert len(lowest) == 2
        # Lowest composite_score should be first
        assert lowest[0]["composite_score"] <= lowest[1]["composite_score"]

    def test_empty_eval_data(self):
        evolver = SkillEvolver()
        lowest = evolver.identify_lowest_scoring_skills([], top_n=3)
        assert lowest == []
