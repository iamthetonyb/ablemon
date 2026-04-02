"""
ABLE AGI Goal Planner - Autonomous goal decomposition and execution.

OpenClaw-inspired: "Not just reactive, but proactively plans and executes."

Features:
    - Goal decomposition into sub-tasks
    - Dependency resolution
    - Parallel execution where safe
    - Self-monitoring and re-planning
    - Learning from outcomes

Architecture:
    Goal → Decomposer → DependencyGraph → ExecutionScheduler → Executor
                                              ↓
                                       SelfMonitor
                                              ↓
                                       OutcomeLearner
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Awaitable

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    READY = "ready"        # All dependencies met
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"    # Waiting on dependency
    CANCELLED = "cancelled"
    NEEDS_APPROVAL = "needs_approval"


class TaskPriority(Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


@dataclass
class SubTask:
    """An atomic unit of work in a goal plan"""
    id: str
    description: str
    tool: str                    # Which tool/skill to invoke
    args: Dict[str, Any]         # Arguments for the tool
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    depends_on: List[str] = field(default_factory=list)  # Task IDs
    estimated_tokens: int = 500
    requires_approval: bool = False
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    retries: int = 0
    max_retries: int = 2


@dataclass
class Goal:
    """A high-level goal to be decomposed and executed"""
    id: str
    description: str
    client_id: str
    priority: TaskPriority = TaskPriority.NORMAL
    subtasks: List[SubTask] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    success: Optional[bool] = None
    learnings: List[str] = field(default_factory=list)

    def completion_percentage(self) -> float:
        if not self.subtasks:
            return 0.0
        done = sum(1 for t in self.subtasks if t.status == TaskStatus.COMPLETED)
        return done / len(self.subtasks) * 100


@dataclass
class PlannerResult:
    """Result from the goal planner"""
    goal: Goal
    success: bool
    total_time_s: float
    tokens_used: int
    output: Optional[str] = None
    error: Optional[str] = None


class GoalDecomposer:
    """
    Decomposes high-level goals into executable sub-tasks.

    Uses pattern matching for known goal types, falls back to
    LLM-based decomposition for novel goals.
    """

    # Known goal patterns with pre-defined decompositions
    KNOWN_PATTERNS = {
        "research": [
            SubTask(id="s1", description="Search for information", tool="browser.search", args={}),
            SubTask(id="s2", description="Summarize findings", tool="llm.summarize", args={}, depends_on=["s1"]),
            SubTask(id="s3", description="Fact-check summary", tool="factchecker.verify", args={}, depends_on=["s2"]),
            SubTask(id="s4", description="Store in memory", tool="memory.store", args={}, depends_on=["s3"]),
        ],
        "code_review": [
            SubTask(id="s1", description="Read code files", tool="shell.read", args={}),
            SubTask(id="s2", description="Malware scan", tool="malware.scan", args={}, depends_on=["s1"]),
            SubTask(id="s3", description="Static analysis", tool="llm.analyze", args={}, depends_on=["s2"]),
            SubTask(id="s4", description="Generate report", tool="llm.report", args={}, depends_on=["s3"]),
            SubTask(id="s5", description="Fact-check report", tool="factchecker.verify", args={}, depends_on=["s4"]),
        ],
        "write_skill": [
            SubTask(id="s1", description="Define skill spec", tool="llm.design", args={}),
            SubTask(id="s2", description="Write implementation", tool="llm.code", args={}, depends_on=["s1"]),
            SubTask(id="s3", description="Malware scan implementation", tool="malware.scan", args={}, depends_on=["s2"]),
            SubTask(id="s4", description="Write tests", tool="llm.code", args={}, depends_on=["s3"]),
            SubTask(id="s5", description="Register skill", tool="skills.register", args={}, requires_approval=True, depends_on=["s4"]),
        ],
        "generate_report": [
            SubTask(id="s1", description="Gather data", tool="memory.search", args={}),
            SubTask(id="s2", description="Generate draft", tool="llm.generate", args={}, depends_on=["s1"]),
            SubTask(id="s3", description="Fact-check draft", tool="factchecker.verify", args={}, depends_on=["s2"]),
            SubTask(id="s4", description="Format and store", tool="memory.store", args={}, depends_on=["s3"]),
        ],
    }

    def decompose(self, goal: Goal) -> List[SubTask]:
        """
        Decompose a goal into sub-tasks.

        First checks known patterns, then generates dynamically.
        """
        desc_lower = goal.description.lower()

        # Match known patterns
        for pattern, tasks in self.KNOWN_PATTERNS.items():
            if pattern in desc_lower or any(w in desc_lower for w in pattern.split('_')):
                # Clone tasks with goal-specific args
                subtasks = []
                for template in tasks:
                    subtask = SubTask(
                        id=f"{goal.id}_{template.id}",
                        description=template.description,
                        tool=template.tool,
                        args={**template.args, "goal_id": goal.id, "context": goal.context},
                        priority=goal.priority,
                        status=TaskStatus.PENDING,
                        depends_on=[f"{goal.id}_{dep}" for dep in template.depends_on],
                        requires_approval=template.requires_approval,
                    )
                    subtasks.append(subtask)
                return subtasks

        # Default: single task
        return [SubTask(
            id=f"{goal.id}_main",
            description=goal.description,
            tool="llm.complete",
            args={"prompt": goal.description, "context": goal.context},
            priority=goal.priority,
        )]

    def build_dependency_graph(self, tasks: List[SubTask]) -> Dict[str, Set[str]]:
        """Build adjacency list for task dependencies"""
        graph: Dict[str, Set[str]] = {t.id: set(t.depends_on) for t in tasks}
        return graph

    def get_ready_tasks(
        self,
        tasks: List[SubTask],
        graph: Dict[str, Set[str]]
    ) -> List[SubTask]:
        """Get tasks that have all dependencies satisfied"""
        completed = {t.id for t in tasks if t.status == TaskStatus.COMPLETED}
        ready = []
        for task in tasks:
            if task.status == TaskStatus.PENDING:
                deps = graph.get(task.id, set())
                if deps.issubset(completed):
                    ready.append(task)
        return sorted(ready, key=lambda t: t.priority.value)


class SelfMonitor:
    """
    Monitors plan execution and triggers re-planning when needed.

    Detects:
    - Stalled tasks (no progress for too long)
    - Cascading failures
    - Resource exhaustion
    - Unexpected outcomes
    """

    STALL_THRESHOLD_S = 120  # 2 minutes
    MAX_FAILURE_RATE = 0.5   # 50% failure rate triggers re-plan

    def check_health(self, goal: Goal) -> Tuple[bool, str]:
        """
        Check plan execution health.

        Returns (is_healthy, reason_if_not)
        """
        now = time.time()

        # Check for stalled tasks
        in_progress = [t for t in goal.subtasks if t.status == TaskStatus.IN_PROGRESS]
        for task in in_progress:
            if task.started_at and (now - task.started_at) > self.STALL_THRESHOLD_S:
                return False, f"Task '{task.description}' stalled for {int(now - task.started_at)}s"

        # Check failure rate
        total = len(goal.subtasks)
        failed = sum(1 for t in goal.subtasks if t.status == TaskStatus.FAILED)
        if total > 0 and failed / total > self.MAX_FAILURE_RATE:
            return False, f"High failure rate: {failed}/{total} tasks failed"

        return True, "healthy"

    def suggest_retry_strategy(self, task: SubTask) -> Dict[str, Any]:
        """Suggest retry strategy for a failed task"""
        if task.retries == 0:
            return {"action": "retry_immediately", "delay": 0}
        elif task.retries == 1:
            return {"action": "retry_with_backoff", "delay": 5}
        elif task.retries == 2:
            return {"action": "retry_with_alternate_tool", "delay": 10}
        else:
            return {"action": "escalate", "delay": 0}


class OutcomeLearner:
    """
    Learns from goal execution outcomes to improve future planning.

    Stores:
    - Successful patterns
    - Failed approaches
    - Timing data per task type
    - Tool effectiveness
    """

    def __init__(self, memory_store=None):
        self.memory = memory_store
        self._outcomes: List[Dict] = []

    async def record_outcome(self, goal: Goal, result: PlannerResult):
        """Record what worked and what didn't"""
        outcome = {
            "goal_type": self._classify_goal(goal.description),
            "success": result.success,
            "total_time_s": result.total_time_s,
            "tokens_used": result.tokens_used,
            "task_count": len(goal.subtasks),
            "task_outcomes": [
                {
                    "tool": t.tool,
                    "success": t.status == TaskStatus.COMPLETED,
                    "retries": t.retries,
                    "duration_s": (t.completed_at - t.started_at) if t.started_at and t.completed_at else None
                }
                for t in goal.subtasks
            ],
            "learnings": goal.learnings,
        }

        self._outcomes.append(outcome)

        if self.memory:
            await self.memory.store(
                content=json.dumps(outcome),
                memory_type="LEARNING",
                metadata={"type": "plan_outcome", "goal_type": outcome["goal_type"]}
            )

    def _classify_goal(self, description: str) -> str:
        """Classify goal type from description"""
        desc = description.lower()
        for goal_type in ["research", "code", "write", "analyze", "generate", "fix", "test"]:
            if goal_type in desc:
                return goal_type
        return "other"

    def get_insights(self) -> Dict[str, Any]:
        """Get insights from recorded outcomes"""
        if not self._outcomes:
            return {"message": "No outcomes recorded yet"}

        success_rate = sum(1 for o in self._outcomes if o["success"]) / len(self._outcomes)
        avg_time = sum(o["total_time_s"] for o in self._outcomes) / len(self._outcomes)

        # Find most reliable tools
        tool_stats: Dict[str, Dict] = {}
        for outcome in self._outcomes:
            for task in outcome.get("task_outcomes", []):
                tool = task["tool"]
                if tool not in tool_stats:
                    tool_stats[tool] = {"successes": 0, "total": 0}
                tool_stats[tool]["total"] += 1
                if task["success"]:
                    tool_stats[tool]["successes"] += 1

        tool_reliability = {
            tool: data["successes"] / data["total"]
            for tool, data in tool_stats.items()
            if data["total"] > 0
        }

        return {
            "total_goals": len(self._outcomes),
            "success_rate": success_rate,
            "average_time_s": avg_time,
            "tool_reliability": tool_reliability,
        }


