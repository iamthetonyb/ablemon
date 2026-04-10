"""Tests for able.core.execution.overnight_loop — autonomous iteration-commit-rollback."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from able.core.execution.overnight_loop import (
    IterationResult,
    OvernightLoop,
    OvernightReport,
)


@pytest.fixture
def work_dir(tmp_path):
    """Temporary work directory with git-like structure."""
    return tmp_path


def _success_result(iteration: int = 1) -> IterationResult:
    return IterationResult(
        success=True,
        summary=f"Iteration {iteration} succeeded",
        key_changes_made=["modified file.py"],
        key_learnings=["learned something useful"],
        files_changed=["file.py"],
    )


def _failure_result(iteration: int = 1) -> IterationResult:
    return IterationResult(
        success=False,
        summary=f"Iteration {iteration} failed",
        key_learnings=["this approach doesn't work"],
    )


# ── Dataclass basics ────────────────────────────────────────────

def test_iteration_result_defaults():
    r = IterationResult(success=True, summary="ok")
    assert r.key_changes_made == []
    assert r.key_learnings == []
    assert r.files_changed == []


def test_overnight_report_success_rate():
    report = OvernightReport(run_id="test")
    report.iterations_total = 10
    report.iterations_succeeded = 7
    assert report.success_rate == 70.0


def test_overnight_report_zero_iterations():
    report = OvernightReport(run_id="test")
    assert report.success_rate == 0.0


# ── Successful run ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_iterations_succeed(work_dir):
    call_count = 0

    async def task_fn(iteration, notes):
        nonlocal call_count
        call_count += 1
        return _success_result(iteration)

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=3,
        run_id="test-success",
    )

    with patch("subprocess.run"):
        report = await loop.run()

    assert report.iterations_total == 3
    assert report.iterations_succeeded == 3
    assert report.iterations_failed == 0
    assert report.abort_reason is None
    assert call_count == 3
    assert report.duration_s > 0


@pytest.mark.asyncio
async def test_git_commit_on_success(work_dir):
    """Verify git add + commit are called on successful iterations."""
    async def task_fn(iteration, notes):
        return _success_result(iteration)

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=1,
        run_id="test-commit",
    )

    with patch("subprocess.run") as mock_run:
        await loop.run()

    # Should have called git add -A and git commit
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("git" in c and "add" in c for c in calls)
    assert any("git" in c and "commit" in c for c in calls)


# ── Failure handling ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_git_rollback_on_failure(work_dir):
    """Verify git checkout + clean are called on failed iterations."""
    async def task_fn(iteration, notes):
        return _failure_result(iteration)

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=3,
        max_consecutive_failures=3,
        base_backoff_s=0.001,
        run_id="test-rollback",
    )

    with patch("subprocess.run") as mock_run:
        report = await loop.run()

    calls = [str(c) for c in mock_run.call_args_list]
    assert any("checkout" in c for c in calls)
    assert any("clean" in c for c in calls)
    assert report.iterations_failed == 3


# ── 3-consecutive-failure abort ──────────────────────────────────

@pytest.mark.asyncio
async def test_abort_after_consecutive_failures(work_dir):
    """Loop aborts after max_consecutive_failures failures in a row."""
    async def task_fn(iteration, notes):
        return _failure_result(iteration)

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=10,
        max_consecutive_failures=3,
        base_backoff_s=0.001,  # Fast for testing
        run_id="test-abort",
    )

    with patch("subprocess.run"):
        report = await loop.run()

    assert report.iterations_total == 3
    assert "3 consecutive failures" in report.abort_reason


@pytest.mark.asyncio
async def test_success_resets_failure_counter(work_dir):
    """A success between failures resets the consecutive failure counter."""
    iteration_results = [
        _failure_result(1),
        _failure_result(2),
        _success_result(3),  # Resets counter
        _failure_result(4),
        _failure_result(5),
        _success_result(6),  # Resets counter again
    ]
    idx = 0

    async def task_fn(iteration, notes):
        nonlocal idx
        result = iteration_results[idx]
        idx += 1
        return result

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=6,
        max_consecutive_failures=3,
        base_backoff_s=0.001,
        run_id="test-reset",
    )

    with patch("subprocess.run"):
        report = await loop.run()

    # All 6 should run — never hit 3 consecutive failures
    assert report.iterations_total == 6
    assert report.abort_reason is None
    assert report.iterations_succeeded == 2
    assert report.iterations_failed == 4


# ── Exception handling ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_exception_treated_as_failure(work_dir):
    """Exceptions from task_fn are caught and treated as failures."""
    async def task_fn(iteration, notes):
        raise RuntimeError("Simulated crash")

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=3,
        max_consecutive_failures=3,
        base_backoff_s=0.001,
        run_id="test-crash",
    )

    with patch("subprocess.run"):
        report = await loop.run()

    assert report.iterations_failed == 3
    assert "3 consecutive failures" in report.abort_reason
    assert "Crashed" in report.results[0]["summary"]


# ── Abort signal ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_abort_signal(work_dir):
    """abort() stops the loop after current iteration."""
    call_count = 0

    async def task_fn(iteration, notes):
        nonlocal call_count
        call_count += 1
        return _success_result(iteration)

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=10,
        run_id="test-signal",
    )

    # Abort before starting — should run 0 iterations
    loop.abort()

    with patch("subprocess.run"):
        report = await loop.run()

    assert call_count == 0
    assert report.abort_reason == "Aborted by operator"


# ── Cross-iteration notes.md ────────────────────────────────────

@pytest.mark.asyncio
async def test_notes_accumulate(work_dir):
    """notes.md grows with each iteration's results."""
    async def task_fn(iteration, notes):
        return IterationResult(
            success=True,
            summary=f"Did thing {iteration}",
            key_learnings=[f"Learned {iteration}"],
            key_changes_made=[f"Changed {iteration}"],
        )

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=3,
        run_id="test-notes",
    )

    with patch("subprocess.run"):
        report = await loop.run()

    notes_path = work_dir / "data" / "overnight_runs" / "test-notes" / "notes.md"
    assert notes_path.exists()
    content = notes_path.read_text()
    assert "Iteration 1 [SUCCESS]" in content
    assert "Iteration 2 [SUCCESS]" in content
    assert "Iteration 3 [SUCCESS]" in content
    assert "Learned 2" in content
    assert "Changed 1" in content


