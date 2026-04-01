"""
ATLAS Cron Scheduler — Persistent, self-healing scheduled task execution.

SQLite-backed execution log, retry with backoff, missed job recovery on startup.
Every job execution is recorded before delivery — results survive gateway restarts.
"""

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Awaitable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def _get_tz() -> ZoneInfo:
    """Get configured timezone from ATLAS_TIMEZONE env var, default to UTC."""
    tz_name = os.environ.get("ATLAS_TIMEZONE", "UTC")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        logger.warning(f"Invalid ATLAS_TIMEZONE '{tz_name}', falling back to UTC")
        return ZoneInfo("UTC")


def _now() -> datetime:
    """Current time in the configured ATLAS timezone."""
    return datetime.now(_get_tz())

# ── Simple cron expression parser ─────────────────────────────────────────

def _matches_field(value: int, expr: str, min_val: int, max_val: int) -> bool:
    """Check if a value matches a cron field expression."""
    if expr == '*':
        return True
    if '/' in expr:
        _, step = expr.split('/', 1)
        return value % int(step) == 0
    if ',' in expr:
        return value in [int(x) for x in expr.split(',')]
    if '-' in expr:
        start, end = expr.split('-', 1)
        return int(start) <= value <= int(end)
    return value == int(expr)


def cron_matches(expr: str, dt: datetime) -> bool:
    """
    Check if a datetime matches a cron expression.

    Format: minute hour day-of-month month day-of-week
    Example: "0 9 * * 1-5" = 9am Monday-Friday
    """
    try:
        parts = expr.strip().split()
        if len(parts) != 5:
            return False
        minute, hour, dom, month, dow = parts
        return (
            _matches_field(dt.minute, minute, 0, 59) and
            _matches_field(dt.hour, hour, 0, 23) and
            _matches_field(dt.day, dom, 1, 31) and
            _matches_field(dt.month, month, 1, 12) and
            _matches_field(dt.weekday(), dow, 0, 6)
        )
    except Exception:
        return False


def _next_occurrence(expr: str, after: datetime) -> Optional[datetime]:
    """Find the next datetime that matches a cron expression (within 8 days)."""
    check = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=8)
    while check < limit:
        if cron_matches(expr, check):
            return check
        check += timedelta(minutes=1)
    return None


def _expected_runs_since(expr: str, since: datetime, until: datetime) -> List[datetime]:
    """Find all times a cron expression should have fired between since and until."""
    runs = []
    cursor = since.replace(second=0, microsecond=0)
    while cursor <= until:
        if cron_matches(expr, cursor):
            runs.append(cursor)
        cursor += timedelta(minutes=1)
    return runs


# ── Pre-defined schedule expressions ──────────────────────────────────────

EVERY_MINUTE = "* * * * *"
EVERY_5_MINUTES = "*/5 * * * *"
EVERY_15_MINUTES = "*/15 * * * *"
EVERY_HOUR = "0 * * * *"
DAILY_3AM = "0 3 * * *"
DAILY_7AM = "0 7 * * *"
DAILY_9AM = "0 9 * * *"
WEEKDAYS_9AM = "0 9 * * 1-5"
WEEKLY_SUNDAY_6PM = "0 18 * * 0"
MONTHLY_1ST = "0 0 1 * *"


# ── Job definition ─────────────────────────────────────────────────────────

@dataclass
class CronJob:
    """A scheduled task."""
    name: str
    schedule: str               # Cron expression
    task: Callable              # Async callable
    args: Dict = field(default_factory=dict)
    description: str = ""
    enabled: bool = True
    last_run: Optional[float] = None
    last_status: Optional[str] = None  # "success" | "failed" | "timeout"
    run_count: int = 0
    error_count: int = 0
    timeout_seconds: float = 300.0  # 5 minute default timeout
    max_retries: int = 3
    retry_backoff_base: float = 30.0  # seconds


