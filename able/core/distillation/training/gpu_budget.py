"""GPU budget tracker for ABLE training pools.

Tracks pooled usage across:
- t4_colab: default 16 GB T4 / Colab training lane
- h100_session: premium 27B training lane
- local: on-device experiments and resumptions

State persists to ``~/.able/gpu_budget.yaml`` and migrates the earlier
single-pool H100 schema forward automatically.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import yaml


DEFAULT_POOLS = {
    "t4_colab": {
        "monthly_hours": 72.0,
        "buffer_hours": 8.0,
        "description": "Default T4 / 16 GB Colab lane for 9B training and resume cycles.",
    },
    "h100_session": {
        "monthly_hours": 20.0,
        "buffer_hours": 2.5,
        "description": "Premium H100 lane for 27B training and heavy validation.",
    },
    "local": {
        "monthly_hours": 999.0,
        "buffer_hours": 0.0,
        "description": "Local experiments and resume validation.",
    },
}


class GPUBudget:
    """Track monthly GPU usage across named pools."""

    def __init__(
        self,
        budget_path: str | None = None,
        monthly_hours: float = 20.0,
        buffer_hours: float = 2.5,
        default_pool: str = "h100_session",
    ) -> None:
        self.budget_path = budget_path or os.path.expanduser("~/.able/gpu_budget.yaml")
        self.default_pool = default_pool
        self._pool_defaults = {
            name: dict(config) for name, config in DEFAULT_POOLS.items()
        }
        self._pool_defaults[self.default_pool]["monthly_hours"] = monthly_hours
        self._pool_defaults[self.default_pool]["buffer_hours"] = buffer_hours
        self._data = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_usage(
        self,
        hours: float,
        purpose: str,
        tenant_id: str = "default",
        pool: str | None = None,
    ) -> None:
        """Record GPU hours consumed for the selected pool."""
        self._ensure_current_month()
        selected_pool = pool or self.default_pool
        self._ensure_pool(selected_pool)
        entry = {
            "hours": hours,
            "purpose": purpose,
            "tenant_id": tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._data["pools"][selected_pool]["entries"].append(entry)
        self._save()

    def remaining(self, pool: str | None = None) -> float:
        """Hours remaining this month for a pool, minus its buffer."""
        self._ensure_current_month()
        selected_pool = pool or self.default_pool
        self._ensure_pool(selected_pool)
        pool_data = self._data["pools"][selected_pool]
        used = self._total_used(selected_pool)
        return max(
            0.0,
            float(pool_data["monthly_hours"]) - float(pool_data["buffer_hours"]) - used,
        )

    def can_train(self, estimated_hours: float, pool: str | None = None) -> bool:
        """Check whether a pool has room for a run of the given length."""
        return estimated_hours <= self.remaining(pool=pool)

    def get_summary(self) -> dict[str, Any]:
        """Full budget summary, preserving top-level compatibility for the default pool."""
        self._ensure_current_month()

        pool_summaries: dict[str, Any] = {}
        for pool_name in self._data["pools"]:
            pool_summaries[pool_name] = self._pool_summary(pool_name)

        default_summary = pool_summaries[self.default_pool]
        return {
            "month": self._data["month"],
            "default_pool": self.default_pool,
            "monthly_hours": default_summary["monthly_hours"],
            "buffer_hours": default_summary["buffer_hours"],
            "used_hours": default_summary["used_hours"],
            "remaining_hours": default_summary["remaining_hours"],
            "by_tenant": default_summary["by_tenant"],
            "by_purpose": default_summary["by_purpose"],
            "entry_count": default_summary["entry_count"],
            "pools": pool_summaries,
        }

    def reset_monthly(self) -> None:
        """Roll current entries into history and reset for a new month."""
        history_entry = {
            "month": self._data.get("month"),
            "pools": {
                pool_name: self._pool_summary(pool_name)
                for pool_name in self._data["pools"]
            },
        }
        self._data.setdefault("history", []).append(history_entry)
        self._data["month"] = _current_month()
        for pool_name in self._data["pools"]:
            self._data["pools"][pool_name]["entries"] = []
        self._save()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _pool_summary(self, pool_name: str) -> dict[str, Any]:
        self._ensure_pool(pool_name)
        pool_data = self._data["pools"][pool_name]
        by_tenant: dict[str, float] = {}
        by_purpose: dict[str, float] = {}
        for entry in pool_data["entries"]:
            tenant_id = entry.get("tenant_id", "default")
            by_tenant[tenant_id] = by_tenant.get(tenant_id, 0.0) + entry["hours"]
            purpose = entry.get("purpose", "unknown")
            by_purpose[purpose] = by_purpose.get(purpose, 0.0) + entry["hours"]

        used = self._total_used(pool_name)
        remaining = max(
            0.0,
            float(pool_data["monthly_hours"]) - float(pool_data["buffer_hours"]) - used,
        )
        return {
            "monthly_hours": float(pool_data["monthly_hours"]),
            "buffer_hours": float(pool_data["buffer_hours"]),
            "used_hours": round(used, 2),
            "remaining_hours": round(remaining, 2),
            "by_tenant": by_tenant,
            "by_purpose": by_purpose,
            "entry_count": len(pool_data["entries"]),
            "description": pool_data.get("description", ""),
        }

    def _total_used(self, pool: str) -> float:
        self._ensure_pool(pool)
        return sum(entry["hours"] for entry in self._data["pools"][pool]["entries"])

    def _ensure_current_month(self) -> None:
        if self._data.get("month") != _current_month():
            self.reset_monthly()

    def _ensure_pool(self, pool_name: str) -> None:
        if pool_name in self._data["pools"]:
            return
        defaults = dict(self._pool_defaults.get(pool_name, {}))
        defaults.setdefault("monthly_hours", 0.0)
        defaults.setdefault("buffer_hours", 0.0)
        defaults.setdefault("description", "")
        defaults["entries"] = []
        self._data["pools"][pool_name] = defaults

    def _load(self) -> dict[str, Any]:
        if os.path.exists(self.budget_path):
            with open(self.budget_path) as handle:
                data = yaml.safe_load(handle)
            if isinstance(data, dict):
                migrated = self._migrate_schema(data)
                migrated.setdefault("history", [])
                migrated.setdefault("month", _current_month())
                migrated.setdefault("pools", {})
                for pool_name in self._pool_defaults:
                    self._ensure_pool_in_data(migrated, pool_name)
                return migrated

        fresh = {"month": _current_month(), "history": [], "pools": {}}
        for pool_name in self._pool_defaults:
            self._ensure_pool_in_data(fresh, pool_name)
        return fresh

    def _migrate_schema(self, data: dict[str, Any]) -> dict[str, Any]:
        if "pools" in data:
            for pool_name in self._pool_defaults:
                self._ensure_pool_in_data(data, pool_name)
            return data

        legacy_entries = list(data.get("entries", []))
        migrated = {
            "month": data.get("month", _current_month()),
            "history": data.get("history", []),
            "pools": {},
        }
        for pool_name in self._pool_defaults:
            self._ensure_pool_in_data(migrated, pool_name)
        migrated["pools"][self.default_pool]["entries"] = legacy_entries
        return migrated

    def _ensure_pool_in_data(self, data: dict[str, Any], pool_name: str) -> None:
        data.setdefault("pools", {})
        if pool_name in data["pools"]:
            pool = data["pools"][pool_name]
            pool.setdefault("entries", [])
            defaults = self._pool_defaults.get(pool_name, {})
            pool.setdefault("monthly_hours", defaults.get("monthly_hours", 0.0))
            pool.setdefault("buffer_hours", defaults.get("buffer_hours", 0.0))
            pool.setdefault("description", defaults.get("description", ""))
            return

        defaults = dict(self._pool_defaults.get(pool_name, {}))
        defaults.setdefault("monthly_hours", 0.0)
        defaults.setdefault("buffer_hours", 0.0)
        defaults.setdefault("description", "")
        defaults["entries"] = []
        data["pools"][pool_name] = defaults

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.budget_path), exist_ok=True)
        with open(self.budget_path, "w") as handle:
            yaml.dump(self._data, handle, default_flow_style=False, sort_keys=False)


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")
