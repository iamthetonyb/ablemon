"""GPU budget tracker for H100 training sessions.

Tracks monthly usage with a safety buffer. Default allocation:
- 20 h/month total
- ~8 h reserved for core model training
- ~12 h for tenant fine-tuning
- 2.5 h buffer always held back

Persists state to a YAML file at ~/.atlas/gpu_budget.yaml.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import yaml


class GPUBudget:
    """Tracks H100 GPU hours. 20 h/month total, 2.5 h buffer."""

    def __init__(
        self,
        budget_path: str | None = None,
        monthly_hours: float = 20.0,
        buffer_hours: float = 2.5,
    ) -> None:
        self.budget_path = budget_path or os.path.expanduser(
            "~/.atlas/gpu_budget.yaml"
        )
        self.monthly_hours = monthly_hours
        self.buffer_hours = buffer_hours
        self._data = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_usage(
        self,
        hours: float,
        purpose: str,
        tenant_id: str = "default",
    ) -> None:
        """Record GPU hours consumed."""
        self._ensure_current_month()
        entry = {
            "hours": hours,
            "purpose": purpose,
            "tenant_id": tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._data["entries"].append(entry)
        self._save()

    def remaining(self) -> float:
        """Hours remaining this month (minus buffer)."""
        self._ensure_current_month()
        used = self._total_used()
        return max(0.0, self.monthly_hours - self.buffer_hours - used)

    def can_train(self, estimated_hours: float) -> bool:
        """Check whether budget allows a training run of the given length."""
        return estimated_hours <= self.remaining()

    def get_summary(self) -> dict[str, Any]:
        """Full budget summary: used, remaining, by tenant, by purpose."""
        self._ensure_current_month()
        used = self._total_used()
        by_tenant: dict[str, float] = {}
        by_purpose: dict[str, float] = {}
        for e in self._data["entries"]:
            tid = e.get("tenant_id", "default")
            by_tenant[tid] = by_tenant.get(tid, 0.0) + e["hours"]
            p = e.get("purpose", "unknown")
            by_purpose[p] = by_purpose.get(p, 0.0) + e["hours"]

        return {
            "month": self._data["month"],
            "monthly_hours": self.monthly_hours,
            "buffer_hours": self.buffer_hours,
            "used_hours": round(used, 2),
            "remaining_hours": round(self.remaining(), 2),
            "by_tenant": by_tenant,
            "by_purpose": by_purpose,
            "entry_count": len(self._data["entries"]),
        }

    def reset_monthly(self) -> None:
        """Reset for a new month. Moves current entries into history."""
        history_entry = {
            "month": self._data.get("month"),
            "used_hours": self._total_used(),
            "entry_count": len(self._data["entries"]),
        }
        if "history" not in self._data:
            self._data["history"] = []
        self._data["history"].append(history_entry)
        self._data["month"] = _current_month()
        self._data["entries"] = []
        self._save()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _total_used(self) -> float:
        return sum(e["hours"] for e in self._data.get("entries", []))

    def _ensure_current_month(self) -> None:
        """Auto-reset if the stored month is stale."""
        if self._data.get("month") != _current_month():
            self.reset_monthly()

    def _load(self) -> dict[str, Any]:
        if os.path.exists(self.budget_path):
            with open(self.budget_path) as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data
        return {"month": _current_month(), "entries": [], "history": []}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.budget_path), exist_ok=True)
        with open(self.budget_path, "w") as f:
            yaml.dump(self._data, f, default_flow_style=False, sort_keys=False)


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")
