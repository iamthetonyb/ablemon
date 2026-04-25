import asyncio
import time
from datetime import datetime, timezone

from able.scheduler.cron import CronExecutionDB, CronScheduler, JobResult


def _run(coro):
    return asyncio.run(coro)


def _slot(hour: int = 1) -> int:
    return int(datetime(2026, 4, 24, hour, 0, tzinfo=timezone.utc).timestamp())


def test_run_slot_claim_blocks_duplicate_scheduler_instances(tmp_path):
    calls = []

    async def task():
        calls.append(time.time())
        return {"ok": True}

    db_path = tmp_path / "cron.db"
    first = CronScheduler(db_path=str(db_path))
    second = CronScheduler(db_path=str(db_path))
    first_job = first.add_job("nightly-research", "0 1 * * *", task, max_retries=1)
    second_job = second.add_job("nightly-research", "0 1 * * *", task, max_retries=1)

    run_slot = _slot()
    first_result = _run(first._run_job_with_retry(first_job, run_slot=run_slot))
    second_result = _run(second._run_job_with_retry(second_job, run_slot=run_slot))

    assert first_result.success is True
    assert second_result.success is True
    assert second_result.output == {
        "skipped": True,
        "reason": "run_slot_claimed",
        "run_slot": run_slot,
    }
    assert len(calls) == 1
    assert second.db.get_run_claim("nightly-research", run_slot)["success"] is True


def test_empty_db_recovery_is_disabled_by_default(tmp_path, monkeypatch):
    calls = []

    async def task():
        calls.append("ran")
        return {"ok": True}

    from able.scheduler import cron as cron_module

    monkeypatch.delenv("ABLE_CRON_EMPTY_DB_RECOVERY_HOURS", raising=False)
    monkeypatch.setattr(
        cron_module,
        "_now",
        lambda: datetime(2026, 4, 24, 9, 0, tzinfo=timezone.utc),
    )

    scheduler = CronScheduler(db_path=str(tmp_path / "cron.db"))
    scheduler.add_job("nightly-research", "0 1 * * *", task, max_retries=1)

    _run(scheduler.recover_missed_jobs(max_lookback_hours=48))

    assert calls == []
    assert scheduler.db.count_records() == 0


def test_recovery_claim_uses_scheduled_slot_not_current_minute(tmp_path, monkeypatch):
    calls = []

    async def task():
        calls.append("ran")
        return {"ok": True}

    from able.scheduler import cron as cron_module

    fixed_now = datetime(2026, 4, 24, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(cron_module, "_now", lambda: fixed_now)

    db_path = tmp_path / "cron.db"
    scheduler = CronScheduler(db_path=str(db_path))
    scheduler.db.record_start(
        JobResult(
            id="seed",
            name="seed",
            started_at=fixed_now.timestamp() - 86400,
            success=True,
        )
    )
    scheduler.db.record_finish(
        JobResult(
            id="seed",
            name="seed",
            started_at=fixed_now.timestamp() - 86400,
            duration_s=0.1,
            success=True,
        )
    )
    scheduler.add_job("nightly-research", "0 1 * * *", task, max_retries=1)

    _run(scheduler.recover_missed_jobs(max_lookback_hours=48))
    first_calls = list(calls)
    _run(scheduler.recover_missed_jobs(max_lookback_hours=48))

    scheduled_slot = _slot(1)
    current_slot = _slot(9)
    assert first_calls == ["ran"]
    assert calls == ["ran"]
    assert scheduler.db.get_run_claim("nightly-research", scheduled_slot) is not None
    assert scheduler.db.get_run_claim("nightly-research", current_slot) is None


def test_stale_unfinished_claim_can_be_reclaimed(tmp_path):
    db = CronExecutionDB(str(tmp_path / "cron.db"))
    run_slot = _slot()

    assert db.try_claim_run(
        "nightly-research",
        run_slot,
        ttl_seconds=-1,
        execution_id="first",
        trigger="scheduled",
    )
    assert db.try_claim_run(
        "nightly-research",
        run_slot,
        ttl_seconds=300,
        execution_id="second",
        trigger="recovery",
    )

    claim = db.get_run_claim("nightly-research", run_slot)
    assert claim["execution_id"] == "second"
    assert claim["trigger"] == "recovery"
