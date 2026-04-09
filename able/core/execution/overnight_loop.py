"""Overnight orchestrator — autonomous iteration-commit-rollback loop.

Forked from gnhf's overnight pattern. Runs a series of task iterations,
committing on success and rolling back on failure. Cross-iteration memory
via notes.md. 3-consecutive-failure abort with exponential backoff.

Usage:
    loop = OvernightLoop(
        task_fn=my_async_task,
        work_dir=Path("/path/to/repo"),
        max_iterations=10,
    )
    report = await loop.run()

Each iteration:
1. Run task_fn(iteration, notes) -> IterationResult
2. Success -> git commit with structured message
3. Failure -> git reset --hard to last good state
4. Update notes.md with learnings
5. Exponential backoff on failure: 60s * 2^(consecutive_failures - 1)
6. 3 consecutive failures -> abort

Results: per-run metadata in `data/overnight_runs/`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


@dataclass
class IterationResult:
    """Outcome of a single overnight iteration."""

    success: bool
    summary: str
    key_changes_made: list[str] = field(default_factory=list)
    key_learnings: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)


@dataclass
class OvernightReport:
    """Full report from an overnight loop run."""

    run_id: str
    iterations_total: int = 0
    iterations_succeeded: int = 0
    iterations_failed: int = 0
    abort_reason: Optional[str] = None
    results: list[dict] = field(default_factory=list)
    duration_s: float = 0.0
    notes: str = ""

    @property
    def success_rate(self) -> float:
        return (self.iterations_succeeded / self.iterations_total * 100) if self.iterations_total > 0 else 0.0


# Type alias for the task function signature
TaskFn = Callable[[int, str], Coroutine[Any, Any, IterationResult]]


class OvernightLoop:
    """Autonomous iteration-commit-rollback orchestrator."""

    def __init__(
        self,
        task_fn: TaskFn,
        work_dir: Path,
        *,
        max_iterations: int = 10,
        max_consecutive_failures: int = 3,
        base_backoff_s: float = 60.0,
        run_id: Optional[str] = None,
    ):
        self.task_fn = task_fn
        self.work_dir = work_dir
        self.max_iterations = max_iterations
        self.max_consecutive_failures = max_consecutive_failures
        self.base_backoff_s = base_backoff_s
        self.run_id = run_id or f"overnight-{int(time.time())}"
        self._notes_path = work_dir / "data" / "overnight_runs" / self.run_id / "notes.md"
        self._meta_path = work_dir / "data" / "overnight_runs" / self.run_id / "meta.json"
        self._aborted = False

    def abort(self) -> None:
        """Signal the loop to stop after the current iteration."""
        self._aborted = True

    async def run(self) -> OvernightReport:
        """Execute the overnight loop. Returns full report."""
        self._notes_path.parent.mkdir(parents=True, exist_ok=True)
        self._notes_path.write_text(f"# Overnight Run: {self.run_id}\n\n")

        report = OvernightReport(run_id=self.run_id)
        consecutive_failures = 0
        start = time.time()

        for iteration in range(1, self.max_iterations + 1):
            if self._aborted:
                report.abort_reason = "Aborted by operator"
                break

            notes = self._notes_path.read_text()
            logger.info("Overnight iteration %d/%d starting", iteration, self.max_iterations)

            try:
                result = await self.task_fn(iteration, notes)
                report.iterations_total += 1

                if result.success:
                    report.iterations_succeeded += 1
                    consecutive_failures = 0
                    self._git_commit(iteration, result)
                    self._append_notes(iteration, result, success=True)

                    # Buddy XP
                    try:
                        from able.core.buddy.xp import award_overnight_xp
                        award_overnight_xp(
                            iteration_count=1,
                            success=True,
                        )
                    except Exception:
                        pass

                else:
                    report.iterations_failed += 1
                    consecutive_failures += 1
                    self._git_rollback()
                    self._append_notes(iteration, result, success=False)

                report.results.append({
                    "iteration": iteration,
                    "success": result.success,
                    "summary": result.summary,
                    "changes": result.key_changes_made,
                    "learnings": result.key_learnings,
                })

            except Exception as e:
                report.iterations_total += 1
                report.iterations_failed += 1
                consecutive_failures += 1
                logger.error("Overnight iteration %d crashed: %s", iteration, e)
                self._git_rollback()
                self._append_notes(iteration, IterationResult(
                    success=False, summary=f"Crashed: {e}",
                    key_learnings=[f"Exception: {type(e).__name__}: {e}"],
                ), success=False)
                report.results.append({
                    "iteration": iteration,
                    "success": False,
                    "summary": f"Crashed: {e}",
                })

            # Abort on consecutive failures
            if consecutive_failures >= self.max_consecutive_failures:
                report.abort_reason = f"{consecutive_failures} consecutive failures — aborting"
                logger.warning("Overnight loop aborting: %s", report.abort_reason)
                break

            # Exponential backoff on failure
            if consecutive_failures > 0 and iteration < self.max_iterations:
                delay = self.base_backoff_s * (2 ** (consecutive_failures - 1))
                logger.info("Backoff: sleeping %.0fs after failure", delay)
                await asyncio.sleep(delay)

        report.duration_s = time.time() - start
        report.notes = self._notes_path.read_text() if self._notes_path.exists() else ""

        # Save metadata
        self._meta_path.write_text(json.dumps({
            "run_id": report.run_id,
            "iterations_total": report.iterations_total,
            "iterations_succeeded": report.iterations_succeeded,
            "iterations_failed": report.iterations_failed,
            "success_rate": report.success_rate,
            "abort_reason": report.abort_reason,
            "duration_s": report.duration_s,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2))

        logger.info(
            "Overnight loop complete: %d/%d succeeded (%.0f%%) in %.0fs",
            report.iterations_succeeded, report.iterations_total,
            report.success_rate, report.duration_s,
        )
        return report

    def _git_commit(self, iteration: int, result: IterationResult) -> None:
        """Commit changes on successful iteration."""
        try:
            subprocess.run(["git", "add", "-A"], cwd=self.work_dir, capture_output=True, timeout=30)
            msg = f"overnight({self.run_id}): iteration {iteration} — {result.summary[:80]}"
            subprocess.run(
                ["git", "commit", "-m", msg, "--allow-empty"],
                cwd=self.work_dir, capture_output=True, timeout=30,
            )
            logger.info("Committed iteration %d: %s", iteration, result.summary[:80])
        except Exception as e:
            logger.warning("Git commit failed for iteration %d: %s", iteration, e)

    def _git_rollback(self) -> None:
        """Roll back to last committed state on failure."""
        try:
            subprocess.run(
                ["git", "checkout", "."],
                cwd=self.work_dir, capture_output=True, timeout=30,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=self.work_dir, capture_output=True, timeout=30,
            )
            logger.info("Rolled back to last good state")
        except Exception as e:
            logger.warning("Git rollback failed: %s", e)

    def _append_notes(self, iteration: int, result: IterationResult, *, success: bool) -> None:
        """Append iteration outcome to notes.md for cross-iteration memory."""
        status = "SUCCESS" if success else "FAILED"
        entry = f"\n## Iteration {iteration} [{status}]\n\n{result.summary}\n"
        if result.key_learnings:
            entry += "\n**Learnings:**\n"
            for learning in result.key_learnings:
                entry += f"- {learning}\n"
        if result.key_changes_made:
            entry += "\n**Changes:**\n"
            for change in result.key_changes_made:
                entry += f"- {change}\n"

        with open(self._notes_path, "a") as f:
            f.write(entry)
