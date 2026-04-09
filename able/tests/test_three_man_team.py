"""Tests for the ThreeManTeamProtocol structured handoff system."""

import asyncio
import pytest

from able.core.swarm.swarm import (
    AgentRole,
    SwarmCoordinator,
    ThreeManTeamProtocol,
)


# ── Import / export ──────────────────────────────────────────────


def test_importable_from_init():
    from able.core.swarm import ThreeManTeamProtocol as TMT
    assert TMT is not None


# ── Protocol structure ───────────────────────────────────────────


def test_steps_order():
    """Three steps in the correct order: planner → coder → reviewer."""
    steps = ThreeManTeamProtocol.STEPS
    assert len(steps) == 3
    assert steps[0][0] == "planner"
    assert steps[1][0] == "coder"
    assert steps[2][0] == "reviewer"


def test_step_roles():
    """Each step maps to the correct AgentRole."""
    steps = ThreeManTeamProtocol.STEPS
    assert steps[0][1] == AgentRole.PLANNER
    assert steps[1][1] == AgentRole.CODER
    assert steps[2][1] == AgentRole.REVIEWER


def test_step_artifacts():
    """Each step produces the correct artifact file."""
    steps = ThreeManTeamProtocol.STEPS
    assert steps[0][2] == "PLAN-BRIEF.md"
    assert steps[1][2] == "BUILD-LOG.md"
    assert steps[2][2] == "REVIEW-FEEDBACK.md"


# ── Token optimization: _STEP_READS ─────────────────────────────


def test_planner_reads_nothing():
    """Planner only gets goal + context, not previous artifacts."""
    reads = ThreeManTeamProtocol._STEP_READS
    assert reads["planner"] == []


def test_coder_reads_only_plan():
    """Coder reads ONLY PLAN-BRIEF.md — no goal bloat."""
    reads = ThreeManTeamProtocol._STEP_READS
    assert reads["coder"] == ["PLAN-BRIEF.md"]


def test_reviewer_reads_plan_and_build():
    """Reviewer reads both PLAN-BRIEF.md and BUILD-LOG.md."""
    reads = ThreeManTeamProtocol._STEP_READS
    assert reads["reviewer"] == ["PLAN-BRIEF.md", "BUILD-LOG.md"]


# ── Step prompts ─────────────────────────────────────────────────


def test_all_steps_have_prompts():
    """Every step in STEPS has a corresponding prompt."""
    prompts = ThreeManTeamProtocol._STEP_PROMPTS
    for step_name, _, _ in ThreeManTeamProtocol.STEPS:
        assert step_name in prompts, f"Missing prompt for step: {step_name}"
        assert len(prompts[step_name]) > 50, f"Prompt too short for step: {step_name}"


def test_planner_prompt_forbids_implementation():
    prompt = ThreeManTeamProtocol._STEP_PROMPTS["planner"]
    assert "NOT implement" in prompt or "Do NOT" in prompt


def test_coder_prompt_enforces_scope():
    prompt = ThreeManTeamProtocol._STEP_PROMPTS["coder"]
    assert "scope" in prompt.lower()


def test_reviewer_prompt_expects_verdict():
    prompt = ThreeManTeamProtocol._STEP_PROMPTS["reviewer"]
    assert "PASS" in prompt and "FAIL" in prompt


# ── Full execution (mock) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_full_chain():
    """Run the full 3-step chain with a mock LLM and verify artifacts."""
    coordinator = SwarmCoordinator(llm_provider=None, max_agents=10)
    protocol = ThreeManTeamProtocol(coordinator)

    result = await protocol.execute(
        goal="Add a health check endpoint to the API",
        context={"framework": "FastAPI"},
    )

    # All 3 steps should have run
    assert len(result["steps"]) == 3
    assert "planner" in result["steps"]
    assert "coder" in result["steps"]
    assert "reviewer" in result["steps"]

    # All 3 artifacts should exist
    assert "PLAN-BRIEF.md" in result["artifacts"]
    assert "BUILD-LOG.md" in result["artifacts"]
    assert "REVIEW-FEEDBACK.md" in result["artifacts"]

    # Each artifact should have content
    for name, content in result["artifacts"].items():
        assert len(content) > 0, f"Empty artifact: {name}"


@pytest.mark.asyncio
async def test_execute_produces_verdict():
    """The result should include a verdict string."""
    coordinator = SwarmCoordinator(llm_provider=None, max_agents=10)
    protocol = ThreeManTeamProtocol(coordinator)

    result = await protocol.execute(goal="Test task")

    assert "verdict" in result
    assert result["verdict"] in ("PASS", "REVISE", "FAIL", "INCOMPLETE")


@pytest.mark.asyncio
async def test_scope_lock_halts_on_failure():
    """If a step fails, subsequent steps should not run."""
    coordinator = SwarmCoordinator(llm_provider=None, max_agents=0)  # Will raise on spawn
    protocol = ThreeManTeamProtocol(coordinator)

    result = await protocol.execute(goal="This will fail at planner")

    # Planner should have failed
    assert result["steps"]["planner"]["success"] is False
    # Coder and reviewer should not have run
    assert "coder" not in result["steps"]
    assert "reviewer" not in result["steps"]
    assert result["verdict"] == "INCOMPLETE"