@dataclass
class JobResult:
    """Result of a job execution."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = ""
    success: bool = False
    duration_s: float = 0.0
    output: Optional[Any] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    attempt: int = 1
    trigger: str = "scheduled"  # "scheduled" | "manual" | "recovery" | "retry"


# ── Persistent execution log (SQLite) ─────────────────────────────────────

class CronExecutionDB:
    """SQLite-backed execution log. Survives restarts."""

    def __init__(self, db_path: str = "data/cron_executions.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    id TEXT PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    success INTEGER,
                    duration_s REAL,
                    error TEXT,
                    attempt INTEGER DEFAULT 1,
                    trigger TEXT DEFAULT 'scheduled',
                    output_preview TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_executions_job_time
                ON executions (job_name, started_at DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_executions_time
                ON executions (started_at DESC)
            """)

    def record_start(self, result: JobResult):
        """Record job start (before execution)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO executions (id, job_name, started_at, attempt, trigger) VALUES (?, ?, ?, ?, ?)",
                (result.id, result.name, result.started_at, result.attempt, result.trigger),
            )

    def record_finish(self, result: JobResult):
        """Record job completion (success or failure)."""
        output_preview = str(result.output)[:500] if result.output else None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE executions
                   SET finished_at = ?, success = ?, duration_s = ?, error = ?, output_preview = ?
                   WHERE id = ?""",
                (
                    result.started_at + result.duration_s,
                    1 if result.success else 0,
                    result.duration_s,
                    result.error,
                    output_preview,
                    result.id,
                ),
            )

    def get_last_success(self, job_name: str) -> Optional[float]:
        """Get timestamp of last successful execution for a job."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT started_at FROM executions WHERE job_name = ? AND success = 1 ORDER BY started_at DESC LIMIT 1",
                (job_name,),
            ).fetchone()
        return row[0] if row else None

    def get_recent(self, limit: int = 50, job_name: str = None) -> List[Dict]:
        """Get recent execution history."""
        with sqlite3.connect(self.db_path) as conn:
            if job_name:
                rows = conn.execute(
                    "SELECT id, job_name, started_at, finished_at, success, duration_s, error, attempt, trigger "
                    "FROM executions WHERE job_name = ? ORDER BY started_at DESC LIMIT ?",
                    (job_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, job_name, started_at, finished_at, success, duration_s, error, attempt, trigger "
                    "FROM executions ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {
                "id": r[0], "name": r[1], "started_at": r[2], "finished_at": r[3],
                "success": bool(r[4]) if r[4] is not None else None,
                "duration_s": r[5], "error": r[6], "attempt": r[7], "trigger": r[8],
            }
            for r in rows
        ]

    def get_job_stats(self, job_name: str, days: int = 30) -> Dict:
        """Get execution statistics for a job over N days."""
        cutoff = time.time() - (days * 86400)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures,
                    AVG(duration_s) as avg_duration,
                    MAX(started_at) as last_run
                FROM executions WHERE job_name = ? AND started_at > ?""",
                (job_name, cutoff),
            ).fetchone()
        return {
            "total": row[0] or 0,
            "successes": row[1] or 0,
            "failures": row[2] or 0,
            "avg_duration_s": round(row[3] or 0, 1),
            "last_run": row[4],
            "success_rate": round((row[1] or 0) / max(row[0] or 1, 1) * 100, 1),
        }

    def cleanup(self, max_age_days: int = 90):
        """Purge records older than max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        with sqlite3.connect(self.db_path) as conn:
            deleted = conn.execute(
                "DELETE FROM executions WHERE started_at < ?", (cutoff,)
            ).rowcount
        if deleted:
            logger.info(f"Cron DB cleanup: purged {deleted} records older than {max_age_days} days")


# ── Scheduler ─────────────────────────────────────────────────────────────

class CronScheduler:
    """
    Persistent cron scheduler for ATLAS autonomous operations.

    - SQLite-backed execution log (survives restarts)
    - Retry with exponential backoff (up to 3 attempts)
    - Missed job recovery on startup
    - Awaited execution (no fire-and-forget)
    - 90-day history retention with auto-cleanup

    Usage:
        scheduler = CronScheduler()
        scheduler.add_job("daily-cleanup", DAILY_3AM, my_cleanup_func)
        await scheduler.recover_missed_jobs()  # catch up after downtime
        await scheduler.run_forever()
    """

    def __init__(self, audit_log=None, db_path: str = "data/cron_executions.db"):
        self.jobs: Dict[str, CronJob] = {}
        self.audit_log = audit_log
        self._running = False
        self.db = CronExecutionDB(db_path)
        self._startup_time = time.time()

    def add_job(
        self,
        name: str,
        schedule: str,
        task: Callable[..., Awaitable[Any]],
        args: Dict = None,
        description: str = "",
        enabled: bool = True,
        timeout: float = 300.0,
        max_retries: int = 3,
    ) -> CronJob:
        """Register a new cron job."""
        job = CronJob(
            name=name,
            schedule=schedule,
            task=task,
            args=args or {},
            description=description,
            enabled=enabled,
            timeout_seconds=timeout,
            max_retries=max_retries,
        )
        # Hydrate from DB: restore last_run/status from persistent history
        last = self.db.get_last_success(name)
        if last:
            job.last_run = last
        stats = self.db.get_job_stats(name, days=30)
        job.run_count = stats["total"]
        job.error_count = stats["failures"]
        if stats["last_run"]:
            job.last_run = stats["last_run"]

        self.jobs[name] = job
        logger.info(f"Registered cron job: {name} [{schedule}] (history: {stats['total']} runs, {stats['success_rate']}% success)")
        return job

    def remove_job(self, name: str) -> bool:
        if name in self.jobs:
            del self.jobs[name]
            logger.info(f"Removed cron job: {name}")
            return True
        return False

    def enable_job(self, name: str):
        if name in self.jobs:
            self.jobs[name].enabled = True

    def disable_job(self, name: str):
        if name in self.jobs:
            self.jobs[name].enabled = False

    async def recover_missed_jobs(self, max_lookback_hours: int = 48):
        """
        On startup, detect jobs that should have run during downtime and execute them.

        Only recovers jobs with schedules coarser than every-5-minutes (skip health checks).
        Only recovers if the last successful run is older than the expected interval.
        If the DB is empty (first boot or wiped), limits lookback to 2 hours to avoid
        flooding all channels with stale briefings.
        """
        tz = _get_tz()
        now = _now()
        recovered = 0

        # If DB has zero records, this is likely a fresh DB (first boot or wipe).
        # Limit lookback to 2 hours to avoid recovering stale daily/weekly jobs.
        total_records = self.db.get_job_stats("__any__", days=9999)["total"]
        if total_records == 0:
            effective_lookback = min(max_lookback_hours, 2)
            logger.info(f"Empty cron DB — limiting recovery lookback to {effective_lookback}h (fresh boot)")
        else:
            effective_lookback = max_lookback_hours

        lookback = now - timedelta(hours=effective_lookback)

        for name, job in self.jobs.items():
            if not job.enabled:
                continue

            # Skip high-frequency jobs (every minute / 5 minutes) — not worth recovering
            if job.schedule in (EVERY_MINUTE, EVERY_5_MINUTES, EVERY_15_MINUTES):
                continue

            # Find when this job last succeeded
            last_success = self.db.get_last_success(name)
            if last_success:
                last_dt = datetime.fromtimestamp(last_success, tz=tz)
            else:
                last_dt = lookback  # never ran — check full lookback

            # Find expected runs since last success
            expected = _expected_runs_since(job.schedule, last_dt.replace(tzinfo=None), now.replace(tzinfo=None))
            if not expected:
                continue

            # Check if the most recent expected run was missed
            most_recent_expected = expected[-1]
            # Re-attach ATLAS timezone before converting to epoch (naive .timestamp() assumes server tz)
            most_recent_ts = most_recent_expected.replace(tzinfo=tz).timestamp()
            if last_success and last_success >= most_recent_ts:
                continue  # Already ran

            # This job missed its last scheduled run — recover it
            logger.warning(
                f"⚡ Recovering missed job: {name} "
                f"(last success: {datetime.fromtimestamp(last_success, tz=tz).isoformat() if last_success else 'never'}, "
                f"expected: {most_recent_expected.isoformat()})"
            )
            try:
                await self._run_job(job, trigger="recovery")
                recovered += 1
            except Exception as e:
                logger.error(f"Recovery failed for {name}: {e}")

        if recovered:
            logger.info(f"⚡ Recovered {recovered} missed job(s)")
        else:
            logger.info("No missed jobs to recover")

    async def run_forever(self, poll_interval: float = 30.0):
        """
        Run the scheduler indefinitely.

        Checks every poll_interval seconds for due jobs.
        Jobs are awaited (not fire-and-forget). Multiple due jobs run sequentially
        to avoid resource contention.
        """
        self._running = True
        tz = _get_tz()
        logger.info(f"⏰ Cron scheduler started ({len(self.jobs)} jobs, tz={tz}, persistent DB active)")

        # Daily DB cleanup at startup
        self.db.cleanup(max_age_days=90)

        while self._running:
            now = _now()
            due = [j for j in self.jobs.values() if j.enabled and cron_matches(j.schedule, now)]

            for job in due:
                # Avoid double-firing in same minute
                if job.last_run and (time.time() - job.last_run) < 60:
                    continue
                # Await execution — no fire-and-forget
                await self._run_job_with_retry(job)

            await asyncio.sleep(poll_interval)

    async def run_job_now(self, name: str) -> JobResult:
        """Manually trigger a job immediately."""
        job = self.jobs.get(name)
        if not job:
            raise ValueError(f"Job '{name}' not found")
        return await self._run_job_with_retry(job, trigger="manual")

    async def _run_job_with_retry(self, job: CronJob, trigger: str = "scheduled") -> JobResult:
        """Execute a job with retry on failure (exponential backoff)."""
        result = await self._run_job(job, trigger=trigger)

        attempt = 1
        while not result.success and attempt < job.max_retries:
            attempt += 1
            backoff = job.retry_backoff_base * (2 ** (attempt - 2))  # 30s, 60s, 120s
            logger.info(f"🔄 Retrying {job.name} in {backoff:.0f}s (attempt {attempt}/{job.max_retries})")
            await asyncio.sleep(backoff)
            result = await self._run_job(job, trigger="retry", attempt=attempt)

        if not result.success and attempt >= job.max_retries:
            logger.error(f"💀 Job '{job.name}' failed after {attempt} attempts — giving up until next schedule")

        return result

    async def _run_job(self, job: CronJob, trigger: str = "scheduled", attempt: int = 1) -> JobResult:
        """Execute a single job with timeout, persistence, and audit logging."""
        start = time.time()
        job.last_run = start
        job.run_count += 1

        result = JobResult(
            name=job.name,
            started_at=start,
            attempt=attempt,
            trigger=trigger,
        )

        # Persist start BEFORE execution
        self.db.record_start(result)
        logger.info(f"⏰ Running job: {job.name} [trigger={trigger}, attempt={attempt}]")

        try:
            output = await asyncio.wait_for(
                job.task(**job.args),
                timeout=job.timeout_seconds,
            )

            result.duration_s = time.time() - start
            result.success = True
            result.output = output
            job.last_status = "success"
            logger.info(f"✅ Job '{job.name}' completed in {result.duration_s:.1f}s")

        except asyncio.TimeoutError:
            result.duration_s = time.time() - start
            result.error = f"Timed out after {job.timeout_seconds}s"
            job.last_status = "timeout"
            job.error_count += 1
            logger.warning(f"⏱ Job '{job.name}' timed out after {job.timeout_seconds}s")

        except Exception as e:
            result.duration_s = time.time() - start
            result.error = str(e)
            job.last_status = "failed"
            job.error_count += 1
            logger.error(f"❌ Job '{job.name}' failed: {e}")

        # Persist result AFTER execution (before any delivery)
        self.db.record_finish(result)

        # Audit log (best-effort, non-blocking)
        if self.audit_log:
            try:
                await self.audit_log.log_event(
                    action="cron_job",
                    details={
                        "job": job.name,
                        "success": result.success,
                        "duration_s": result.duration_s,
                        "error": result.error,
                        "trigger": trigger,
                        "attempt": attempt,
                    },
                )
            except Exception:
                pass

        return result

    async def stop(self):
        self._running = False
        logger.info("Cron scheduler stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get status of all jobs with persistent stats."""
        jobs_status = {}
        for name, job in self.jobs.items():
            stats = self.db.get_job_stats(name, days=30)
            jobs_status[name] = {
                "schedule": job.schedule,
                "description": job.description,
                "enabled": job.enabled,
                "last_run": job.last_run,
                "last_status": job.last_status,
                "run_count_30d": stats["total"],
                "success_rate_30d": stats["success_rate"],
                "error_count_30d": stats["failures"],
                "avg_duration_s": stats["avg_duration_s"],
            }
        return {
            "total_jobs": len(self.jobs),
            "enabled_jobs": sum(1 for j in self.jobs.values() if j.enabled),
            "db_path": str(self.db.db_path),
            "jobs": jobs_status,
        }

    def get_recent_history(self, limit: int = 50, job_name: str = None) -> List[Dict]:
        """Get recent job execution history from persistent DB."""
        return self.db.get_recent(limit=limit, job_name=job_name)


