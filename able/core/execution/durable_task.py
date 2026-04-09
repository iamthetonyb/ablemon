"""Durable task execution framework — checkpointed, resumable tasks.

Forked from gnhf's iteration-commit-rollback pattern, adapted for ABLE.
Tasks checkpoint their state at safe boundaries. On failure, they resume
from the last checkpoint instead of restarting from scratch.

Usage:
    class MyTask(DurableTask):
        async def run(self, ctx: TaskContext) -> TaskResult:
            data = await ctx.retry(fetch_data)
            await ctx.checkpoint({"data": data})
            result = await process(data)
            return TaskResult(success=True, summary="Done", data=result)

    runner = TaskRunner()
    result = await runner.execute(MyTask(task_id="my-task"))
    # On crash: result = await runner.resume("my-task")

Storage: SQLite table `durable_tasks` in `data/durable_tasks.db`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_DB_DIR = Path(__file__).resolve().parents[3] / "data"
_DB_PATH = _DB_DIR / "durable_tasks.db"


@dataclass
class TaskCheckpoint:
    """Serializable snapshot of task state at a safe boundary."""

    checkpoint_id: str
    task_id: str
    step_name: str
    state: dict
    created_at: str
    iteration: int = 0

    def to_json(self) -> str:
        return json.dumps({
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "step_name": self.step_name,
            "state": self.state,
            "created_at": self.created_at,
            "iteration": self.iteration,
        })

    @classmethod
    def from_json(cls, data: str) -> TaskCheckpoint:
        d = json.loads(data)
        return cls(**d)


@dataclass
class TaskResult:
    """Outcome of a durable task execution."""

    success: bool
    summary: str
    data: Any = None
    key_changes: list[str] = field(default_factory=list)
    key_learnings: list[str] = field(default_factory=list)
    checkpoints_used: int = 0
    duration_s: float = 0.0
    error: Optional[str] = None


class TaskContext:
    """Execution context passed to DurableTask.run() — provides checkpoint/retry/waitpoint."""

    def __init__(self, task_id: str, store: TaskStore, iteration: int = 0):
        self.task_id = task_id
        self._store = store
        self._iteration = iteration
        self._checkpoint_count = 0

    async def checkpoint(self, state: dict, step_name: str = "") -> None:
        """Save a checkpoint at a safe boundary. Call after completing a phase."""
        self._checkpoint_count += 1
        cp = TaskCheckpoint(
            checkpoint_id=f"{self.task_id}-cp-{self._checkpoint_count}",
            task_id=self.task_id,
            step_name=step_name or f"step-{self._checkpoint_count}",
            state=state,
            created_at=datetime.now(timezone.utc).isoformat(),
            iteration=self._iteration,
        )
        self._store.save_checkpoint(cp)
        logger.info("Checkpoint saved: %s/%s", self.task_id, cp.step_name)

        # Buddy XP for checkpoint
        try:
            from able.core.buddy.xp import award_durable_task_xp
            award_durable_task_xp("checkpoint")
        except Exception:
            pass

    async def retry(
        self,
        fn: Callable,
        *args: Any,
        max_attempts: int = 3,
        backoff: str = "exponential",
        **kwargs: Any,
    ) -> Any:
        """Retry a function with backoff. Raises on final failure."""
        last_error = None
        for attempt in range(max_attempts):
            try:
                if asyncio.iscoroutinefunction(fn):
                    return await fn(*args, **kwargs)
                return fn(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_attempts - 1:
                    delay = (2 ** attempt) if backoff == "exponential" else 1
                    logger.warning(
                        "Retry %d/%d for %s: %s (backoff %ds)",
                        attempt + 1, max_attempts, fn.__name__, e, delay,
                    )
                    await asyncio.sleep(delay)
        raise last_error  # type: ignore[misc]

    async def waitpoint(self, reason: str) -> None:
        """Pause for human approval. Currently logs and continues."""
        logger.info("Waitpoint: %s (auto-continuing)", reason)


class DurableTask(ABC):
    """Base class for durable tasks. Override run() with your logic."""

    def __init__(self, task_id: Optional[str] = None):
        self.task_id = task_id or f"task-{uuid.uuid4().hex[:8]}"

    @abstractmethod
    async def run(self, ctx: TaskContext) -> TaskResult:
        """Execute the task. Call ctx.checkpoint() at safe boundaries."""

    async def resume(self, ctx: TaskContext, checkpoint: TaskCheckpoint) -> TaskResult:
        """Resume from a checkpoint. Override for custom resume logic.

        Default: re-runs run() — subclasses can load checkpoint.state
        and skip completed phases.
        """
        return await self.run(ctx)


class TaskStore:
    """SQLite-backed storage for task checkpoints and metadata."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS durable_tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                task_class TEXT,
                created_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                result_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                step_name TEXT,
                state_json TEXT NOT NULL,
                created_at TEXT,
                iteration INTEGER DEFAULT 0,
                FOREIGN KEY (task_id) REFERENCES durable_tasks(task_id)
            )
        """)
        conn.commit()
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    def register_task(self, task: DurableTask) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO durable_tasks (task_id, status, task_class, created_at) VALUES (?, ?, ?, ?)",
            (task.task_id, "pending", type(task).__name__, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()

    def update_status(self, task_id: str, status: str, result: Optional[TaskResult] = None) -> None:
        conn = self._conn()
        now = datetime.now(timezone.utc).isoformat()
        if status == "running":
            conn.execute("UPDATE durable_tasks SET status = ?, started_at = ? WHERE task_id = ?", (status, now, task_id))
        elif status in ("completed", "failed"):
            result_json = json.dumps({
                "success": result.success if result else False,
                "summary": result.summary if result else "",
                "error": result.error if result else None,
            }) if result else None
            conn.execute(
                "UPDATE durable_tasks SET status = ?, completed_at = ?, result_json = ? WHERE task_id = ?",
                (status, now, result_json, task_id),
            )
        conn.commit()
        conn.close()

    def save_checkpoint(self, cp: TaskCheckpoint) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO task_checkpoints (checkpoint_id, task_id, step_name, state_json, created_at, iteration) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cp.checkpoint_id, cp.task_id, cp.step_name, json.dumps(cp.state), cp.created_at, cp.iteration),
        )
        conn.commit()
        conn.close()

    def get_latest_checkpoint(self, task_id: str) -> Optional[TaskCheckpoint]:
        conn = self._conn()
        row = conn.execute(
            "SELECT checkpoint_id, task_id, step_name, state_json, created_at, iteration "
            "FROM task_checkpoints WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return TaskCheckpoint(
            checkpoint_id=row[0], task_id=row[1], step_name=row[2],
            state=json.loads(row[3]), created_at=row[4], iteration=row[5],
        )

    def list_resumable(self) -> list[dict]:
        """List tasks that failed and have checkpoints — eligible for resume."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT t.task_id, t.status, t.task_class, t.created_at, "
            "  (SELECT COUNT(*) FROM task_checkpoints WHERE task_id = t.task_id) as cp_count "
            "FROM durable_tasks t WHERE t.status IN ('failed', 'running') "
            "ORDER BY t.created_at DESC",
        ).fetchall()
        conn.close()
        return [
            {"task_id": r[0], "status": r[1], "task_class": r[2], "created_at": r[3], "checkpoints": r[4]}
            for r in rows
        ]