@pytest.mark.asyncio
async def test_notes_passed_to_task_fn(work_dir):
    """Each iteration receives the cumulative notes from prior iterations."""
    received_notes = []

    async def task_fn(iteration, notes):
        received_notes.append(notes)
        return _success_result(iteration)

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=3,
        run_id="test-notes-pass",
    )

    with patch("subprocess.run"):
        await loop.run()

    # First iteration gets just the header
    assert "Overnight Run" in received_notes[0]
    # Later iterations get accumulated content
    assert len(received_notes[2]) > len(received_notes[0])


# ── Metadata persistence ────────────────────────────────────────

@pytest.mark.asyncio
async def test_metadata_file_written(work_dir):
    """meta.json is written with run statistics."""
    async def task_fn(iteration, notes):
        return _success_result(iteration)

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=2,
        run_id="test-meta",
    )

    with patch("subprocess.run"):
        await loop.run()

    meta_path = work_dir / "data" / "overnight_runs" / "test-meta" / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["run_id"] == "test-meta"
    assert meta["iterations_total"] == 2
    assert meta["iterations_succeeded"] == 2
    assert meta["success_rate"] == 100.0
    assert "completed_at" in meta


# ── Exponential backoff ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_backoff_timing(work_dir):
    """Verify exponential backoff formula: base * 2^(failures-1)."""
    sleep_delays = []

    original_sleep = asyncio.sleep

    async def mock_sleep(delay):
        sleep_delays.append(delay)

    async def task_fn(iteration, notes):
        return _failure_result(iteration)

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=3,
        max_consecutive_failures=3,
        base_backoff_s=10.0,
        run_id="test-backoff",
    )

    with patch("subprocess.run"), \
         patch("able.core.execution.overnight_loop.asyncio.sleep", side_effect=mock_sleep):
        await loop.run()

    # 1st failure: 10 * 2^0 = 10, 2nd failure: 10 * 2^1 = 20, 3rd: aborts (no sleep)
    assert len(sleep_delays) == 2
    assert sleep_delays[0] == 10.0
    assert sleep_delays[1] == 20.0


# ── Report in results ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_contains_all_results(work_dir):
    results_seq = [_success_result(1), _failure_result(2), _success_result(3)]
    idx = 0

    async def task_fn(iteration, notes):
        nonlocal idx
        r = results_seq[idx]
        idx += 1
        return r

    loop = OvernightLoop(
        task_fn=task_fn,
        work_dir=work_dir,
        max_iterations=3,
        base_backoff_s=0.001,
        run_id="test-results",
    )

    with patch("subprocess.run"):
        report = await loop.run()

    assert len(report.results) == 3
    assert report.results[0]["success"] is True
    assert report.results[1]["success"] is False
    assert report.results[2]["success"] is True
    assert report.notes != ""  # Notes should be populated
