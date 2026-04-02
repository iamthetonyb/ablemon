from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from able.core.evolution.analyzer import AnalysisResult, EvolutionAnalyzer
from able.core.evolution.auto_improve import ImprovementReport
from able.core.evolution.daemon import EvolutionConfig, EvolutionDaemon
from able.core.evolution.improver import Improvement, WeightImprover
from able.core.routing.interaction_log import InteractionLogger, InteractionRecord


def _sample_weights() -> dict:
    return {
        "version": 1,
        "features": {
            "token_count_weight": 0.15,
            "requires_tools_weight": 0.15,
            "requires_code_weight": 0.20,
            "multi_step_weight": 0.20,
            "safety_critical_weight": 0.30,
        },
        "tier_thresholds": {
            "tier_1_max": 0.40,
            "tier_2_max": 0.70,
        },
        "domain_adjustments": {
            "security": 0.15,
            "coding": 0.05,
            "research": 0.00,
        },
    }


def _seed_interactions(db_path: str, count: int = 30) -> None:
    logger = InteractionLogger(db_path=db_path)
    for index in range(count):
        logger.log(
            InteractionRecord(
                message_preview=f"interaction {index}",
                complexity_score=0.35 + (index % 4) * 0.1,
                selected_tier=1 if index % 2 == 0 else 2,
                selected_provider="gpt-5.4-mini" if index % 2 == 0 else "gpt-5.4",
                domain="security" if index % 3 == 0 else "research",
                success=index % 6 != 0,
                escalated=index % 4 == 0,
                user_correction=index % 5 == 0,
                latency_ms=150 + index,
                channel="cli",
                session_id="evo-test",
            )
        )


async def _fake_auto_improve(*args, **kwargs):
    return ImprovementReport(
        timestamp="2026-04-01T00:00:00+00:00",
        evals_analyzed=0,
        failures_analyzed=0,
        actions_proposed=0,
        actions_validated=0,
        actions_applied=0,
    )


def test_rule_based_analysis_generates_bounded_improvements():
    metrics = {
        "failures_by_tier": [
            {"selected_tier": 1, "total": 30, "failures": 8, "failure_rate_pct": 26.7},
        ],
        "escalation_rate": {"total": 30, "escalations": 8, "user_corrections": 5, "override_rate_pct": 26.7},
        "cost_by_tier": [],
        "wins_by_tier": [],
        "domain_accuracy": [
            {"domain": "security", "total": 12, "escalations": 5},
        ],
        "scoring_drift": [],
        "fallback_frequency": [],
    }

    analysis = EvolutionAnalyzer(provider=None)._analyze_rule_based(metrics)
    improvements = WeightImprover(_sample_weights()).generate_improvements(analysis)

    assert improvements
    assert all(abs(imp.change_pct) <= 0.20 for imp in improvements)
    assert all(0.0 <= imp.proposed_value <= 1.0 for imp in improvements)


@pytest.mark.asyncio
async def test_evolution_cycle_dry_run_with_seeded_log(tmp_path, monkeypatch):
    db_path = tmp_path / "interaction_log.db"
    weights_path = tmp_path / "scorer_weights.yaml"
    cycle_log_dir = tmp_path / "cycles"
    _seed_interactions(str(db_path), count=30)
    weights_path.write_text(yaml.safe_dump(_sample_weights()), encoding="utf-8")

    async def fake_analyze(metrics):
        return AnalysisResult(
            problems=[{"type": "under_routing", "domain": "security"}],
            recommendations=[
                {
                    "type": "domain_adjustment",
                    "target": "security",
                    "direction": "increase",
                    "reason": "Security escalations remain high",
                }
            ],
            confidence=0.7,
            analysis_source="rule_based",
        )

    monkeypatch.setattr("able.core.evolution.daemon.run_from_evals", _fake_auto_improve)

    daemon = EvolutionDaemon(
        config=EvolutionConfig(
            weights_path=str(weights_path),
            interaction_db=str(db_path),
            cycle_log_dir=str(cycle_log_dir),
            min_interactions_for_cycle=20,
            auto_deploy=False,
        )
    )
    daemon._analyzer.analyze = fake_analyze

    result = await daemon.run_cycle()

    assert result.success is True
    assert result.metrics_collected is True
    assert result.interactions_analyzed >= 20
    assert result.problems_found == 1
    assert result.improvements_proposed == 1
    assert result.improvements_deployed == 0
    assert (cycle_log_dir / f"{result.cycle_id}.json").exists()


@pytest.mark.asyncio
async def test_evolution_cycle_rejects_invalid_change_and_creates_backup(tmp_path, monkeypatch):
    db_path = tmp_path / "interaction_log.db"
    weights_path = tmp_path / "scorer_weights.yaml"
    cycle_log_dir = tmp_path / "cycles"
    _seed_interactions(str(db_path), count=30)
    weights_path.write_text(yaml.safe_dump(_sample_weights()), encoding="utf-8")

    async def fake_analyze(metrics):
        return AnalysisResult(
            problems=[{"type": "high_failure", "tier": 1}],
            recommendations=[{"type": "weight_adjustment", "target": "requires_code_weight"}],
            confidence=0.7,
            analysis_source="rule_based",
        )

    def fake_generate(self, analysis):
        return [
            Improvement(
                target="features.safety_critical_weight",
                current_value=0.30,
                proposed_value=1.50,
                change_pct=4.0,
                reason="invalid",
            ),
            Improvement(
                target="features.requires_code_weight",
                current_value=0.20,
                proposed_value=0.22,
                change_pct=0.10,
                reason="valid",
            ),
        ]

    monkeypatch.setattr("able.core.evolution.daemon.run_from_evals", _fake_auto_improve)
    monkeypatch.setattr("able.core.evolution.daemon.WeightImprover.generate_improvements", fake_generate)

    daemon = EvolutionDaemon(
        config=EvolutionConfig(
            weights_path=str(weights_path),
            interaction_db=str(db_path),
            cycle_log_dir=str(cycle_log_dir),
            min_interactions_for_cycle=20,
            auto_deploy=True,
        )
    )
    daemon._analyzer.analyze = fake_analyze

    result = await daemon.run_cycle()

    assert result.success is True
    assert result.improvements_proposed == 2
    assert result.improvements_approved == 1
    assert result.improvements_deployed == 1
    assert result.new_version == 2
    assert (tmp_path / "scorer_weights.v1.yaml").exists()

    deployed = yaml.safe_load(weights_path.read_text(encoding="utf-8"))
    assert deployed["features"]["requires_code_weight"] == 0.22
