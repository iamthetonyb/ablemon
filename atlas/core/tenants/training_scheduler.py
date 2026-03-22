"""
Training Scheduler — Schedules GPU training across tenants within budget.

Priority order:
1. Tony/ATLAS core (8h allocation)
2. New tenants (first training)
3. High-growth tenants (lots of new data)
4. Drifting tenants (quality degradation detected)
5. Scheduled retrains

At ~3h per first-train: ~3-4 new clients per month from 12h client budget.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)

# Priority levels (lower = higher priority)
PRIORITY_CORE = 0
PRIORITY_NEW_TENANT = 1
PRIORITY_HIGH_GROWTH = 2
PRIORITY_DRIFTING = 3
PRIORITY_SCHEDULED = 4

_PRIORITY_LABELS = {
    PRIORITY_CORE: "core",
    PRIORITY_NEW_TENANT: "new_tenant",
    PRIORITY_HIGH_GROWTH: "high_growth",
    PRIORITY_DRIFTING: "drifting",
    PRIORITY_SCHEDULED: "scheduled",
}


@dataclass
class TrainingJob:
    """A pending or completed training job."""

    tenant_id: str
    priority: int
    priority_label: str
    estimated_hours: float
    status: str = "pending"  # pending | scheduled | running | completed | failed
    scheduled_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GpuBudget:
    """GPU budget allocation."""

    total_monthly_hours: float = 20.0
    core_allocation_hours: float = 8.0
    client_allocation_hours: float = 12.0
    used_core_hours: float = 0.0
    used_client_hours: float = 0.0
    month: str = field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m"))

    @property
    def remaining_core_hours(self) -> float:
        return max(0, self.core_allocation_hours - self.used_core_hours)

    @property
    def remaining_client_hours(self) -> float:
        return max(0, self.client_allocation_hours - self.used_client_hours)

    @property
    def remaining_total_hours(self) -> float:
        return self.remaining_core_hours + self.remaining_client_hours

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["remaining_core_hours"] = self.remaining_core_hours
        d["remaining_client_hours"] = self.remaining_client_hours
        d["remaining_total_hours"] = self.remaining_total_hours
        return d


class TenantTrainingScheduler:
    """Schedules GPU training across tenants within budget."""

    FIRST_TRAIN_HOURS = 3.0  # ~3h per first training run
    RETRAIN_HOURS = 1.5  # ~1.5h for incremental retraining

    def __init__(self, gpu_budget_path: str | None = None):
        self.budget_path = Path(
            gpu_budget_path
            or os.path.expanduser("~/.atlas/tenants/gpu_budget.yaml")
        )
        self._queue: List[TrainingJob] = []
        self._budget = self._load_budget()

    def get_training_queue(self) -> List[Dict[str, Any]]:
        """Return prioritized list of tenants needing training."""
        self._queue.sort(key=lambda j: (j.priority, j.tenant_id))
        return [j.to_dict() for j in self._queue if j.status == "pending"]

    def schedule_training(
        self,
        tenant_id: str,
        priority: str = "scheduled",
        reason: str = "",
        is_first_train: bool = False,
    ) -> Dict[str, Any]:
        """Schedule training for a tenant. Returns estimated time/date."""
        priority_int = {
            "core": PRIORITY_CORE,
            "new_tenant": PRIORITY_NEW_TENANT,
            "high_growth": PRIORITY_HIGH_GROWTH,
            "drifting": PRIORITY_DRIFTING,
            "scheduled": PRIORITY_SCHEDULED,
        }.get(priority, PRIORITY_SCHEDULED)

        estimated_hours = self.FIRST_TRAIN_HOURS if is_first_train else self.RETRAIN_HOURS

        # Check budget
        is_core = (priority_int == PRIORITY_CORE)
        if is_core:
            available = self._budget.remaining_core_hours
        else:
            available = self._budget.remaining_client_hours

        if estimated_hours > available:
            return {
                "status": "rejected",
                "reason": (
                    f"Insufficient GPU budget: need {estimated_hours:.1f}h, "
                    f"have {available:.1f}h remaining"
                ),
                "tenant_id": tenant_id,
            }

        job = TrainingJob(
            tenant_id=tenant_id,
            priority=priority_int,
            priority_label=_PRIORITY_LABELS.get(priority_int, priority),
            estimated_hours=estimated_hours,
            status="scheduled",
            scheduled_at=datetime.utcnow().isoformat(),
            reason=reason or f"{priority} training for {tenant_id}",
        )

        self._queue.append(job)
        self._queue.sort(key=lambda j: (j.priority, j.scheduled_at or ""))

        logger.info(
            f"Training scheduled: {tenant_id} "
            f"priority={priority} hours={estimated_hours}"
        )

        return {
            "status": "scheduled",
            "tenant_id": tenant_id,
            "priority": priority,
            "estimated_hours": estimated_hours,
            "estimated_start": self.estimate_next_available().isoformat(),
            "queue_position": self._queue_position(tenant_id),
        }

    def estimate_next_available(self) -> datetime:
        """When is the next training slot available?

        Sums estimated hours of all pending/scheduled/running jobs.
        """
        busy_hours = sum(
            j.estimated_hours
            for j in self._queue
            if j.status in ("pending", "scheduled", "running")
        )
        return datetime.utcnow() + timedelta(hours=busy_hours)

    def complete_training(self, tenant_id: str, hours_used: float) -> None:
        """Mark a training job as completed and deduct from budget."""
        for job in self._queue:
            if job.tenant_id == tenant_id and job.status in ("scheduled", "running"):
                job.status = "completed"
                job.completed_at = datetime.utcnow().isoformat()
                break

        is_core = (tenant_id == "atlas-core")
        if is_core:
            self._budget.used_core_hours += hours_used
        else:
            self._budget.used_client_hours += hours_used

        self._save_budget()

    def get_budget(self) -> Dict[str, Any]:
        """Get current GPU budget status."""
        return self._budget.to_dict()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _queue_position(self, tenant_id: str) -> int:
        """Get the queue position for a tenant (1-based)."""
        pending = [
            j for j in self._queue
            if j.status in ("pending", "scheduled")
        ]
        for i, job in enumerate(pending):
            if job.tenant_id == tenant_id:
                return i + 1
        return len(pending)

    def _load_budget(self) -> GpuBudget:
        """Load GPU budget from YAML."""
        if not self.budget_path.exists():
            return GpuBudget()

        try:
            with open(self.budget_path) as f:
                data = yaml.safe_load(f) or {}

            budget = GpuBudget(
                total_monthly_hours=data.get("total_monthly_hours", 20.0),
                core_allocation_hours=data.get("core_allocation_hours", 8.0),
                client_allocation_hours=data.get("client_allocation_hours", 12.0),
                used_core_hours=data.get("used_core_hours", 0.0),
                used_client_hours=data.get("used_client_hours", 0.0),
                month=data.get("month", datetime.utcnow().strftime("%Y-%m")),
            )

            # Reset if new month
            current_month = datetime.utcnow().strftime("%Y-%m")
            if budget.month != current_month:
                budget.used_core_hours = 0.0
                budget.used_client_hours = 0.0
                budget.month = current_month

            return budget
        except Exception as exc:
            logger.warning(f"Failed to load GPU budget: {exc}")
            return GpuBudget()

    def _save_budget(self) -> None:
        """Save GPU budget to YAML."""
        self.budget_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.budget_path, "w") as f:
            yaml.dump(asdict(self._budget), f, default_flow_style=False)
