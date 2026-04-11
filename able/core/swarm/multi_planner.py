"""
D5 — Multi-Planner Parallelism.

For complex tasks (complexity >= 0.7), spawns N planner agents with
different system prompts. Each generates an independent plan, then a
coordinator merges/selects the best.

Forked from math-ai-org/mathcode parallel diverse strategy pattern.

Usage:
    mp = MultiPlanner(llm_provider=provider)
    result = await mp.plan(
        task="Refactor the auth module to use OAuth2",
        context={"files": ["auth.py", "middleware.py"]},
    )
    print(result.selected_plan)
    print(result.all_plans)

Design:
- N=3 planners by default (conservative, aggressive, balanced)
- Each planner has a distinct system prompt biasing its approach
- Plans are collected via asyncio.gather()
- Coordinator scores plans on: completeness, risk, effort, correctness
- Returns best plan + all alternatives
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PlanProposal:
    """A single plan from one planner agent."""
    planner_id: str
    strategy: str  # "conservative", "aggressive", "balanced", etc.
    steps: List[str]
    reasoning: str
    estimated_complexity: float  # 0.0–1.0
    risks: List[str] = field(default_factory=list)
    duration_ms: float = 0
    error: Optional[str] = None


@dataclass
class MultiPlanResult:
    """Result from multi-planner execution."""
    task: str
    selected_plan: Optional[PlanProposal]
    all_plans: List[PlanProposal]
    selection_reason: str = ""
    duration_ms: float = 0
    planners_succeeded: int = 0
    planners_failed: int = 0

    def summary(self) -> str:
        selected_id = self.selected_plan.planner_id if self.selected_plan else "none"
        return (
            f"MultiPlanner: {self.planners_succeeded}/{len(self.all_plans)} planners succeeded, "
            f"selected={selected_id}, {self.duration_ms:.0f}ms"
        )


# ── Planner personas ─────────────────────────────────────────────

PLANNER_PERSONAS = {
    "conservative": {
        "system_prompt": (
            "You are a cautious, methodical planner. You prefer:"
            "\n- Small, incremental changes over big rewrites"
            "\n- Thorough testing at each step"
            "\n- Backwards compatibility"
            "\n- Minimizing blast radius"
            "\n- Rolling back easily if something goes wrong"
            "\nPlan the task step-by-step, erring on the side of safety."
        ),
        "temperature": 0.4,
    },
    "aggressive": {
        "system_prompt": (
            "You are a bold, efficiency-focused planner. You prefer:"
            "\n- Clean-slate approaches when legacy code is messy"
            "\n- Parallel execution of independent tasks"
            "\n- Removing dead code and tech debt along the way"
            "\n- Optimizing for speed and simplicity"
            "\n- Getting it done in fewer steps even if riskier"
            "\nPlan the task ambitiously, optimizing for speed."
        ),
        "temperature": 0.8,
    },
    "balanced": {
        "system_prompt": (
            "You are a pragmatic planner who balances speed and safety. You:"
            "\n- Consider multiple approaches before committing"
            "\n- Take measured risks where the payoff justifies it"
            "\n- Test critical paths, skip testing for trivial changes"
            "\n- Reuse existing code when possible, rewrite when necessary"
            "\n- Think about what can go wrong at each step"
            "\nPlan the task with good engineering judgment."
        ),
        "temperature": 0.6,
    },
}


class MultiPlanner:
    """Spawn multiple planner agents with diverse strategies.

    Each planner gets the same task but a different system prompt,
    biasing toward different approaches. Plans are collected in
    parallel and the best is selected based on scoring criteria.
    """

    def __init__(
        self,
        llm_fn: Optional[Callable[..., Coroutine]] = None,
        personas: Optional[Dict[str, Dict]] = None,
        max_planners: int = 3,
        timeout_per_planner: float = 60.0,
    ):
        """
        Args:
            llm_fn: Async callable(system_prompt, user_message, temperature) -> str.
                     If None, returns stub plans.
            personas: Dict of planner personas (name → {system_prompt, temperature}).
            max_planners: Maximum concurrent planners.
            timeout_per_planner: Timeout per planner in seconds.
        """
        self._llm_fn = llm_fn
        self._personas = personas or PLANNER_PERSONAS
        self._max_planners = max_planners
        self._timeout = timeout_per_planner

    async def plan(
        self,
        task: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> MultiPlanResult:
        """Generate multiple plans for a task in parallel.

        Args:
            task: Natural language task description.
            context: Additional context (files, constraints, etc.).

        Returns:
            MultiPlanResult with selected plan and all alternatives.
        """
        start = time.perf_counter()

        # Build context string
        ctx_str = ""
        if context:
            parts = []
            for k, v in context.items():
                if isinstance(v, list):
                    parts.append(f"{k}: {', '.join(str(x) for x in v)}")
                else:
                    parts.append(f"{k}: {v}")
            ctx_str = "\n".join(parts)

        user_message = f"Task: {task}"
        if ctx_str:
            user_message += f"\n\nContext:\n{ctx_str}"
        user_message += (
            "\n\nProvide your plan as a numbered list of steps. "
            "For each step, briefly explain what it does and why. "
            "End with a one-line risk assessment."
        )

        # Spawn planners in parallel
        personas_to_use = list(self._personas.items())[:self._max_planners]

        async def _run_planner(name: str, config: Dict) -> PlanProposal:
            planner_start = time.perf_counter()
            try:
                if self._llm_fn:
                    raw = await asyncio.wait_for(
                        self._llm_fn(
                            config["system_prompt"],
                            user_message,
                            config.get("temperature", 0.6),
                        ),
                        timeout=self._timeout,
                    )
                else:
                    # Stub when no LLM available
                    raw = f"[{name}] Plan stub for: {task[:80]}"

                steps, reasoning, risks = self._parse_plan(raw)

                return PlanProposal(
                    planner_id=name,
                    strategy=name,
                    steps=steps,
                    reasoning=reasoning,
                    estimated_complexity=len(steps) / 20.0,  # Rough heuristic
                    risks=risks,
                    duration_ms=(time.perf_counter() - planner_start) * 1000,
                )
            except asyncio.TimeoutError:
                return PlanProposal(
                    planner_id=name,
                    strategy=name,
                    steps=[],
                    reasoning="",
                    estimated_complexity=0,
                    duration_ms=(time.perf_counter() - planner_start) * 1000,
                    error=f"Timed out after {self._timeout}s",
                )
            except Exception as e:
                return PlanProposal(
                    planner_id=name,
                    strategy=name,
                    steps=[],
                    reasoning="",
                    estimated_complexity=0,
                    duration_ms=(time.perf_counter() - planner_start) * 1000,
                    error=str(e),
                )

        # Run all planners concurrently
        tasks = [_run_planner(name, config) for name, config in personas_to_use]
        all_plans = await asyncio.gather(*tasks)

        succeeded = [p for p in all_plans if not p.error and p.steps]
        failed = [p for p in all_plans if p.error or not p.steps]

        # Select best plan
        selected = self._select_best(succeeded, task) if succeeded else None
        selection_reason = ""
        if selected:
            selection_reason = (
                f"Selected '{selected.planner_id}' strategy: "
                f"{len(selected.steps)} steps, "
                f"complexity={selected.estimated_complexity:.2f}"
            )

        return MultiPlanResult(
            task=task,
            selected_plan=selected,
            all_plans=list(all_plans),
            selection_reason=selection_reason,
            duration_ms=(time.perf_counter() - start) * 1000,
            planners_succeeded=len(succeeded),
            planners_failed=len(failed),
        )

    @staticmethod
    def _parse_plan(raw: str) -> tuple:
        """Parse raw LLM output into structured plan.

        Returns (steps, reasoning, risks).
        """
        lines = raw.strip().split("\n")
        steps = []
        reasoning_lines = []
        risks = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Detect numbered steps (1., 2., etc.)
            if stripped[0].isdigit() and (". " in stripped[:5] or ") " in stripped[:5]):
                # Remove number prefix
                step_text = stripped.split(". ", 1)[-1] if ". " in stripped[:5] else stripped.split(") ", 1)[-1]
                steps.append(step_text)
            elif stripped.lower().startswith("risk"):
                risks.append(stripped)
            elif stripped.startswith("- "):
                steps.append(stripped[2:])
            else:
                reasoning_lines.append(stripped)

        reasoning = " ".join(reasoning_lines[:5])  # Cap reasoning
        return steps, reasoning, risks

    @staticmethod
    def _select_best(plans: List[PlanProposal], task: str) -> Optional[PlanProposal]:
        """Score and select the best plan.

        Scoring criteria:
        - Completeness: more steps = more thorough (up to a point)
        - Conciseness: penalize excessive steps
        - Risk awareness: plans that mention risks score higher
        """
        if not plans:
            return None
        if len(plans) == 1:
            return plans[0]

        def _score(plan: PlanProposal) -> float:
            # Base: number of steps (3-10 is ideal)
            step_score = min(len(plan.steps), 10) / 10.0
            # Penalize very long plans
            if len(plan.steps) > 15:
                step_score *= 0.8
            # Bonus for risk awareness
            risk_bonus = 0.1 if plan.risks else 0.0
            # Bonus for reasoning
            reasoning_bonus = 0.1 if len(plan.reasoning) > 50 else 0.0
            return step_score + risk_bonus + reasoning_bonus

        scored = sorted(plans, key=_score, reverse=True)
        return scored[0]
