"""
Tenant Billing — Per-tenant cost tracking with configurable markup.

Tracks:
- API costs per tier (with configurable markup)
- GPU training hours (included vs overage)
- Self-hosted Tier 0 at $0
- Monthly ROI: 'Your adapter saved $X this month'
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Default per-million-token costs by provider (matches BillingTracker.DEFAULT_COSTS)
_DEFAULT_PROVIDER_COSTS: Dict[str, Dict[str, float]] = {
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.4": {"input": 2.50, "output": 10.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "ollama-local": {"input": 0.0, "output": 0.0},
    "tier-0-adapter": {"input": 0.0, "output": 0.0},
}


@dataclass
class TenantUsageRecord:
    """A single usage record for a tenant."""

    tenant_id: str
    timestamp: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    raw_cost_usd: float  # Actual provider cost
    tier: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TenantUsageRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class TenantBilling:
    """Per-tenant cost tracking with markup."""

    def __init__(
        self,
        data_dir: str | None = None,
        provider_costs: Dict[str, Dict[str, float]] | None = None,
    ):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.able/tenants"))
        self.provider_costs = provider_costs or _DEFAULT_PROVIDER_COSTS

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_usage(
        self,
        tenant_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        tier: int,
    ) -> None:
        """Record token usage for a tenant.

        Writes to data_dir/{tenant_id}/billing/usage.jsonl (append-only).
        """
        billing_dir = self.data_dir / tenant_id / "billing"
        billing_dir.mkdir(parents=True, exist_ok=True)

        record = TenantUsageRecord(
            tenant_id=tenant_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_cost_usd=cost_usd,
            tier=tier,
        )

        usage_path = billing_dir / "usage.jsonl"
        with open(usage_path, "a") as f:
            f.write(json.dumps(record.to_dict()) + "\n")

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    def get_monthly_summary(
        self,
        tenant_id: str,
        month: str | None = None,
        markup_percentage: float = 40.0,
    ) -> Dict[str, Any]:
        """Get monthly billing summary with markup applied.

        Args:
            tenant_id: Tenant identifier
            month: YYYY-MM string (defaults to current month)
            markup_percentage: Markup to apply on raw costs
        """
        if month is None:
            month = datetime.now(timezone.utc).strftime("%Y-%m")

        records = self._load_records(tenant_id, month_filter=month)

        total_raw_cost = sum(r.raw_cost_usd for r in records)
        total_input = sum(r.input_tokens for r in records)
        total_output = sum(r.output_tokens for r in records)
        markup_multiplier = 1.0 + (markup_percentage / 100.0)
        billed_cost = total_raw_cost * markup_multiplier

        by_tier: Dict[int, Dict[str, Any]] = {}
        for r in records:
            if r.tier not in by_tier:
                by_tier[r.tier] = {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "raw_cost_usd": 0.0,
                }
            by_tier[r.tier]["requests"] += 1
            by_tier[r.tier]["input_tokens"] += r.input_tokens
            by_tier[r.tier]["output_tokens"] += r.output_tokens
            by_tier[r.tier]["raw_cost_usd"] += r.raw_cost_usd

        return {
            "tenant_id": tenant_id,
            "month": month,
            "total_requests": len(records),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "raw_cost_usd": round(total_raw_cost, 4),
            "markup_percentage": markup_percentage,
            "billed_cost_usd": round(billed_cost, 4),
            "by_tier": by_tier,
        }

    def calculate_roi(self, tenant_id: str, month: str | None = None) -> Dict[str, Any]:
        """Calculate ROI from Tier 0 adapter (API cost savings).

        Compares Tier 0 requests (cost $0) against what they would have cost
        on Tier 1 (GPT 5.4 Mini).
        """
        if month is None:
            month = datetime.now(timezone.utc).strftime("%Y-%m")

        records = self._load_records(tenant_id, month_filter=month)

        tier_0_records = [r for r in records if r.tier == 0]
        non_tier_0_records = [r for r in records if r.tier != 0]

        # Estimate what Tier 0 requests would have cost on Tier 1
        t1_costs = self.provider_costs.get("gpt-5.4-mini", {"input": 0.75, "output": 4.50})
        hypothetical_cost = sum(
            (r.input_tokens / 1_000_000) * t1_costs["input"]
            + (r.output_tokens / 1_000_000) * t1_costs["output"]
            for r in tier_0_records
        )

        actual_api_cost = sum(r.raw_cost_usd for r in non_tier_0_records)
        total_requests = len(records)
        tier_0_percentage = (
            (len(tier_0_records) / total_requests * 100) if total_requests > 0 else 0.0
        )

        return {
            "tenant_id": tenant_id,
            "month": month,
            "tier_0_requests": len(tier_0_records),
            "total_requests": total_requests,
            "tier_0_percentage": round(tier_0_percentage, 1),
            "savings_usd": round(hypothetical_cost, 4),
            "actual_api_cost_usd": round(actual_api_cost, 4),
            "message": (
                f"Your adapter saved ${hypothetical_cost:.2f} this month "
                f"by handling {len(tier_0_records)} requests locally."
                if tier_0_records
                else "No Tier 0 adapter usage this month."
            ),
        }

    def get_invoice_data(self, tenant_id: str, month: str) -> Dict[str, Any]:
        """Get data for generating a tenant invoice."""
        summary = self.get_monthly_summary(tenant_id, month=month)
        roi = self.calculate_roi(tenant_id, month=month)

        return {
            "tenant_id": tenant_id,
            "invoice_month": month,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "roi": roi,
            "line_items": self._build_line_items(summary),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_records(
        self,
        tenant_id: str,
        month_filter: str | None = None,
    ) -> List[TenantUsageRecord]:
        """Load usage records for a tenant, optionally filtered by month."""
        usage_path = self.data_dir / tenant_id / "billing" / "usage.jsonl"
        if not usage_path.exists():
            return []

        records: List[TenantUsageRecord] = []
        with open(usage_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    record = TenantUsageRecord.from_dict(data)

                    if month_filter and not record.timestamp.startswith(month_filter):
                        continue

                    records.append(record)
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue

        return records

    def _build_line_items(self, summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build invoice line items from summary."""
        items: List[Dict[str, Any]] = []
        for tier, data in summary.get("by_tier", {}).items():
            raw = data["raw_cost_usd"]
            markup = summary["markup_percentage"]
            billed = raw * (1.0 + markup / 100.0)
            items.append(
                {
                    "description": f"Tier {tier} API usage ({data['requests']} requests)",
                    "input_tokens": data["input_tokens"],
                    "output_tokens": data["output_tokens"],
                    "raw_cost_usd": round(raw, 4),
                    "billed_cost_usd": round(billed, 4),
                }
            )
        return items