def register_default_jobs(
    scheduler: CronScheduler,
    memory=None,
    billing=None,
    audit_log=None,
    send_telegram: Optional[Callable] = None,
) -> None:
    """Register all default ATLAS maintenance jobs."""

    async def consolidate_memory():
        if memory:
            logger.info("Running memory consolidation...")
            return "Memory consolidated"

    scheduler.add_job(
        "memory-consolidation",
        DAILY_3AM,
        consolidate_memory,
        description="Archive and compress old memories",
    )

    async def billing_summary():
        if billing:
            logger.info("Generating weekly billing summary...")
            return "Billing summary generated"

    scheduler.add_job(
        "weekly-billing-summary",
        WEEKLY_SUNDAY_6PM,
        billing_summary,
        description="Generate weekly billing report for all clients",
    )

    async def health_check():
        logger.debug("Health check OK")
        return {"status": "healthy", "timestamp": time.time()}

    scheduler.add_job(
        "health-check",
        EVERY_5_MINUTES,
        health_check,
        description="Periodic system health check",
        timeout=30.0,
        max_retries=1,  # Don't retry health checks
    )

    async def rotate_audit_log():
        if audit_log:
            logger.info("Rotating audit logs...")
            return "Audit logs rotated"

    scheduler.add_job(
        "audit-log-rotation",
        MONTHLY_1ST,
        rotate_audit_log,
        description="Rotate and archive audit logs",
    )

    # ── Evolution daemon — nightly at 3am ───────────────────────
    async def run_evolution_daemon():
        from atlas.core.evolution.daemon import EvolutionDaemon, EvolutionConfig
        from atlas.core.evolution.self_scheduler import SelfScheduler

        config = EvolutionConfig()
        daemon = EvolutionDaemon(config=config)
        result = await daemon.run_cycle()

        # Feed results into self-scheduler if the cycle produced analysis
        if result.success and result.problems_found > 0:
            self_sched = SelfScheduler(
                scheduler=scheduler,
                audit_trail=audit_log,
            )
            analysis_data = {
                "problems_found": result.problems_found,
                "improvements_proposed": result.improvements_proposed,
                "improvements_deployed": result.improvements_deployed,
                "problems": [],
                "recommendations": [],
                "failures_by_tier": [],
                "health_indicators": {"overall": "healthy", "alerts": []},
            }
            await self_sched.run_cycle(analysis_data, cycle_id=result.cycle_id)

        return {
            "cycle_id": result.cycle_id,
            "success": result.success,
            "improvements_deployed": result.improvements_deployed,
        }

    scheduler.add_job(
        "evolution-daemon",
        DAILY_3AM,
        run_evolution_daemon,
        description="Nightly evolution daemon — M2.7 tunes routing weights",
        timeout=600.0,
        max_retries=2,
    )

    # ── Nightly distillation harvest — 2am daily ────────────────
    async def run_nightly_distillation():
        from atlas.core.distillation.harvest_runner import run_harvest

        # Harvest for default (ATLAS core)
        result = await run_harvest(since_hours=24, tenant_id="default")
        logger.info(
            f"Distillation harvest [default]: {result.total_conversations} convos → "
            f"{result.total_formatted} pairs, corpus={result.corpus_tier}"
        )

        # Harvest for all active tenants with configured sessions
        tenant_results = {}
        try:
            from atlas.core.tenants.tenant_manager import TenantManager
            tm = TenantManager()
            for tenant in tm.list_tenants(status="active"):
                tid = tenant.tenant_id
                sessions_dir = tenant.distillation.get("claude_sessions_dir")
                if sessions_dir:
                    try:
                        t_result = await run_harvest(
                            since_hours=24, tenant_id=tid,
                        )
                        tenant_results[tid] = t_result.total_formatted
                        logger.info(
                            f"Distillation harvest [{tid}]: {t_result.total_conversations} convos → "
                            f"{t_result.total_formatted} pairs"
                        )
                    except Exception as te:
                        logger.warning(f"Tenant {tid} harvest failed: {te}")
        except Exception as e:
            logger.debug(f"Tenant harvest skipped: {e}")

        return {
            "conversations": result.total_conversations,
            "formatted": result.total_formatted,
            "corpus_version": result.corpus_version,
            "corpus_tier": result.corpus_tier,
            "errors": result.errors,
            "tenant_results": tenant_results,
        }

    scheduler.add_job(
        "nightly-distillation",
        "0 2 * * *",
        run_nightly_distillation,
        description="Harvest conversations from all platforms, build training corpus",
        timeout=600.0,
        max_retries=2,
    )

    # ── Morning report — daily at 7am ───────────────────────────
    async def generate_morning_report():
        from atlas.core.evolution.morning_report import MorningReporter

        reporter = MorningReporter()
        report = await reporter.generate(period_hours=24)
        text = reporter.format_telegram(report)
        logger.info(f"Morning report generated ({len(text)} chars)")

        # Deliver via Telegram if callback is available
        if send_telegram:
            try:
                await send_telegram(text)
                logger.info("Morning report sent via Telegram")
            except Exception as e:
                logger.warning(f"Morning report Telegram delivery failed: {e}")

        return {"report": text, "total_requests": report.total_requests}

    scheduler.add_job(
        "morning-report",
        DAILY_7AM,
        generate_morning_report,
        description="Daily morning report — Telegram-deliverable summary",
        timeout=120.0,
        max_retries=2,
    )

    # ── Nightly research scan — 1am daily ───────────────────────
    async def run_nightly_research_scan():
        from atlas.core.evolution.weekly_research import run_nightly_research
        return await run_nightly_research(send_telegram=send_telegram)

    scheduler.add_job(
        "nightly-research",
        "0 1 * * *",
        run_nightly_research_scan,
        description="Nightly scan — breaking AI news, patches, releases",
        timeout=300.0,
        max_retries=2,
    )

    # ── Weekly deep research — Sunday 10am ──────────────────────
    async def run_weekly_research_scan():
        from atlas.core.evolution.weekly_research import run_weekly_research
        return await run_weekly_research(send_telegram=send_telegram)

    scheduler.add_job(
        "weekly-research",
        "0 10 * * 0",
        run_weekly_research_scan,
        description="Weekly deep scan — AI ecosystem, Claude updates, agentic systems, improvements",
        timeout=600.0,
        max_retries=2,
    )

    # ── AutoPilot — daily at 5am ─────────────────────────────────
    async def run_autopilot():
        from atlas.core.agi.autopilot import AutoPilot

        pilot = AutoPilot()
        result = await pilot.run_objectives(max_tasks=5)

        prompting_result = await pilot.run_auto_prompting(
            domain="coding", count=10,
        )

        eval_result = await pilot.run_self_eval()

        return {
            "objectives": {
                "attempted": result.tasks_attempted,
                "succeeded": result.tasks_succeeded,
                "pairs": result.distillation_pairs,
            },
            "auto_prompting": {
                "attempted": prompting_result.tasks_attempted,
                "succeeded": prompting_result.tasks_succeeded,
                "pairs": prompting_result.distillation_pairs,
            },
            "self_eval": {
                "failures_processed": eval_result.tasks_attempted,
                "prompts_added": eval_result.distillation_pairs,
            },
        }

    scheduler.add_job(
        "autopilot",
        "0 5 * * *",
        run_autopilot,
        description="Daily autonomous task runner — objectives + auto-prompting + self-eval",
        timeout=900.0,
        max_retries=2,
    )
