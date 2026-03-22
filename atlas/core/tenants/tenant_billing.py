"""
Tenant Billing — Per-tenant cost tracking with markup and ROI calculation.

Tracks:
- API costs per tier (with configurable markup)
- GPU training hours (included vs overage)
- Self-hosted Tier 0 at $0
- Monthly invoice with ROI: 'Your adapter saved $X this month'

Uses JSONL for append-only usage logs (one per tenant) and SQLite for
aggregated billing summaries.
"""

import json
import logging
import sqlite3

import yaml
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/tenant_billing.db"

# Provider costs ($ per million tokens) — mirrors BillingTracker.DEFAULT_COSTS
PROVIDER_COSTS = {
    "gpt-5.4-mini": {"input": 0.0, "output": 0.0},
    "gpt-5.4": {"input": 0.0, "output": 0.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "ollama-tenant": {"input": 0.0, "output": 0.0},
    "ollama-local": {"input": 0.0, "output": 0.0},
    "mimo-v2-pro": {"input": 1.0, "output": 3.0},
    "nemotron-120b-nim": {"input": 0.30, "output": 0.80},
}

# Default GPU training cost per hour
GPU_COST_PER_HOUR = 3.50  # H100 rate


@dataclass
class TenantUsageRecord:
    """A single usage event for a tenant."""

    tenant_id: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    tier: int = 1
    provider: str = ""
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw_cost: float = 0.0  # Actual provider cost
    billed_cost: float = 0.0  # Cost with markup applied
    tier_0_saved: float = 0.0  # Would-have-cost if not using adapter

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TenantBilling:
    """Per-tenant cost tracking with markup, GPU hours, and ROI.

    Each tenant gets:
    - An append-only JSONL usage log
    - Aggregated billing in SQLite
    - GPU training hour tracking
    - Monthly ROI calculation showing adapter savings
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS tenant_billing (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id TEXT NOT NULL,
        month TEXT NOT NULL,
        total_raw_cost REAL DEFAULT 0.0,
        total_billed_cost REAL DEFAULT 0.0,
        total_tier_0_saved REAL DEFAULT 0.0,
        total_input_tokens INTEGER DEFAULT 0,
        total_output_tokens INTEGER DEFAULT 0,
        gpu_hours_used REAL DEFAULT 0.0,
        gpu_hours_included REAL DEFAULT 3.0,
        gpu_overage_cost REAL DEFAULT 0.0,
        request_count INTEGER DEFAULT 0,
        tier_0_request_count INTEGER DEFAULT 0,
        UNIQUE(tenant_id, month)
    );
    CREATE INDEX IF NOT EXISTS idx_tb_tenant ON tenant_billing(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_tb_month ON tenant_billing(month);
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        log_dir: Optional[Path] = None,
    ):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._log_dir = log_dir or Path("data/tenant_logs")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript(self.SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_markup(self, tenant_id: str, config_dir: str = "config/tenants") -> float:
        """Load tenant markup percentage from config."""
        config_path = Path(config_dir) / f"{tenant_id}.yaml"
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            return config.get("billing", {}).get("markup_percentage", 40) / 100.0
        return 0.40  # Default 40%

    def _calculate_cost(
        self, provider: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Calculate raw provider cost."""
        costs = PROVIDER_COSTS.get(provider, {"input": 1.0, "output": 5.0})
        return (
            (input_tokens / 1_000_000) * costs["input"]
            + (output_tokens / 1_000_000) * costs["output"]
        )

    def _estimate_tier_0_savings(
        self, input_tokens: int, output_tokens: int
    ) -> float:
        """Estimate what Tier 1 would have cost for this request."""
        # Tier 1 costs $0 (subscription), so estimate at Tier 2 fallback rate
        costs = PROVIDER_COSTS.get("mimo-v2-pro", {"input": 1.0, "output": 3.0})
        return (
            (input_tokens / 1_000_000) * costs["input"]
            + (output_tokens / 1_000_000) * costs["output"]
        )

    async def track_usage(
        self,
        tenant_id: str,
        tier: int,
        provider: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> TenantUsageRecord:
        """Track a single usage event for a tenant.

        Appends to JSONL log and updates monthly aggregate.
        """
        raw_cost = self._calculate_cost(provider, input_tokens, output_tokens)
        markup = self._get_markup(tenant_id)
        billed_cost = raw_cost * (1 + markup)

        tier_0_saved = 0.0
        if tier == 0:
            # Tier 0 is free; savings = what it would have cost otherwise
            tier_0_saved = self._estimate_tier_0_savings(input_tokens, output_tokens)
            billed_cost = 0.0

        record = TenantUsageRecord(
            tenant_id=tenant_id,
            tier=tier,
            provider=provider,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_cost=raw_cost,
            billed_cost=billed_cost,
            tier_0_saved=tier_0_saved,
        )

        # Append to JSONL
        log_path = self._log_dir / f"{tenant_id}.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(record.to_dict()) + "\n")

        # Update monthly aggregate
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        self._update_monthly(
            tenant_id, month, raw_cost, billed_cost, tier_0_saved,
            input_tokens, output_tokens, is_tier_0=(tier == 0),
        )

        logger.debug(
            f"[TENANT_BILLING] {tenant_id} | tier={tier} | "
            f"raw=${raw_cost:.4f} billed=${billed_cost:.4f}"
        )
        return record

    def _update_monthly(
        self,
        tenant_id: str,
        month: str,
        raw_cost: float,
        billed_cost: float,
        tier_0_saved: float,
        input_tokens: int,
        output_tokens: int,
        is_tier_0: bool,
    ):
        """Upsert monthly billing aggregate."""
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO tenant_billing
                   (tenant_id, month, total_raw_cost, total_billed_cost,
                    total_tier_0_saved, total_input_tokens, total_output_tokens,
                    request_count, tier_0_request_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(tenant_id, month) DO UPDATE SET
                    total_raw_cost = total_raw_cost + ?,
                    total_billed_cost = total_billed_cost + ?,
                    total_tier_0_saved = total_tier_0_saved + ?,
                    total_input_tokens = total_input_tokens + ?,
                    total_output_tokens = total_output_tokens + ?,
                    request_count = request_count + 1,
                    tier_0_request_count = tier_0_request_count + ?""",
                (
                    tenant_id, month, raw_cost, billed_cost, tier_0_saved,
                    input_tokens, output_tokens, int(is_tier_0),
                    raw_cost, billed_cost, tier_0_saved,
                    input_tokens, output_tokens, int(is_tier_0),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def record_gpu_hours(
        self,
        tenant_id: str,
        hours: float,
        month: Optional[str] = None,
        gpu_hours_included: float = 3.0,
    ) -> Dict[str, Any]:
        """Record GPU training hours used by a tenant."""
        month = month or datetime.now(timezone.utc).strftime("%Y-%m")

        conn = self._connect()
        try:
            # Upsert: first insert includes the hours, conflict adds to existing
            conn.execute(
                """INSERT INTO tenant_billing
                   (tenant_id, month, gpu_hours_used, gpu_hours_included)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(tenant_id, month) DO UPDATE SET
                    gpu_hours_used = gpu_hours_used + ?,
                    gpu_hours_included = ?""",
                (tenant_id, month, hours, gpu_hours_included, hours, gpu_hours_included),
            )
            conn.commit()

            # Calculate overage
            row = conn.execute(
                "SELECT gpu_hours_used, gpu_hours_included FROM tenant_billing "
                "WHERE tenant_id = ? AND month = ?",
                (tenant_id, month),
            ).fetchone()

            used = row["gpu_hours_used"] if row else hours
            included = row["gpu_hours_included"] if row else gpu_hours_included
            overage = max(0, used - included)
            overage_cost = overage * GPU_COST_PER_HOUR

            conn.execute(
                "UPDATE tenant_billing SET gpu_overage_cost = ? "
                "WHERE tenant_id = ? AND month = ?",
                (overage_cost, tenant_id, month),
            )
            conn.commit()

            return {
                "tenant_id": tenant_id,
                "month": month,
                "gpu_hours_used": used,
                "gpu_hours_included": included,
                "overage_hours": overage,
                "overage_cost": overage_cost,
            }
        finally:
            conn.close()

    async def get_monthly_summary(
        self, tenant_id: str, month: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get monthly billing summary for a tenant."""
        month = month or datetime.now(timezone.utc).strftime("%Y-%m")

        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM tenant_billing WHERE tenant_id = ? AND month = ?",
                (tenant_id, month),
            ).fetchone()

            if not row:
                return {
                    "tenant_id": tenant_id,
                    "month": month,
                    "total_raw_cost": 0.0,
                    "total_billed_cost": 0.0,
                    "total_tier_0_saved": 0.0,
                    "request_count": 0,
                    "tier_0_request_count": 0,
                    "tier_0_percentage": 0.0,
                    "gpu_hours_used": 0.0,
                    "gpu_hours_included": 3.0,
                    "gpu_overage_cost": 0.0,
                    "total_invoice": 0.0,
                    "roi_message": "No usage this month.",
                }

            data = dict(row)
            total_reqs = data["request_count"]
            t0_reqs = data["tier_0_request_count"]
            t0_pct = (t0_reqs / total_reqs * 100) if total_reqs > 0 else 0.0

            total_invoice = data["total_billed_cost"] + data["gpu_overage_cost"]
            saved = data["total_tier_0_saved"]

            roi_msg = f"Your adapter saved ${saved:.2f} this month." if saved > 0 else ""
            if t0_pct > 0:
                roi_msg += f" {t0_pct:.0f}% of requests served free via Tier 0."

            data["tier_0_percentage"] = t0_pct
            data["total_invoice"] = total_invoice
            data["roi_message"] = roi_msg.strip()
            return data
        finally:
            conn.close()

    async def get_all_tenants_summary(
        self, month: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get billing summary across all tenants for a month."""
        month = month or datetime.now(timezone.utc).strftime("%Y-%m")

        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM tenant_billing WHERE month = ? ORDER BY total_billed_cost DESC",
                (month,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
