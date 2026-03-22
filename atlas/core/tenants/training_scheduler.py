"""
Tenant Training Scheduler — GPU allocation across tenants within budget.

Priority: core -> new tenants -> high-growth -> drifting -> retrain.

At ~3h per first-train: ~3-4 new clients per month from 12h client budget.
Batches similar-domain tenants. Staggers retrains to avoid GPU contention.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._helpers import count_corpus_files, has_adapter, load_tenant_config

logger = logging.getLogger(__name__)


@dataclass
class TrainingJob:
    """A scheduled GPU training job."""

    tenant_id: str
    domain: str
    priority: int  # 1=highest (core), 5=lowest (retrain)
    priority_label: str = ""
    estimated_hours: float = 3.0
    corpus_size: int = 0
    status: str = "pending"  # pending, scheduled, running, completed, failed
    scheduled_at: Optional[str] = None
    completed_at: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "domain": self.domain,
            "priority": self.priority,
            "priority_label": self.priority_label,
            "estimated_hours": self.estimated_hours,
            "corpus_size": self.corpus_size,
            "status": self.status,
            "scheduled_at": self.scheduled_at,
            "completed_at": self.completed_at,
            "reason": self.reason,
        }


PRIORITY_CORE = 1
PRIORITY_NEW = 2
PRIORITY_HIGH_GROWTH = 3
PRIORITY_DRIFTING = 4
PRIORITY_RETRAIN = 5

PRIORITY_LABELS = {
    PRIORITY_CORE: "core",
    PRIORITY_NEW: "new_tenant",
    PRIORITY_HIGH_GROWTH: "high_growth",
    PRIORITY_DRIFTING: "drifting",
    PRIORITY_RETRAIN: "retrain",
}

BASE_TRAINING_HOURS = 3.0
HOURS_PER_500_DOCS = 0.5


class TenantTrainingScheduler:
    """Schedules GPU training across tenants within budget.

    Priority ordering:
      1. core (ATLAS itself)
      2. new_tenant (first-time training, onboarding SLA)
      3. high_growth (>50% corpus growth since last train)
      4. drifting (quality scores declining)
      5. retrain (scheduled periodic refresh)

    Budget: ~12h GPU/month for client work.
    At ~3h per first-train: ~3-4 new clients per month.
    Batches similar-domain tenants to amortize setup overhead.
    """

    def __init__(
        self,
        config_dir: str = "config/tenants",
        data_dir: Optional[Path] = None,
        monthly_gpu_budget_hours: float = 12.0,
    ):
        self.config_dir = Path(config_dir)
        self.data_dir = data_dir or (Path.home() / ".atlas" / "tenants")
        self.monthly_budget = monthly_gpu_budget_hours
        self._queue: List[TrainingJob] = []

    def _estimate_hours(self, corpus_size: int) -> float:
        """Estimate training time based on corpus size."""
        if corpus_size <= 500:
            return BASE_TRAINING_HOURS
        extra_blocks = (corpus_size - 500) / 500
        return BASE_TRAINING_HOURS + (extra_blocks * HOURS_PER_500_DOCS)

    def _classify_priority(
        self, tenant_id: str, corpus_size: int, adapter_exists: bool,
        config: Optional[Dict[str, Any]],
    ) -> tuple[int, str]:
        """Determine training priority for a tenant.

        Returns (priority_number, reason).
        """
        if tenant_id in ("core", "atlas"):
            return PRIORITY_CORE, "Core ATLAS training"

        if not config:
            return PRIORITY_RETRAIN, "Config not found, scheduling retrain"

        threshold = config.get("distillation", {}).get("training_threshold", 500)

        if not adapter_exists:
            if corpus_size >= threshold:
                return PRIORITY_NEW, f"New tenant with {corpus_size} corpus files (threshold: {threshold})"
            return PRIORITY_RETRAIN, f"Corpus below threshold ({corpus_size}/{threshold})"

        if corpus_size > threshold * 1.5:
            return PRIORITY_HIGH_GROWTH, f"Corpus growth: {corpus_size} files (50%+ above threshold)"

        return PRIORITY_RETRAIN, f"Scheduled retrain ({corpus_size} corpus files)"

    async def evaluate_tenant(self, tenant_id: str) -> TrainingJob:
        """Evaluate a single tenant for training priority."""
        config = load_tenant_config(self.config_dir, tenant_id)
        domain = config.get("domain", "general") if config else "general"
        corpus_size = count_corpus_files(self.data_dir, tenant_id)
        adapter_exists = has_adapter(self.data_dir, tenant_id)
        priority, reason = self._classify_priority(
            tenant_id, corpus_size, adapter_exists, config,
        )

        return TrainingJob(
            tenant_id=tenant_id,
            domain=domain,
            priority=priority,
            priority_label=PRIORITY_LABELS.get(priority, "unknown"),
            estimated_hours=self._estimate_hours(corpus_size),
            corpus_size=corpus_size,
            reason=reason,
        )

    async def build_schedule(
        self, tenant_ids: List[str], gpu_hours_available: Optional[float] = None
    ) -> Dict[str, Any]:
        """Build a training schedule for multiple tenants.

        Sorts by priority, batches by domain, respects GPU budget.
        Returns schedule dict with jobs and budget summary.
        """
        budget = gpu_hours_available or self.monthly_budget

        jobs = []
        for tid in tenant_ids:
            job = await self.evaluate_tenant(tid)
            jobs.append(job)

        jobs.sort(key=lambda j: (j.priority, -j.corpus_size))

        domain_groups: Dict[str, List[TrainingJob]] = {}
        for job in jobs:
            domain_groups.setdefault(job.domain, []).append(job)

        scheduled = []
        deferred = []
        hours_used = 0.0

        for job in jobs:
            if hours_used + job.estimated_hours <= budget:
                job.status = "scheduled"
                job.scheduled_at = datetime.now(timezone.utc).isoformat()
                scheduled.append(job)
                hours_used += job.estimated_hours
            else:
                job.status = "deferred"
                job.reason += f" (budget exhausted: {hours_used:.1f}/{budget:.1f}h)"
                deferred.append(job)

        self._queue = scheduled

        return {
            "scheduled": [j.to_dict() for j in scheduled],
            "deferred": [j.to_dict() for j in deferred],
            "budget": {
                "total_hours": budget,
                "hours_allocated": hours_used,
                "hours_remaining": budget - hours_used,
            },
            "domain_batches": {
                domain: [j.tenant_id for j in group]
                for domain, group in domain_groups.items()
            },
        }

    async def trigger_training(self, tenant_id: str) -> TrainingJob:
        """Manually trigger training for a specific tenant.

        Evaluates priority and returns a training job ready for execution.
        """
        job = await self.evaluate_tenant(tenant_id)
        config = load_tenant_config(self.config_dir, tenant_id)

        if config:
            threshold = config.get("distillation", {}).get("training_threshold", 500)
            if job.corpus_size < threshold:
                job.status = "blocked"
                job.reason = (
                    f"Corpus size {job.corpus_size} below threshold {threshold}. "
                    f"Need {threshold - job.corpus_size} more files."
                )
                return job

        job.status = "scheduled"
        job.scheduled_at = datetime.now(timezone.utc).isoformat()
        self._queue.append(job)

        logger.info(
            f"[TRAINING_TRIGGER] {tenant_id} | priority={job.priority_label} | "
            f"est_hours={job.estimated_hours:.1f}"
        )
        return job

    def get_queue(self) -> List[Dict[str, Any]]:
        """Get current training queue."""
        return [j.to_dict() for j in self._queue]
