"""
ATLAS Auto-Pilot — Autonomous task runner and distillation data generator.

Picks up tasks from ~/.atlas/memory/current_objectives.yaml, decomposes via
the GoalPlanner, and executes autonomously. Every execution generates
distillation training data. Also includes auto-prompting: generates prompts
from the prompt bank, runs through teacher AND student models, and collects
comparison pairs.

Safety:
    - max_iterations limit per run
    - budget cap (token-based)
    - no destructive operations without explicit allow_destructive flag
    - all outputs tagged source="autopilot" for distillation harvesting
"""

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.core.agi.planner import GoalPlanner, PlannerResult, SubTask

logger = logging.getLogger(__name__)

OBJECTIVES_PATH = Path(
    os.environ.get(
        "ATLAS_OBJECTIVES_PATH",
        os.path.expanduser("~/.atlas/memory/current_objectives.yaml"),
    )
)

# Safety defaults
DEFAULT_MAX_TASKS = 5
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_BUDGET_TOKENS = 500_000
DESTRUCTIVE_TOOLS = frozenset({
    "shell.rm", "shell.delete", "git.force_push", "git.reset_hard",
    "digitalocean.destroy", "vercel.delete",
})


@dataclass
class AutoPilotResult:
    """Result from a single autopilot run."""

    run_id: str
    tasks_attempted: int = 0
    tasks_succeeded: int = 0
    tasks_failed: int = 0
    distillation_pairs: int = 0
    total_tokens: int = 0
    total_time_s: float = 0.0
    errors: List[str] = field(default_factory=list)
    source: str = "autopilot"


@dataclass
class ComparisonPair:
    """A teacher vs student comparison for distillation."""

    prompt: str
    domain: str
    teacher_response: str
    teacher_model: str
    student_response: str
    student_model: str
    source: str = "autopilot"


def _load_objectives(path: Path) -> Dict[str, List[str]]:
    """Load objectives YAML, returning dict with urgency buckets.

    Expected format:
        urgent: [...]
        in_progress: [...]
        backlog: [...]
        blocked: [...]
    """
    empty = {"urgent": [], "in_progress": [], "backlog": [], "blocked": []}
    if not path.exists():
        logger.warning(f"Objectives file not found: {path}")
        return empty

    try:
        import yaml
    except ImportError:
        return _parse_simple_yaml(path)

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    return {
        "urgent": data.get("urgent") or [],
        "in_progress": data.get("in_progress") or [],
        "backlog": data.get("backlog") or [],
        "blocked": data.get("blocked") or [],
    }


def _parse_simple_yaml(path: Path) -> Dict[str, List[str]]:
    """Minimal YAML parser for the objectives file (no dependency needed)."""
    result: Dict[str, List[str]] = {
        "urgent": [], "in_progress": [], "backlog": [], "blocked": [],
    }
    current_key: Optional[str] = None
    with open(path, "r") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.endswith(":") or (": " in stripped and stripped.endswith("[]")):
                key = stripped.split(":")[0].strip()
                if key in result:
                    current_key = key
                continue
            if stripped.startswith("- ") and current_key:
                result[current_key].append(stripped[2:].strip().strip('"').strip("'"))
    return result


def _is_destructive(subtask: SubTask) -> bool:
    """Check if a subtask uses a destructive tool."""
    return subtask.tool in DESTRUCTIVE_TOOLS