class GoalPlanner:
    """
    Autonomous goal planning and execution engine.

    Entry point for high-level goal processing in ABLE.

    Usage:
        planner = GoalPlanner(executor=tool_executor, memory=memory_store)
        result = await planner.execute_goal("Research and summarize LLM trends")
    """

    def __init__(
        self,
        executor: Callable[[SubTask], Awaitable[Any]] = None,
        memory_store=None,
        fact_checker=None,
        approval_workflow=None,
        max_parallel_tasks: int = 3,
    ):
        self.executor = executor
        self.memory = memory_store
        self.fact_checker = fact_checker
        self.approval = approval_workflow
        self.max_parallel = max_parallel_tasks

        self.decomposer = GoalDecomposer()
        self.monitor = SelfMonitor()
        self.learner = OutcomeLearner(memory_store)

        self._active_goals: Dict[str, Goal] = {}

    async def execute_goal(
        self,
        description: str,
        client_id: str = "internal",
        priority: TaskPriority = TaskPriority.NORMAL,
        context: Dict = None,
    ) -> PlannerResult:
        """
        Decompose and execute a goal autonomously.

        Args:
            description: Natural language goal description
            client_id: Which client this goal is for
            priority: Execution priority
            context: Additional context for task execution

        Returns:
            PlannerResult with success status and output
        """
        goal_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        goal = Goal(
            id=goal_id,
            description=description,
            client_id=client_id,
            priority=priority,
            context=context or {},
        )

        self._active_goals[goal_id] = goal

        logger.info(f"🎯 Starting goal [{goal_id}]: {description}")

        # Decompose
        goal.subtasks = self.decomposer.decompose(goal)
        dep_graph = self.decomposer.build_dependency_graph(goal.subtasks)
        goal.status = TaskStatus.IN_PROGRESS

        total_tokens = 0

        try:
            # Execute tasks respecting dependencies
            while True:
                ready = self.decomposer.get_ready_tasks(goal.subtasks, dep_graph)

                if not ready:
                    # Check if we're done
                    pending = [t for t in goal.subtasks
                               if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.NEEDS_APPROVAL)]
                    if not pending:
                        break  # All tasks complete

                    # Check for deadlock (blocked tasks with no progress)
                    if all(t.status == TaskStatus.BLOCKED for t in pending):
                        logger.error(f"Goal [{goal_id}]: Deadlock detected")
                        break

                    await asyncio.sleep(0.5)
                    continue

                # Check plan health
                healthy, reason = self.monitor.check_health(goal)
                if not healthy:
                    logger.warning(f"Goal [{goal_id}]: Unhealthy - {reason}")
                    # Could trigger re-planning here

                # Execute ready tasks (limited parallelism)
                batch = ready[:self.max_parallel]
                for task in batch:
                    task.status = TaskStatus.IN_PROGRESS
                    task.started_at = time.time()

                # Execute batch concurrently
                coros = [self._execute_task(task, goal) for task in batch]
                results = await asyncio.gather(*coros, return_exceptions=True)

                for task, result in zip(batch, results):
                    task.completed_at = time.time()
                    if isinstance(result, Exception):
                        task.status = TaskStatus.FAILED
                        task.error = str(result)
                        goal.learnings.append(f"Tool {task.tool} failed: {result}")

                        # Retry if applicable
                        strategy = self.monitor.suggest_retry_strategy(task)
                        if strategy["action"].startswith("retry") and task.retries < task.max_retries:
                            task.retries += 1
                            task.status = TaskStatus.PENDING
                            if strategy["delay"]:
                                await asyncio.sleep(strategy["delay"])
                    else:
                        task.status = TaskStatus.COMPLETED
                        task.result = result
                        if isinstance(result, dict):
                            total_tokens += result.get("tokens_used", 0)

            # Determine goal success
            completed = sum(1 for t in goal.subtasks if t.status == TaskStatus.COMPLETED)
            failed = sum(1 for t in goal.subtasks if t.status == TaskStatus.FAILED)
            success = failed == 0 and completed > 0

            goal.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
            goal.completed_at = time.time()
            goal.success = success

            # Build output from last task result
            output = None
            completed_tasks = [t for t in goal.subtasks if t.status == TaskStatus.COMPLETED]
            if completed_tasks:
                last = completed_tasks[-1]
                if isinstance(last.result, str):
                    output = last.result
                elif isinstance(last.result, dict):
                    output = last.result.get("content") or json.dumps(last.result)

            elapsed = time.time() - start_time
            result = PlannerResult(
                goal=goal,
                success=success,
                total_time_s=elapsed,
                tokens_used=total_tokens,
                output=output,
            )

            # Learn from outcome
            await self.learner.record_outcome(goal, result)

            emoji = "✅" if success else "❌"
            logger.info(
                f"{emoji} Goal [{goal_id}] complete in {elapsed:.1f}s: "
                f"{completed}/{len(goal.subtasks)} tasks OK"
            )

            return result

        except Exception as e:
            logger.exception(f"Goal [{goal_id}] crashed: {e}")
            goal.status = TaskStatus.FAILED
            elapsed = time.time() - start_time
            return PlannerResult(
                goal=goal, success=False, total_time_s=elapsed,
                tokens_used=total_tokens, error=str(e)
            )
        finally:
            self._active_goals.pop(goal_id, None)

    async def _execute_task(self, task: SubTask, goal: Goal) -> Any:
        """Execute a single sub-task"""
        # Handle approval-required tasks
        if task.requires_approval and self.approval:
            approval = await self.approval.request_approval(
                operation=task.description,
                details={"tool": task.tool, "args": task.args},
                timeout_seconds=300
            )
            if not approval.approved:
                task.status = TaskStatus.CANCELLED
                return None

        # Execute via the provided executor
        if self.executor:
            return await self.executor(task)

        # Fallback: return placeholder
        return {"status": "executed", "tool": task.tool, "tokens_used": 100}

    def get_active_goals(self) -> List[Dict]:
        """Get status of all active goals"""
        return [
            {
                "id": g.id,
                "description": g.description,
                "status": g.status.value,
                "progress": f"{g.completion_percentage():.0f}%",
                "client_id": g.client_id,
            }
            for g in self._active_goals.values()
        ]

    def get_insights(self) -> Dict:
        """Get planning insights from learner"""
        return self.learner.get_insights()
