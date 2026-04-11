"""Overnight Orchestrator Skill — wraps OvernightLoop as a registered ABLE skill.

Triggers: "run overnight", "autonomous loop", "work while I sleep"
Delegates to: able/core/execution/overnight_loop.py

Usage:
    result = await run_overnight_skill({
        "task": "refactor auth module and add tests",
        "max_iterations": 10,
    })
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def run_overnight_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the overnight orchestrator skill.

    Args:
        args: Skill arguments — must include "task", optionally
              "max_iterations", "work_dir", "commit_prefix".

    Returns:
        Dict with "report" (serialized OvernightReport) and "notes" content.
    """
    from able.core.execution.overnight_loop import OvernightLoop, IterationResult

    task_description = args.get("task", "")
    if not task_description:
        return {"error": "No task description provided", "success": False}

    max_iterations = int(args.get("max_iterations", 10))
    work_dir = Path(args.get("work_dir", ".")).resolve()

    if not work_dir.exists():
        return {"error": f"Work directory does not exist: {work_dir}", "success": False}

    # Define the iteration function that the loop will call
    async def _iteration_fn(
        iteration: int,
        notes: str,
    ) -> IterationResult:
        """Single iteration of the overnight task.

        This is a template — in production, this would dispatch to an
        LLM agent or tool chain. For now, it provides the skeleton that
        callers can override via subclassing or dependency injection.
        """
        logger.info(
            "Overnight iteration %d: task=%r, notes_len=%d",
            iteration, task_description[:80], len(notes),
        )

        # Placeholder: a real implementation would invoke the gateway
        # or an agent swarm to execute one step of the task.
        # The overnight_loop handles commit/rollback around this function.
        return IterationResult(
            success=True,
            summary=f"Iteration {iteration}: executed task step",
            key_learnings=[f"Completed step {iteration} of: {task_description[:60]}"],
        )

    loop = OvernightLoop(
        task_fn=_iteration_fn,
        work_dir=work_dir,
        max_iterations=max_iterations,
    )

    report = await loop.run()

    # Read accumulated notes from the run directory
    notes_content = ""
    if hasattr(loop, '_notes_path') and loop._notes_path.exists():
        notes_content = loop._notes_path.read_text()
    else:
        # Fallback: check root notes.md
        notes_path = work_dir / "notes.md"
        if notes_path.exists():
            notes_content = notes_path.read_text()

    return {
        "success": report.abort_reason is None,
        "report": {
            "run_id": report.run_id,
            "iterations_total": report.iterations_total,
            "iterations_succeeded": report.iterations_succeeded,
            "iterations_failed": report.iterations_failed,
            "abort_reason": report.abort_reason,
        },
        "notes": notes_content,
    }


# Skill metadata for SKILL_INDEX.yaml registration
SKILL_METADATA = {
    "name": "overnight",
    "version": "1.0.0",
    "triggers": ["run overnight", "autonomous loop", "work while I sleep", "overnight"],
    "trust_level": 3,
    "category": "execution",
}
