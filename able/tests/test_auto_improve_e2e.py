"""
E2E tests for the AutoImprover pipeline (B5).

Tests the full cycle: eval failure → classifier → action proposal →
validation → approval workflow → SKILL.md patch generation.
Also tests behavioral audit probe execution and per-model guidance.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from able.core.evolution.auto_improve import (
    AutoImprover,
    ImprovementAction,
    ImprovementReport,
    _classify_failures,
    _generate_routing_improvements,
    _generate_enricher_improvements,
    _generate_skill_improvements,
    _build_skill_patch,
    _resolve_skill_target,
    _swarm_analyze_failures,
)


# ── Fixture: realistic parsed eval data ────────────────────────

def _make_eval_data(
    description: str = "eval-coding-regression",
    providers: dict = None,
    routing_mismatches: list = None,
) -> dict:
    """Build a parsed eval result dict matching collect_results.py output."""
    if providers is None:
        providers = {}
    return {
        "description": description,
        "by_provider": providers,
        "routing_mismatches": routing_mismatches or [],
    }


def _make_output(test: str, passed: bool, reason: str = "", output: str = "") -> dict:
    return {"test": test, "pass": passed, "reason": reason, "output": output}


def _eval_with_thinking_bleed() -> dict:
    return _make_eval_data(
        description="eval-security-regression",
        providers={
            "T1-GPT5.4Mini": {
                "outputs": [
                    _make_output("sql_inject", False, "thinking tokens leaked in response"),
                    _make_output("xss_filter", False, "reasoning token in output"),
                ]
            }
        },
    )


def _eval_with_under_routing() -> dict:
    return _make_eval_data(
        description="eval-coding-regression",
        routing_mismatches=[
            {"test": "complex_refactor", "issue": "under-routed to T1, T4 passed"},
            {"test": "security_audit", "issue": "under-routed, T2 needed"},
            {"test": "threat_model", "issue": "under-routed to T1"},
        ],
    )


def _eval_with_format_violations() -> dict:
    return _make_eval_data(
        description="eval-coding-format",
        providers={
            "T2-GPT5.4": {
                "outputs": [
                    _make_output("code_format", False, "wrong format structure"),
                    _make_output("code_heading", False, "missing heading format"),
                ]
            }
        },
    )


def _eval_with_skill_quality() -> dict:
    return _make_eval_data(
        description="eval-copywriting-quality",
        providers={
            "T2-GPT5.4": {
                "outputs": [
                    _make_output("landing_page", False, "low quality depth"),
                    _make_output("email_copy", False, "insufficient detail"),
                ]
            }
        },
    )


def _eval_with_compression_failure() -> dict:
    return _make_eval_data(
        description="eval-compression-check",
        providers={
            "T1-GPT5.4Mini": {
                "outputs": [
                    _make_output("compressed_q", False, "unclear abbreviation, ambiguous output"),
                ]
            }
        },
    )


# ── _classify_failures ─────────────────────────────────────────

def test_classify_thinking_bleed():
    failures = _classify_failures([_eval_with_thinking_bleed()])
    assert "thinking_bleed" in failures
    assert len(failures["thinking_bleed"]) == 2


def test_classify_under_routing():
    failures = _classify_failures([_eval_with_under_routing()])
    assert "under_routing" in failures
    assert len(failures["under_routing"]) == 3


def test_classify_format_violations():
    failures = _classify_failures([_eval_with_format_violations()])
    assert "format_violation" in failures
    assert len(failures["format_violation"]) == 2


def test_classify_compression_failure():
    failures = _classify_failures([_eval_with_compression_failure()])
    assert "compression_failure" in failures
    assert len(failures["compression_failure"]) == 1


def test_classify_empty_input():
    failures = _classify_failures([])
    assert failures == {}


def test_classify_all_passing():
    ev = _make_eval_data(providers={
        "T1": {"outputs": [_make_output("test1", True)]}
    })
    failures = _classify_failures([ev])
    assert failures == {}


def test_classify_mixed_multi_eval():
    failures = _classify_failures([
        _eval_with_thinking_bleed(),
        _eval_with_under_routing(),
        _eval_with_format_violations(),
    ])
    assert "thinking_bleed" in failures
    assert "under_routing" in failures
    assert "format_violation" in failures


# ── _generate_routing_improvements ─────────────────────────────

def test_routing_improvements_under_threshold():
    """Under 3 under-routes → no action (noise threshold)."""
    actions = _generate_routing_improvements(
        [{"eval": "test", "test": "security_audit", "issue": "under"}],
        [],
    )
    assert len(actions) == 0


def test_routing_improvements_above_threshold():
    under = [
        {"eval": "test", "test": "security_audit_1", "issue": "under"},
        {"eval": "test", "test": "security_audit_2", "issue": "under"},
        {"eval": "test", "test": "threat_model", "issue": "under"},
    ]
    actions = _generate_routing_improvements(under, [])
    assert len(actions) >= 1
    assert any(a.category == "routing" for a in actions)


def test_routing_improvements_over_routing():
    over = [
        {"eval": "test", "test": "t1", "issue": "wasted spend"},
        {"eval": "test", "test": "t2", "issue": "wasted Opus"},
    ]
    actions = _generate_routing_improvements([], over)
    assert len(actions) == 1
    assert actions[0].id == "route-over-alert"


# ── _generate_enricher_improvements ────────────────────────────

def test_enricher_improvements_format_domain():
    fvs = [
        {"eval": "coding-format", "test": "t1", "reason": "format", "output_preview": ""},
        {"eval": "coding-style", "test": "t2", "reason": "structure", "output_preview": ""},
    ]
    actions = _generate_enricher_improvements([], fvs)
    assert any("coding" in a.description for a in actions)


def test_enricher_improvements_quality_gap():
    gaps = [{"eval": "e"} for _ in range(4)]
    actions = _generate_enricher_improvements(gaps, [])
    assert len(actions) == 1
    assert actions[0].id == "enricher-quality-gap"


# ── _generate_skill_improvements ───────────────────────────────

def test_skill_improvements_t2_failing():
    gaps = [
        {"eval": "copywriting-quality", "test": "t1", "provider": "T2-GPT5.4",
         "reason": "quality", "output_preview": ""},
        {"eval": "copywriting-quality", "test": "t2", "provider": "T2-GPT5.4",
         "reason": "depth", "output_preview": ""},
    ]
    actions = _generate_skill_improvements(gaps, [])
    assert any(a.category == "skill" for a in actions)


def test_skill_improvements_t1_ceiling():
    gaps = [
        {"eval": "security-deep", "test": f"t{i}", "provider": "T1-GPT5.4Mini",
         "reason": "depth", "output_preview": ""}
        for i in range(4)
    ]
    actions = _generate_skill_improvements(gaps, [])
    assert any("T1 ceiling" in a.description for a in actions)


# ── _resolve_skill_target ──────────────────────────────────────

def test_resolve_skill_copywriting():
    name, path = _resolve_skill_target("eval-landing-page-copy")
    assert name == "copywriting"
    assert "copywriting" in path


def test_resolve_skill_security():
    name, path = _resolve_skill_target("eval-security-audit")
    assert name == "security"


def test_resolve_skill_fallback():
    name, path = _resolve_skill_target("eval-unknown-domain")
    assert name == "self_improvement"


# ── _build_skill_patch ─────────────────────────────────────────

def test_build_skill_patch_format():
    action = ImprovementAction(
        id="test-1", category="skill",
        target_file="SKILL.md", description="Fix quality",
        proposed_change="Add depth criteria",
        confidence=0.8, source_eval="eval-copy",
        failure_pattern="3 quality failures",
    )
    patch = _build_skill_patch(action)
    assert "### Latest Eval Reinforcement" in patch
    assert "eval-copy" in patch
    assert "3 quality failures" in patch


# ── _swarm_analyze_failures ────────────────────────────────────

@pytest.mark.asyncio
async def test_swarm_analysis_no_llm():
    failures = _classify_failures([
        _eval_with_thinking_bleed(),
        _eval_with_under_routing(),
    ])
    insights = await _swarm_analyze_failures(failures)
    assert any("[ANALYST]" in i for i in insights)
    assert any("[REVIEWER]" in i for i in insights)
    assert any("[PLANNER]" in i for i in insights)


@pytest.mark.asyncio
async def test_swarm_analysis_with_llm():
    mock_llm = AsyncMock(return_value="Root cause: strip_thinking not in eval harness")
    # Need 5+ failures to trigger LLM
    failures = _classify_failures([
        _eval_with_thinking_bleed(),
        _eval_with_under_routing(),
        _eval_with_format_violations(),
    ])
    insights = await _swarm_analyze_failures(failures, mock_llm)
    assert any("[LLM_ANALYST]" in i for i in insights)


@pytest.mark.asyncio
async def test_swarm_analysis_llm_failure_graceful():
    mock_llm = AsyncMock(side_effect=Exception("LLM down"))
    failures = _classify_failures([
        _eval_with_thinking_bleed(),
        _eval_with_under_routing(),
        _eval_with_format_violations(),
    ])
    # Should not raise
    insights = await _swarm_analyze_failures(failures, mock_llm)
    assert len(insights) > 0
    assert not any("[LLM_ANALYST]" in i for i in insights)


@pytest.mark.asyncio
async def test_swarm_critic_enricher_plus_quality():
    """CRITIC role fires when both enricher_gap and content_quality fail."""
    ev = _make_eval_data(
        description="eval-enricher-test",
        providers={
            "T1": {
                "outputs": [
                    _make_output("t1", False, "enricher didn't help quality"),
                    _make_output("t2", False, "quality depth insufficient"),
                    _make_output("t3", False, "quality lacking detail"),
                    _make_output("t4", False, "enricher gap found"),
                ]
            }
        },
    )
    failures = _classify_failures([ev])
    insights = await _swarm_analyze_failures(failures)
    if "enricher_gap" in failures and "content_quality" in failures:
        assert any("[CRITIC]" in i for i in insights)


# ── AutoImprover.run() — full E2E ─────────────────────────────

@pytest.mark.asyncio
async def test_full_cycle_no_failures():
    improver = AutoImprover()
    report = await improver.run([
        _make_eval_data(providers={
            "T1": {"outputs": [_make_output("passing_test", True)]}
        })
    ])
    assert report.evals_analyzed == 1
    assert report.failures_analyzed == 0
    assert report.actions_proposed == 0
    assert "healthy" in report.insights[0].lower()


@pytest.mark.asyncio
async def test_full_cycle_with_failures():
    improver = AutoImprover()
    report = await improver.run([
        _eval_with_thinking_bleed(),
        _eval_with_under_routing(),
        _eval_with_format_violations(),
        _eval_with_skill_quality(),
    ])
    assert report.evals_analyzed == 4
    assert report.failures_analyzed > 0
    assert report.actions_proposed > 0
    assert report.actions_validated > 0
    assert len(report.insights) > 0
    assert report.duration_ms > 0


@pytest.mark.asyncio
async def test_full_cycle_validation_filters_low_confidence():
    improver = AutoImprover()
    report = await improver.run([_eval_with_under_routing()])
    # Under-routing with 3+ cases generates actions with confidence >= 0.5
    for action in report.actions:
        if action.validated:
            assert action.confidence >= 0.5


@pytest.mark.asyncio
async def test_full_cycle_no_auto_apply_by_default():
    improver = AutoImprover()
    report = await improver.run([
        _eval_with_thinking_bleed(),
        _eval_with_under_routing(),
    ])
    assert report.actions_applied == 0  # auto_apply=False by default


@pytest.mark.asyncio
async def test_full_cycle_logs_report(tmp_path):
    improver = AutoImprover(log_dir=str(tmp_path / "auto_improve"))
    report = await improver.run([_eval_with_thinking_bleed()])
    # Check log file was created
    log_files = list((tmp_path / "auto_improve").glob("*.json"))
    assert len(log_files) >= 1


@pytest.mark.asyncio
async def test_full_cycle_thinking_bleed_special_action():
    """Thinking bleed >= 2 generates eval-harness-strip action."""
    improver = AutoImprover()
    report = await improver.run([_eval_with_thinking_bleed()])
    action_ids = [a.id for a in report.actions]
    assert "eval-harness-strip" in action_ids


@pytest.mark.asyncio
async def test_full_cycle_compression_category():
    """Compression failures are classified but don't yet generate actions."""
    improver = AutoImprover()
    report = await improver.run([_eval_with_compression_failure()])
    # compression_failure is classified, even if no auto-action generator yet
    assert report.failures_analyzed >= 1
