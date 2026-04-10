"""Tests for able.core.execution.durable_task — checkpointed, resumable task framework."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from able.core.execution.durable_task import (
    DurableTask,
    TaskCheckpoint,
    TaskContext,
    TaskResult,
    TaskRunner,
    TaskStore,
)


@pytest.fixture
def tmp_store(tmp_path):
    """TaskStore backed by a temporary SQLite database."""
    return TaskStore(db_path=tmp_path / "test_tasks.db")


@pytest.fixture
def runner(tmp_store):
    return TaskRunner(store=tmp_store)


# ── TaskCheckpoint serialization ──────────────────────────────────

def test_checkpoint_round_trip():
    cp = TaskCheckpoint(
        checkpoint_id="cp-1",
        task_id="task-1",
        step_name="fetch",
        state={"data": [1, 2, 3], "progress": 0.5},
        created_at="2026-04-09T00:00:00Z",
        iteration=2,
    )
    json_str = cp.to_json()
    restored = TaskCheckpoint.from_json(json_str)
    assert restored.checkpoint_id == "cp-1"
    assert restored.state == {"data": [1, 2, 3], "progress": 0.5}
    assert restored.iteration == 2


def test_checkpoint_empty_state():
    cp = TaskCheckpoint(
        checkpoint_id="cp-0", task_id="t", step_name="init",
        state={}, created_at="2026-01-01T00:00:00Z",
    )
    restored = TaskCheckpoint.from_json(cp.to_json())
    assert restored.state == {}


# ── TaskStore persistence ─────────────────────────────────────────

def test_store_register_and_status(tmp_store):
    class Dummy(DurableTask):
        async def run(self, ctx):
            return TaskResult(success=True, summary="ok")

    task = Dummy(task_id="test-reg")
    tmp_store.register_task(task)
    tmp_store.update_status("test-reg", "running")
    tmp_store.update_status("test-reg", "completed", TaskResult(success=True, summary="done"))

    # Verify via raw SQL
    import sqlite3
    conn = sqlite3.connect(str(tmp_store._db_path))
    row = conn.execute("SELECT status FROM durable_tasks WHERE task_id = ?", ("test-reg",)).fetchone()
    conn.close()
    assert row[0] == "completed"


def test_store_save_and_get_checkpoint(tmp_store):
    cp = TaskCheckpoint(
        checkpoint_id="cp-1", task_id="task-cp",
        step_name="phase-1", state={"x": 42},
        created_at="2026-04-09T10:00:00Z",
    )
    tmp_store.save_checkpoint(cp)
    latest = tmp_store.get_latest_checkpoint("task-cp")
    assert latest is not None
    assert latest.state == {"x": 42}
    assert latest.step_name == "phase-1"


def test_store_no_checkpoint_returns_none(tmp_store):
    assert tmp_store.get_latest_checkpoint("nonexistent") is None


def test_store_list_resumable(tmp_store):
    class Dummy(DurableTask):
        async def run(self, ctx):
            return TaskResult(success=True, summary="ok")

    task = Dummy(task_id="fail-task")
    tmp_store.register_task(task)
    tmp_store.update_status("fail-task", "failed", TaskResult(success=False, summary="boom"))
    cp = TaskCheckpoint(
        checkpoint_id="cp-f1", task_id="fail-task",
        step_name="step-1", state={},
        created_at="2026-04-09T10:00:00Z",
    )
    tmp_store.save_checkpoint(cp)

    resumable = tmp_store.list_resumable()
    assert len(resumable) >= 1
    assert any(r["task_id"] == "fail-task" for r in resumable)


# ── TaskContext ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_checkpoint(tmp_store):
    ctx = TaskContext("ctx-test", tmp_store)
    with patch("able.core.buddy.xp.award_durable_task_xp", side_effect=Exception("no buddy")):
        await ctx.checkpoint({"step": 1}, step_name="first")
    assert ctx._checkpoint_count == 1
    cp = tmp_store.get_latest_checkpoint("ctx-test")
    assert cp is not None
    assert cp.state == {"step": 1}


@pytest.mark.asyncio
async def test_context_retry_succeeds():
    store = TaskStore(db_path=Path(tempfile.mkdtemp()) / "retry.db")
    ctx = TaskContext("retry-test", store)

    call_count = 0
    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ValueError("transient")
        return "success"

    result = await ctx.retry(flaky, max_attempts=3, backoff="exponential")
    assert result == "success"
    assert call_count == 2


@pytest.mark.asyncio
async def test_context_retry_exhausts():
    store = TaskStore(db_path=Path(tempfile.mkdtemp()) / "exhaust.db")
    ctx = TaskContext("exhaust-test", store)

    def always_fails():
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError, match="permanent"):
        await ctx.retry(always_fails, max_attempts=2, backoff="exponential")


@pytest.mark.asyncio
async def test_context_waitpoint_does_not_raise():
    store = TaskStore(db_path=Path(tempfile.mkdtemp()) / "wait.db")
    ctx = TaskContext("wait-test", store)
    await ctx.waitpoint("approval needed")  # Should not raise


# ── TaskRunner execution ──────────────────────────────────────────

class SuccessTask(DurableTask):
    async def run(self, ctx: TaskContext) -> TaskResult:
        await ctx.checkpoint({"phase": "fetch"}, step_name="fetch")
        await ctx.checkpoint({"phase": "process"}, step_name="process")
        return TaskResult(
            success=True,
            summary="All phases complete",
            key_changes=["created output"],
            key_learnings=["task works"],
        )


class FailTask(DurableTask):
    async def run(self, ctx: TaskContext) -> TaskResult:
        await ctx.checkpoint({"phase": "started"})
        raise RuntimeError("Simulated failure after checkpoint")


@pytest.mark.asyncio
async def test_runner_execute_success(runner):
    task = SuccessTask(task_id="success-1")
    result = await runner.execute(task)
    assert result.success
    assert result.checkpoints_used == 2
    assert result.duration_s > 0


@pytest.mark.asyncio
async def test_runner_execute_failure(runner):
    task = FailTask(task_id="fail-1")
    result = await runner.execute(task)
    assert not result.success
    assert "Simulated failure" in result.summary


@pytest.mark.asyncio
async def test_runner_resume_with_checkpoint(runner):
    # First: fail after checkpoint
    task = FailTask(task_id="resume-1")
    result = await runner.execute(task)
    assert not result.success

    # Verify checkpoint exists
    cp = runner.store.get_latest_checkpoint("resume-1")
    assert cp is not None

    # Resume should work (FailTask doesn't override resume, so it re-runs)
    class ResumableTask(DurableTask):
        async def run(self, ctx):
            return TaskResult(success=True, summary="Resumed OK")

        async def resume(self, ctx, checkpoint):
            assert checkpoint.state == {"phase": "started"}
            return TaskResult(success=True, summary="Resumed from checkpoint")

    resumable = ResumableTask(task_id="resume-1")
    result = await runner.resume(resumable)
    assert result.success
    assert "Resumed" in result.summary


@pytest.mark.asyncio
async def test_runner_resume_no_checkpoint(runner):
    """Resume without checkpoint should run from scratch."""
    task = SuccessTask(task_id="no-cp")
    result = await runner.resume(task)
    assert result.success
    assert result.checkpoints_used == 2