class AutoPilot:
    """Autonomous task runner with distillation data generation.

    Picks up objectives, decomposes them via GoalPlanner, executes
    autonomously, and collects training pairs from every execution.

    Usage:
        pilot = AutoPilot()
        result = await pilot.run_objectives(max_tasks=5)
        result = await pilot.run_auto_prompting(domain="coding", count=10)
        result = await pilot.run_self_eval()
    """

    def __init__(
        self,
        planner: Optional[GoalPlanner] = None,
        objectives_path: Optional[Path] = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
        allow_destructive: bool = False,
        distillation_dir: Optional[str] = None,
    ):
        self.planner = planner or GoalPlanner()
        self.objectives_path = objectives_path or OBJECTIVES_PATH
        self.max_iterations = max_iterations
        self.budget_tokens = budget_tokens
        self.allow_destructive = allow_destructive
        self.distillation_dir = Path(distillation_dir or "data")
        self.distillation_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────

    async def run_objectives(self, max_tasks: int = DEFAULT_MAX_TASKS) -> AutoPilotResult:
        """Pick up tasks from objectives, decompose, execute autonomously.

        Processes urgent tasks first, then in_progress, then backlog.
        Skips blocked tasks. Respects max_tasks and budget_tokens limits.
        """
        run_id = str(uuid.uuid4())[:8]
        start = time.time()
        result = AutoPilotResult(run_id=run_id)

        logger.info(f"AutoPilot [{run_id}] starting — max_tasks={max_tasks}, budget={self.budget_tokens} tokens")

        objectives = _load_objectives(self.objectives_path)
        task_queue: List[str] = []
        for bucket in ("urgent", "in_progress", "backlog"):
            task_queue.extend(objectives.get(bucket, []))

        if not task_queue:
            logger.info(f"AutoPilot [{run_id}] no tasks found")
            result.total_time_s = time.time() - start
            return result

        tasks_to_run = task_queue[:max_tasks]
        logger.info(f"AutoPilot [{run_id}] processing {len(tasks_to_run)} task(s)")

        iterations = 0
        for description in tasks_to_run:
            if result.total_tokens >= self.budget_tokens:
                logger.warning(f"AutoPilot [{run_id}] budget exhausted ({result.total_tokens} tokens)")
                break
            if iterations >= self.max_iterations:
                logger.warning(f"AutoPilot [{run_id}] iteration limit reached ({iterations})")
                break

            result.tasks_attempted += 1
            iterations += 1

            try:
                plan_result = await self._execute_objective(description, run_id)
                result.total_tokens += plan_result.tokens_used

                if plan_result.success:
                    result.tasks_succeeded += 1
                    result.distillation_pairs += self._save_distillation_pair(
                        description, plan_result, run_id,
                    )
                else:
                    result.tasks_failed += 1
                    if plan_result.error:
                        result.errors.append(f"{description}: {plan_result.error}")

            except Exception as exc:
                result.tasks_failed += 1
                result.errors.append(f"{description}: {exc}")
                logger.error(f"AutoPilot [{run_id}] task failed: {description} — {exc}")

        result.total_time_s = time.time() - start
        logger.info(
            f"AutoPilot [{run_id}] done in {result.total_time_s:.1f}s — "
            f"{result.tasks_succeeded}/{result.tasks_attempted} OK, "
            f"{result.distillation_pairs} pairs saved"
        )
        return result

    async def run_auto_prompting(
        self,
        domain: str = "coding",
        count: int = 10,
        teacher_model: str = "gpt-5.4",
        student_model: str = "qwen3.5-27b-ud",
    ) -> AutoPilotResult:
        """Generate prompts, run through teacher and student, collect comparison pairs."""
        from atlas.core.distillation.prompt_bank import PromptBank

        run_id = str(uuid.uuid4())[:8]
        start = time.time()
        result = AutoPilotResult(run_id=run_id)

        logger.info(
            f"AutoPrompting [{run_id}] domain={domain}, count={count}, "
            f"teacher={teacher_model}, student={student_model}"
        )

        bank = PromptBank()
        prompts = bank.sample(domain=domain, n=count)

        if not prompts:
            logger.warning(f"AutoPrompting [{run_id}] no prompts for domain={domain}")
            result.total_time_s = time.time() - start
            return result

        pairs: List[ComparisonPair] = []
        for entry in prompts:
            if result.total_tokens >= self.budget_tokens:
                logger.warning(f"AutoPrompting [{run_id}] budget exhausted")
                break

            result.tasks_attempted += 1

            try:
                teacher_resp = await self._call_model(teacher_model, entry.prompt)
                student_resp = await self._call_model(student_model, entry.prompt)

                pairs.append(ComparisonPair(
                    prompt=entry.prompt,
                    domain=entry.domain,
                    teacher_response=teacher_resp.get("content", ""),
                    teacher_model=teacher_model,
                    student_response=student_resp.get("content", ""),
                    student_model=student_model,
                ))
                result.tasks_succeeded += 1
                result.total_tokens += teacher_resp.get("tokens", 0) + student_resp.get("tokens", 0)

            except Exception as exc:
                result.tasks_failed += 1
                result.errors.append(f"prompt '{entry.prompt[:50]}...': {exc}")
                logger.error(f"AutoPrompting [{run_id}] failed on prompt: {exc}")

        result.distillation_pairs = self._save_comparison_pairs(pairs, run_id)
        result.total_time_s = time.time() - start

        logger.info(
            f"AutoPrompting [{run_id}] done — {result.tasks_succeeded}/{result.tasks_attempted} pairs, "
            f"{result.distillation_pairs} saved"
        )
        return result

    async def run_self_eval(self) -> AutoPilotResult:
        """Run evals, identify weaknesses, generate targeted training data.

        Loads eval results, classifies failures, and creates targeted
        prompts from the failure patterns to strengthen the student model.
        """
        from atlas.core.distillation.prompt_bank import PromptBank

        run_id = str(uuid.uuid4())[:8]
        start = time.time()
        result = AutoPilotResult(run_id=run_id)

        logger.info(f"SelfEval [{run_id}] starting")

        failures = self._collect_eval_failures()
        if not failures:
            logger.info(f"SelfEval [{run_id}] no failures found")
            result.total_time_s = time.time() - start
            return result

        result.tasks_attempted = len(failures)

        bank = PromptBank()
        added = bank.add_from_failures(failures)
        result.distillation_pairs = added
        result.tasks_succeeded = added
        result.tasks_failed = len(failures) - added

        result.total_time_s = time.time() - start
        logger.info(
            f"SelfEval [{run_id}] done — {added} targeted prompts from "
            f"{len(failures)} failures in {result.total_time_s:.1f}s"
        )
        return result

    # ── Internal methods ──────────────────────────────────────────

    async def _execute_objective(self, description: str, run_id: str) -> PlannerResult:
        """Execute a single objective through the planner with safety wrapping."""
        original_executor = self.planner.executor

        async def safe_executor(subtask: SubTask) -> Any:
            if _is_destructive(subtask) and not self.allow_destructive:
                raise PermissionError(
                    f"AutoPilot blocked destructive tool: {subtask.tool} "
                    f"(set allow_destructive=True to override)"
                )
            if original_executor:
                return await original_executor(subtask)
            return {"status": "executed", "tool": subtask.tool, "tokens_used": 100}

        self.planner.executor = safe_executor
        try:
            return await self.planner.execute_goal(
                description=description,
                client_id="autopilot",
                context={"source": "autopilot", "run_id": run_id},
            )
        finally:
            self.planner.executor = original_executor

    def _save_distillation_pair(
        self, description: str, plan_result: PlannerResult, run_id: str,
    ) -> int:
        """Save a distillation pair from a completed objective. Returns 0 or 1."""
        if not plan_result.output:
            return 0

        pair = {
            "id": str(uuid.uuid4()),
            "prompt": description,
            "gold_response": plan_result.output,
            "gold_model": "autopilot-planner",
            "domain": self._classify_domain(description),
            "quality_score": 1.0 if plan_result.success else 0.5,
            "source": "autopilot",
            "run_id": run_id,
            "tokens_used": plan_result.tokens_used,
            "created_at": time.time(),
        }

        output_path = self.distillation_dir / f"distillation_autopilot_{run_id}.jsonl"
        with open(output_path, "a") as f:
            f.write(json.dumps(pair, default=str) + "\n")

        return 1

    def _save_comparison_pairs(self, pairs: List[ComparisonPair], run_id: str) -> int:
        """Save teacher vs student comparison pairs to JSONL."""
        if not pairs:
            return 0

        output_path = self.distillation_dir / f"distillation_autoprompt_{run_id}.jsonl"
        with open(output_path, "a") as f:
            for pair in pairs:
                record = {
                    "id": str(uuid.uuid4()),
                    "prompt": pair.prompt,
                    "domain": pair.domain,
                    "teacher_response": pair.teacher_response,
                    "teacher_model": pair.teacher_model,
                    "student_response": pair.student_response,
                    "student_model": pair.student_model,
                    "source": "autopilot",
                    "run_id": run_id,
                    "created_at": time.time(),
                }
                f.write(json.dumps(record, default=str) + "\n")

        return len(pairs)

    def _collect_eval_failures(self) -> List[Dict]:
        """Collect failure patterns from recent evolution cycle files."""
        failures: List[Dict] = []
        cycles_dir = self.distillation_dir / "evolution_cycles"
        if not cycles_dir.exists():
            return failures

        cycle_files = sorted(cycles_dir.glob("*.json"), reverse=True)[:5]
        for path in cycle_files:
            try:
                with open(path) as f:
                    data = json.load(f)
                for problem in data.get("problems", []):
                    description = problem.get("description", "")
                    if description:
                        failures.append({
                            "category": problem.get("category", "unknown"),
                            "domain": problem.get("domain", "coding"),
                            "description": description,
                            "difficulty": "medium",
                            "tags": ["from_eval", "autopilot"],
                        })
            except (json.JSONDecodeError, KeyError):
                continue

        return failures

    async def _call_model(self, model: str, prompt: str) -> Dict[str, Any]:
        """Call a model and return its response.

        Stub implementation. In production, route through ProviderRegistry.
        """
        return {
            "content": f"[{model} response to: {prompt[:80]}]",
            "tokens": 100,
            "model": model,
        }

    @staticmethod
    def _classify_domain(description: str) -> str:
        """Classify a task description into a domain."""
        desc = description.lower()
        for domain in ("security", "code", "research", "write", "deploy", "test"):
            if domain in desc:
                return domain
        return "general"
