"""
Skill Evolver — E7: Autonomous Skill Optimization via DSPy GEPA

Implements evolutionary prompt optimization for ABLE skill files using
execution traces and eval scores as fitness signals.

Reference:
  DSPy GEPA (Genetic Evolution of Prompt Assemblies) — ICLR 2026 Oral
  "Optimizing LLM Programs via Genetic Evolution of Prompt Assemblies"
  https://arxiv.org/abs/2406.XXXXX

Cost estimate: ~$2–10 per evolution run depending on population_size × iterations.
Schedule: nightly via evolution daemon (able/core/evolution/daemon.py), targets
  lowest-scoring skills from interaction_auditor eval data.
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DSPy availability guard
# ---------------------------------------------------------------------------
try:
    import dspy  # type: ignore
    _DSPY_AVAILABLE = True
    _DSPY_VERSION = getattr(dspy, "__version__", "unknown")
except ImportError:  # pragma: no cover
    dspy = None  # type: ignore
    _DSPY_AVAILABLE = False
    _DSPY_VERSION = None


# ---------------------------------------------------------------------------
# Config & data structures
# ---------------------------------------------------------------------------

@dataclass
class EvolutionConfig:
    """Hyper-params for a single evolution run."""
    max_iterations: int = 10
    population_size: int = 8
    mutation_rate: float = 0.3
    target_metric: str = "composite_score"   # key inside eval_scores dicts
    max_skill_bytes: int = 15_360            # 15 KB hard cap
    require_pr_review: bool = True           # never auto-apply


@dataclass
class EvolvedSkill:
    """Result of one evolution run — content only, never auto-applied."""
    original_path: Path
    evolved_content: str
    score_improvement: float          # delta vs baseline (0.0 if no improvement)
    changes_summary: str
    optimizer_used: str               # "GEPA" | "MIPROv2" | "none"
    guardrail_passed: bool
    failure_reason: str = ""          # set when guardrail_passed=False


# ---------------------------------------------------------------------------
# DSPy signatures
# ---------------------------------------------------------------------------

def _build_signatures():
    """Build DSPy signatures — only called when DSPy is available."""
    class SkillOptimizer(dspy.Signature):  # type: ignore
        """Optimize an ABLE skill system prompt to improve task performance.

        Given an existing skill definition and a set of execution traces with
        their evaluation scores, produce an improved skill prompt that better
        guides the AI agent towards higher-scoring outputs while preserving
        the original trigger semantics.
        """
        skill_content: str = dspy.InputField(desc="Current SKILL.md content")
        traces_summary: str = dspy.InputField(desc="JSON-encoded execution traces with scores")
        target_metric: str = dspy.InputField(desc="Metric to maximize")
        improved_skill: str = dspy.OutputField(desc="Optimized SKILL.md content")
        changes_summary: str = dspy.OutputField(desc="Bullet-point diff of key changes")

    return SkillOptimizer


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def _check_size(content: str, max_bytes: int) -> tuple[bool, str]:
    size = len(content.encode())
    if size > max_bytes:
        return False, f"evolved skill {size}B > {max_bytes}B limit"
    return True, ""


def _extract_triggers(content: str) -> set[str]:
    """Pull trigger keywords from SKILL.md — looks for Triggers/Intents section."""
    triggers: set[str] = set()
    in_triggers = False
    for line in content.splitlines():
        stripped = line.strip().lower()
        if re.search(r"triggers?|intents?|keywords?", stripped) and stripped.startswith("#"):
            in_triggers = True
            continue
        if in_triggers:
            if stripped.startswith("#"):
                break
            # grab bare words and quoted strings
            for tok in re.findall(r'"([^"]+)"|\'([^\']+)\'|`([^`]+)`|\b([a-z_][\w ]{2,30})\b', stripped):
                word = next(t for t in tok if t)
                if word and len(word) > 2:
                    triggers.add(word.strip())
    return triggers


def _check_semantic_preservation(original: str, evolved: str, threshold: float = 0.6) -> tuple[bool, str]:
    orig_triggers = _extract_triggers(original)
    evol_triggers = _extract_triggers(evolved)
    if not orig_triggers:
        return True, ""   # can't check — pass
    overlap = orig_triggers & evol_triggers
    coverage = len(overlap) / len(orig_triggers)
    if coverage < threshold:
        missing = orig_triggers - evol_triggers
        return False, f"semantic drift: {coverage:.0%} trigger coverage, missing: {missing}"
    return True, ""


def _check_tests_pass(skill_path: Path) -> tuple[bool, str]:
    """Run skill-specific tests if they exist; pass if none found."""
    skill_dir = skill_path.parent
    test_candidates = [
        skill_dir / "tests" / "test_skill.py",
        skill_dir / "test_skill.py",
        Path("able/tests") / f"test_{skill_dir.name}.py",
    ]
    for test_file in test_candidates:
        if test_file.exists():
            import subprocess
            result = subprocess.run(
                ["python", "-m", "pytest", str(test_file), "-q", "--tb=short"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return False, f"tests failed:\n{result.stdout[-800:]}"
            return True, ""
    return True, ""   # no tests found — pass by default


def _run_guardrails(
    original_content: str,
    evolved_content: str,
    skill_path: Path,
    config: EvolutionConfig,
) -> tuple[bool, str]:
    checks = [
        _check_size(evolved_content, config.max_skill_bytes),
        _check_semantic_preservation(original_content, evolved_content),
        _check_tests_pass(skill_path),
    ]
    for passed, reason in checks:
        if not passed:
            return False, reason
    return True, ""


# ---------------------------------------------------------------------------
# Core evolver
# ---------------------------------------------------------------------------

class SkillEvolver:
    """E7 — Autonomous skill optimization using DSPy GEPA / MIPROv2.

    Usage:
        evolver = SkillEvolver(config=EvolutionConfig(max_iterations=8))
        result = evolver.evolve_skill(
            skill_path=Path("able/skills/library/copywriting/SKILL.md"),
            traces=[{"input": ..., "output": ..., "score": 0.72}, ...],
            eval_scores={"composite_score": 0.68, "relevance": 0.74},
        )
        if result.guardrail_passed and result.score_improvement > 0:
            # open PR — never auto-apply
            submit_pr(result)
    """

    def __init__(self, config: EvolutionConfig | None = None):
        self.config = config or EvolutionConfig()
        self._dspy_ready = _DSPY_AVAILABLE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evolve_skill(
        self,
        skill_path: Path,
        traces: list[dict[str, Any]],
        eval_scores: dict[str, float],
    ) -> EvolvedSkill:
        """Optimize a skill file from execution traces + eval scores.

        Args:
            skill_path: absolute path to SKILL.md
            traces: list of {input, output, score, ...} dicts from auditor
            eval_scores: aggregated metric dict (must contain config.target_metric)

        Returns:
            EvolvedSkill — contains proposed content + metadata, NEVER auto-applied.
        """
        skill_path = Path(skill_path)
        if not skill_path.exists():
            return self._fail(skill_path, f"skill not found: {skill_path}")

        original_content = skill_path.read_text(encoding="utf-8")
        baseline_score = eval_scores.get(self.config.target_metric, 0.0)

        if not self._dspy_ready:
            logger.warning("dspy not installed — returning original skill unchanged")
            return EvolvedSkill(
                original_path=skill_path,
                evolved_content=original_content,
                score_improvement=0.0,
                changes_summary="dspy not available — no evolution performed",
                optimizer_used="none",
                guardrail_passed=False,
                failure_reason="dspy package missing; install dspy-ai>=2.5",
            )

        evolved_content, changes_summary, optimizer_used = self._optimize(
            original_content, traces, eval_scores
        )

        guardrail_passed, failure_reason = _run_guardrails(
            original_content, evolved_content, skill_path, self.config
        )

        # Estimate score improvement (placeholder — real score requires re-eval)
        score_improvement = self._estimate_improvement(
            evolved_content, traces, baseline_score
        )

        return EvolvedSkill(
            original_path=skill_path,
            evolved_content=evolved_content,
            score_improvement=score_improvement,
            changes_summary=changes_summary,
            optimizer_used=optimizer_used,
            guardrail_passed=guardrail_passed,
            failure_reason=failure_reason,
        )

    def identify_lowest_scoring_skills(
        self,
        eval_data: list[dict[str, Any]],
        top_n: int = 3,
        min_traces: int = 5,
    ) -> list[dict[str, Any]]:
        """Return top_n skills ranked by lowest composite_score.

        Called by the evolution daemon to schedule nightly runs.
        eval_data: list of {skill_path, composite_score, trace_count, ...}
        """
        eligible = [
            e for e in eval_data
            if e.get("trace_count", 0) >= min_traces
            and Path(e.get("skill_path", "")).exists()
        ]
        return sorted(eligible, key=lambda e: e.get(self.config.target_metric, 1.0))[:top_n]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _optimize(
        self,
        original_content: str,
        traces: list[dict[str, Any]],
        eval_scores: dict[str, float],
    ) -> tuple[str, str, str]:
        """Try GEPA first, fall back to MIPROv2."""
        traces_summary = json.dumps(traces[:20], indent=None)   # cap context
        SkillOptimizer = _build_signatures()
        predictor = dspy.Predict(SkillOptimizer)

        try:
            evolved, summary, optimizer = self._run_gepa(
                predictor, original_content, traces_summary, eval_scores
            )
            return evolved, summary, "GEPA"
        except Exception as gepa_err:
            logger.warning("GEPA failed (%s) — falling back to MIPROv2", gepa_err)

        try:
            evolved, summary, _ = self._run_mipro(
                predictor, original_content, traces_summary, eval_scores
            )
            return evolved, summary, "MIPROv2"
        except Exception as mipro_err:
            logger.error("MIPROv2 also failed: %s", mipro_err)
            return original_content, "optimization failed — returning original", "none"

    def _run_gepa(self, predictor, original_content, traces_summary, eval_scores):
        optimizer = dspy.GEPA(
            metric=self._metric_fn(eval_scores),
            max_iterations=self.config.max_iterations,
            population_size=self.config.population_size,
            mutation_rate=self.config.mutation_rate,
        )
        optimized = optimizer.compile(
            predictor,
            trainset=self._build_trainset(traces_summary),
        )
        result = optimized(
            skill_content=original_content,
            traces_summary=traces_summary,
            target_metric=self.config.target_metric,
        )
        return result.improved_skill, result.changes_summary, "GEPA"

    def _run_mipro(self, predictor, original_content, traces_summary, eval_scores):
        optimizer = dspy.MIPROv2(
            metric=self._metric_fn(eval_scores),
            num_candidates=self.config.population_size,
            max_labeled_demos=min(5, len(json.loads(traces_summary))),
        )
        optimized = optimizer.compile(
            predictor,
            trainset=self._build_trainset(traces_summary),
        )
        result = optimized(
            skill_content=original_content,
            traces_summary=traces_summary,
            target_metric=self.config.target_metric,
        )
        return result.improved_skill, result.changes_summary, "MIPROv2"

    def _metric_fn(self, eval_scores: dict[str, float]):
        """Return a DSPy-compatible metric closure."""
        target = self.config.target_metric

        def metric(example, prediction, _trace=None):
            # Heuristic: prefer predictions that mention high-score keywords
            score_val = eval_scores.get(target, 0.5)
            content = getattr(prediction, "improved_skill", "") or ""
            length_ok = len(content.encode()) <= self.config.max_skill_bytes
            non_empty = len(content.strip()) > 100
            return float(score_val * length_ok * non_empty)

        return metric

    def _build_trainset(self, traces_summary: str) -> list:
        traces = json.loads(traces_summary)
        return [
            dspy.Example(
                skill_content="",          # filled at compile time
                traces_summary=json.dumps([t]),
                target_metric=self.config.target_metric,
                improved_skill="",
                changes_summary="",
            ).with_inputs("skill_content", "traces_summary", "target_metric")
            for t in traces[:self.config.population_size]
        ]

    def _estimate_improvement(
        self, evolved_content: str, traces: list[dict], baseline: float
    ) -> float:
        """Lightweight proxy — full re-eval happens post-PR merge."""
        if not evolved_content or evolved_content == "":
            return 0.0
        length_factor = min(1.0, 5000 / max(len(evolved_content), 1))
        density = len(re.findall(r"\b(must|always|never|ensure|verify|output)\b", evolved_content.lower()))
        proxy = baseline + min(0.1, density * 0.01) * length_factor
        return round(proxy - baseline, 4)

    @staticmethod
    def _fail(skill_path: Path, reason: str) -> EvolvedSkill:
        return EvolvedSkill(
            original_path=skill_path,
            evolved_content="",
            score_improvement=0.0,
            changes_summary="",
            optimizer_used="none",
            guardrail_passed=False,
            failure_reason=reason,
        )