class TaskRunner:
    """Execute and manage durable tasks."""

    def __init__(self, store: Optional[TaskStore] = None):
        self.store = store or TaskStore()

    async def execute(self, task: DurableTask) -> TaskResult:
        """Run a durable task from scratch with checkpoint support."""
        self.store.register_task(task)
        self.store.update_status(task.task_id, "running")
        start = time.time()

        try:
            ctx = TaskContext(task.task_id, self.store)
            result = await task.run(ctx)
            result.duration_s = time.time() - start
            result.checkpoints_used = ctx._checkpoint_count
            self.store.update_status(task.task_id, "completed", result)

            # Buddy XP for completion
            try:
                from able.core.buddy.xp import award_durable_task_xp
                award_durable_task_xp("complete")
            except Exception:
                pass

            logger.info("Task %s completed: %s (%.1fs)", task.task_id, result.summary, result.duration_s)
            return result

        except Exception as e:
            result = TaskResult(
                success=False,
                summary=f"Task failed: {e}",
                error=str(e),
                duration_s=time.time() - start,
            )
            self.store.update_status(task.task_id, "failed", result)
            logger.error("Task %s failed: %s", task.task_id, e)
            return result

    async def resume(self, task: DurableTask) -> TaskResult:
        """Resume a task from its latest checkpoint."""
        cp = self.store.get_latest_checkpoint(task.task_id)
        if not cp:
            logger.info("No checkpoint found for %s — running from scratch", task.task_id)
            return await self.execute(task)

        self.store.update_status(task.task_id, "running")
        start = time.time()

        # Buddy XP for resilient resume
        try:
            from able.core.buddy.xp import award_durable_task_xp
            award_durable_task_xp("resume")
        except Exception:
            pass

        try:
            ctx = TaskContext(task.task_id, self.store, iteration=cp.iteration + 1)
            result = await task.resume(ctx, cp)
            result.duration_s = time.time() - start
            result.checkpoints_used = ctx._checkpoint_count
            self.store.update_status(task.task_id, "completed", result)
            logger.info("Task %s resumed and completed: %s (%.1fs)", task.task_id, result.summary, result.duration_s)
            return result

        except Exception as e:
            result = TaskResult(
                success=False,
                summary=f"Resume failed: {e}",
                error=str(e),
                duration_s=time.time() - start,
            )
            self.store.update_status(task.task_id, "failed", result)
            logger.error("Task %s resume failed: %s", task.task_id, e)
            return result
