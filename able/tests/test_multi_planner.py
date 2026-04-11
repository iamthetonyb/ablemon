"""Tests for D5 — Multi-Planner Parallelism.

Covers: parallel plan generation, plan parsing, plan selection,
persona configuration, error handling, timeouts, result structure.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock

from able.core.swarm.multi_planner import (
    MultiPlanner,
    MultiPlanResult,
    PlanProposal,
    PLANNER_PERSONAS,
)


@pytest.fixture
def planner():
    return MultiPlanner()


@pytest.fixture
def planner_with_llm():
    """Planner with a mock LLM that returns structured plans."""
    async def _mock_llm(system, user, temperature):
        return (
            "1. Analyze the current codebase structure\n"
            "2. Identify dependencies and interfaces\n"
            "3. Create new module with improved design\n"
            "4. Write unit tests for the new module\n"
            "5. Migrate callers to new interfaces\n"
            "6. Remove old code\n"
            "Risk: Data migration could fail if schemas diverge"
        )

    return MultiPlanner(llm_fn=_mock_llm)


# ── Personas ──────────────────────────────────────────────────────

class TestPersonas:

    def test_default_personas_exist(self):
        assert "conservative" in PLANNER_PERSONAS
        assert "aggressive" in PLANNER_PERSONAS
        assert "balanced" in PLANNER_PERSONAS

    def test_personas_have_required_fields(self):
        for name, config in PLANNER_PERSONAS.items():
            assert "system_prompt" in config
            assert "temperature" in config
            assert len(config["system_prompt"]) > 20

    def test_conservative_lower_temperature(self):
        assert PLANNER_PERSONAS["conservative"]["temperature"] < PLANNER_PERSONAS["aggressive"]["temperature"]


# ── Plan parsing ──────────────────────────────────────────────────

class TestPlanParsing:

    def test_parse_numbered_steps(self):
        raw = "1. First step\n2. Second step\n3. Third step"
        steps, reasoning, risks = MultiPlanner._parse_plan(raw)
        assert len(steps) == 3
        assert "First step" in steps[0]

    def test_parse_with_risks(self):
        raw = "1. Do thing\nRisk: This could break"
        steps, reasoning, risks = MultiPlanner._parse_plan(raw)
        assert len(steps) == 1
        assert len(risks) == 1
        assert "break" in risks[0]

    def test_parse_bullet_steps(self):
        raw = "- Step one\n- Step two\n- Step three"
        steps, _, _ = MultiPlanner._parse_plan(raw)
        assert len(steps) == 3

    def test_parse_mixed_format(self):
        raw = "Overview of the plan.\n1. First thing\n2. Second thing\nRisk: potential issue"
        steps, reasoning, risks = MultiPlanner._parse_plan(raw)
        assert len(steps) == 2
        assert len(risks) == 1
        assert "Overview" in reasoning

    def test_parse_empty(self):
        steps, reasoning, risks = MultiPlanner._parse_plan("")
        assert steps == []
        assert reasoning == ""
        assert risks == []


# ── Plan selection ────────────────────────────────────────────────

class TestPlanSelection:

    def test_single_plan_selected(self):
        plan = PlanProposal(
            planner_id="test",
            strategy="test",
            steps=["step 1", "step 2"],
            reasoning="good plan",
            estimated_complexity=0.5,
        )
        selected = MultiPlanner._select_best([plan], "task")
        assert selected is plan

    def test_more_steps_preferred(self):
        short = PlanProposal(
            planner_id="short",
            strategy="short",
            steps=["step 1"],
            reasoning="brief",
            estimated_complexity=0.1,
        )
        detailed = PlanProposal(
            planner_id="detailed",
            strategy="detailed",
            steps=[f"step {i}" for i in range(7)],
            reasoning="thorough plan with good reasoning",
            estimated_complexity=0.7,
            risks=["Something could go wrong"],
        )
        selected = MultiPlanner._select_best([short, detailed], "task")
        assert selected.planner_id == "detailed"

    def test_excessive_steps_penalized(self):
        moderate = PlanProposal(
            planner_id="moderate",
            strategy="balanced",
            steps=[f"step {i}" for i in range(8)],
            reasoning="solid plan",
            estimated_complexity=0.8,
            risks=["minor risk"],
        )
        excessive = PlanProposal(
            planner_id="excessive",
            strategy="overengineered",
            steps=[f"step {i}" for i in range(20)],
            reasoning="way too many steps",
            estimated_complexity=1.0,
        )
        selected = MultiPlanner._select_best([moderate, excessive], "task")
        assert selected.planner_id == "moderate"

    def test_empty_list_returns_none(self):
        assert MultiPlanner._select_best([], "task") is None


# ── Multi-planner execution ──────────────────────────────────────

class TestMultiPlannerExecution:

    @pytest.mark.asyncio
    async def test_stub_plans_generated(self, planner):
        """Without LLM, stub plans are generated."""
        result = await planner.plan(task="refactor auth module")
        assert isinstance(result, MultiPlanResult)
        assert len(result.all_plans) == 3
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_with_llm_plans_parsed(self, planner_with_llm):
        result = await planner_with_llm.plan(task="refactor auth module")
        assert result.planners_succeeded == 3
        assert result.selected_plan is not None
        assert len(result.selected_plan.steps) == 6
        assert len(result.selected_plan.risks) >= 1

    @pytest.mark.asyncio
    async def test_with_context(self, planner_with_llm):
        result = await planner_with_llm.plan(
            task="add caching",
            context={"files": ["cache.py", "config.py"], "constraint": "no redis"},
        )
        assert result.selected_plan is not None

    @pytest.mark.asyncio
    async def test_timeout_handled(self):
        async def _slow_llm(system, user, temperature):
            await asyncio.sleep(10)
            return "1. Never reached"

        mp = MultiPlanner(llm_fn=_slow_llm, timeout_per_planner=0.1)
        result = await mp.plan(task="test timeout")
        assert result.planners_failed == 3
        assert all(p.error and "Timed out" in p.error for p in result.all_plans)

    @pytest.mark.asyncio
    async def test_error_handled(self):
        async def _failing_llm(system, user, temperature):
            raise RuntimeError("LLM exploded")

        mp = MultiPlanner(llm_fn=_failing_llm)
        result = await mp.plan(task="test error")
        assert result.planners_failed == 3
        assert result.selected_plan is None

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self):
        _call_count = 0

        async def _flaky_llm(system, user, temperature):
            nonlocal _call_count
            _call_count += 1
            if _call_count == 2:
                raise RuntimeError("flaky failure")
            return "1. Step one\n2. Step two"

        mp = MultiPlanner(llm_fn=_flaky_llm)
        result = await mp.plan(task="test mixed")
        assert result.planners_succeeded == 2
        assert result.planners_failed == 1
        assert result.selected_plan is not None

    @pytest.mark.asyncio
    async def test_custom_personas(self):
        custom = {
            "alpha": {"system_prompt": "Plan A", "temperature": 0.3},
            "beta": {"system_prompt": "Plan B", "temperature": 0.9},
        }
        mp = MultiPlanner(personas=custom, max_planners=2)
        result = await mp.plan(task="test custom")
        assert len(result.all_plans) == 2
        strategies = {p.strategy for p in result.all_plans}
        assert "alpha" in strategies
        assert "beta" in strategies


# ── Result structure ──────────────────────────────────────────────

class TestResultStructure:

    def test_summary(self):
        result = MultiPlanResult(
            task="test",
            selected_plan=PlanProposal(
                planner_id="balanced",
                strategy="balanced",
                steps=["a", "b"],
                reasoning="",
                estimated_complexity=0.5,
            ),
            all_plans=[],
            planners_succeeded=3,
            planners_failed=0,
            duration_ms=42.0,
        )
        s = result.summary()
        assert "balanced" in s
        assert "3/0" in s

    def test_plan_proposal_with_error(self):
        p = PlanProposal(
            planner_id="fail",
            strategy="fail",
            steps=[],
            reasoning="",
            estimated_complexity=0,
            error="something broke",
        )
        assert p.error == "something broke"
