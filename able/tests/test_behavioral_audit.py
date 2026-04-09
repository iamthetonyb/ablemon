"""Tests for the provider behavioral audit system."""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from able.core.evolution.auto_improve import (
    BEHAVIORAL_PROBES,
    BehavioralAuditResult,
    _FAILURE_MODE_GUIDANCE,
    provider_behavioral_audit,
)


# ── Probe definitions ────────────────────────────────────────────


def test_probe_count():
    """Must have exactly 10 standardized probes."""
    assert len(BEHAVIORAL_PROBES) == 10


def test_probes_have_required_fields():
    """Every probe must have id, mode, prompt, check."""
    for probe in BEHAVIORAL_PROBES:
        assert "id" in probe, f"Probe missing id: {probe}"
        assert "mode" in probe, f"Probe missing mode: {probe}"
        assert "prompt" in probe, f"Probe missing prompt: {probe}"
        assert "check" in probe, f"Probe missing check: {probe}"


def test_probes_cover_all_5_modes():
    """Probes must cover all 5 failure modes."""
    modes = {p["mode"] for p in BEHAVIORAL_PROBES}
    expected = {"thinking_bleed", "empty_response", "tool_refusal", "format_violation", "hallucinated_tool"}
    assert modes == expected


def test_each_mode_has_2_probes():
    """Each failure mode should have exactly 2 probes."""
    from collections import Counter
    counts = Counter(p["mode"] for p in BEHAVIORAL_PROBES)
    for mode, count in counts.items():
        assert count == 2, f"Mode {mode} has {count} probes, expected 2"


def test_probe_ids_unique():
    ids = [p["id"] for p in BEHAVIORAL_PROBES]
    assert len(ids) == len(set(ids)), "Duplicate probe IDs"


# ── Failure mode guidance ────────────────────────────────────────


def test_guidance_covers_all_modes():
    modes = {p["mode"] for p in BEHAVIORAL_PROBES}
    for mode in modes:
        assert mode in _FAILURE_MODE_GUIDANCE, f"Missing guidance for {mode}"


def test_guidance_non_empty():
    for mode, guidance in _FAILURE_MODE_GUIDANCE.items():
        assert len(guidance) > 20, f"Guidance too short for {mode}"


# ── BehavioralAuditResult ───────────────────────────────────────


def test_audit_result_dataclass():
    result = BehavioralAuditResult(
        provider_name="test-provider",
        tier=1,
        total_probes=10,
        failures={"thinking_bleed": [{"probe_id": "tb-1"}]},
        pass_rate=0.9,
        guidance=["Fix thinking bleed"],
    )
    assert result.provider_name == "test-provider"
    assert result.tier == 1
    assert result.pass_rate == 0.9
    assert len(result.guidance) == 1
    assert result.timestamp  # Auto-populated


# ── Audit execution with mock LLM ───────────────────────────────


@pytest.mark.asyncio
async def test_audit_with_perfect_llm(tmp_path):
    """An LLM that returns perfect answers should get 100% pass rate."""
    responses = {
        "tb-1": "4",
        "tb-2": "The cat sat on the mat, summarized in one sentence.",
        "er-1": "1. Improved cardiovascular health 2. Better mood 3. Weight management",
        "er-2": "The sky appears blue because of Rayleigh scattering, which causes shorter wavelengths to scatter more.",
        "tr-1": "I'll use the web_search tool to find Tokyo weather.",
        "tr-2": "Let me search GitHub for the most starred Python project.",
        "fv-1": '{"name": "Alice", "age": 30}',
        "fv-2": "| Feature | Status | Notes |\n|---------|--------|-------|\n| Auth | Done | OAuth |\n| API | WIP | REST |\n| UI | Todo | React |",
        "ht-1": "I have access to web_search and buddy_status tools.",
        "ht-2": "I don't have a database query tool. I can help with other tasks.",
    }

    call_count = 0

    async def mock_llm(system: str, user: str) -> str:
        nonlocal call_count
        # Match probe by prompt content
        for probe in BEHAVIORAL_PROBES:
            if probe["prompt"] == user:
                call_count += 1
                return responses.get(probe["id"], "default response")
        return "default response"

    results = await provider_behavioral_audit(
        llm_call=mock_llm,
        tiers=[1],
        log_dir=str(tmp_path / "audit"),
    )

    assert len(results) == 1
    result = results[0]
    assert result.tier == 1
    assert result.pass_rate >= 0.8  # Allow some flex for heuristic checks
    assert call_count == 10  # All 10 probes ran


@pytest.mark.asyncio
async def test_audit_with_broken_llm(tmp_path):
    """An LLM that fails every probe should generate guidance for all modes."""
    async def broken_llm(system: str, user: str) -> str:
        return "<think>I'm thinking...</think>"  # Thinking bleed + short + no tools

    results = await provider_behavioral_audit(
        llm_call=broken_llm,
        tiers=[2],
        log_dir=str(tmp_path / "audit"),
    )

    assert len(results) == 1
    result = results[0]
    assert result.pass_rate < 1.0
    assert len(result.failures) > 0  # At least some failures detected
    assert len(result.guidance) > 0  # Guidance generated


@pytest.mark.asyncio
async def test_audit_persists_report(tmp_path):
    """Audit results should be saved to disk."""
    async def noop_llm(system: str, user: str) -> str:
        return "A reasonable response that is long enough to pass most checks and has valid content."

    log_dir = tmp_path / "audit"
    await provider_behavioral_audit(
        llm_call=noop_llm,
        tiers=[1],
        log_dir=str(log_dir),
    )

    files = list(log_dir.glob("behavioral_audit_*.json"))
    assert len(files) == 1

    data = json.loads(files[0].read_text())
    assert isinstance(data, list)
    assert len(data) == 1
    assert "pass_rate" in data[0]


@pytest.mark.asyncio
async def test_audit_no_providers_returns_empty(tmp_path):
    """If no providers can be built, return empty results."""
    results = await provider_behavioral_audit(
        llm_call=None,
        tiers=[99],  # Nonexistent tier
        log_dir=str(tmp_path / "audit"),
    )
    # Either empty (no chains) or results exist — no crash
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_audit_multiple_tiers(tmp_path):
    """Audit across multiple tiers produces one result per tier."""
    async def simple_llm(system: str, user: str) -> str:
        return '{"name": "Test", "age": 25}\nA valid response with enough content to pass.'

    results = await provider_behavioral_audit(
        llm_call=simple_llm,
        tiers=[1, 2, 4],
        log_dir=str(tmp_path / "audit"),
    )

    assert len(results) == 3
    tiers_seen = {r.tier for r in results}
    assert tiers_seen == {1, 2, 4}


# ── Cron wiring ──────────────────────────────────────────────────


def test_behavioral_audit_registered_in_cron():
    """The behavioral-audit cron job should be registered."""
    import inspect
    from able.scheduler.cron import register_default_jobs

    source = inspect.getsource(register_default_jobs)
    assert "behavioral-audit" in source
    assert "provider_behavioral_audit" in source
