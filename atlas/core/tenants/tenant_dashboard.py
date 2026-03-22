"""
Tenant Dashboard — Per-tenant dashboard data for /tenant/{id}/dashboard endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from atlas.core.tenants.tenant_billing import TenantBilling
from atlas.core.tenants.tenant_manager import TenantManager
from atlas.core.tenants.tenant_router import TenantRouter
from atlas.core.tenants.training_scheduler import TenantTrainingScheduler

logger = logging.getLogger(__name__)


class TenantDashboard:
    """Per-tenant dashboard data aggregator."""

    def __init__(
        self,
        tenant_manager: TenantManager,
        billing: TenantBilling,
        router: TenantRouter | None = None,
        scheduler: TenantTrainingScheduler | None = None,
    ):
        self.manager = tenant_manager
        self.billing = billing
        self.router = router
        self.scheduler = scheduler

    def get_dashboard(self, tenant_id: str) -> Dict[str, Any]:
        """Full dashboard data for a tenant.

        Includes:
        - Tier 0 percentage (free requests)
        - Cost savings from adapter
        - Request volume stats
        - Model/adapter status
        - Next retrain date
        - Monthly billing summary
        """
        config = self.manager.get_tenant(tenant_id)
        if config is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        month = datetime.utcnow().strftime("%Y-%m")
        summary = self.billing.get_monthly_summary(
            tenant_id,
            month=month,
            markup_percentage=config.billing.get("markup_percentage", 40),
        )
        roi = self.billing.calculate_roi(tenant_id, month=month)

        has_adapter = False
        if self.router:
            has_adapter = self.router.has_adapter(tenant_id)

        next_retrain = None
        training_queue_position = None
        if self.scheduler:
            queue = self.scheduler.get_training_queue()
            for i, job in enumerate(queue):
                if job.get("tenant_id") == tenant_id:
                    training_queue_position = i + 1
                    break
            next_retrain = self.scheduler.estimate_next_available().isoformat()

        return {
            "tenant_id": tenant_id,
            "name": config.name,
            "domain": config.domain,
            "status": config.status,
            "generated_at": datetime.utcnow().isoformat(),
            "adapter": {
                "has_adapter": has_adapter,
                "tier_0_enabled": config.routing.get("tier_0_enabled", False),
                "tier_0_percentage": roi.get("tier_0_percentage", 0.0),
            },
            "billing": {
                "month": month,
                "total_requests": summary.get("total_requests", 0),
                "raw_cost_usd": summary.get("raw_cost_usd", 0.0),
                "billed_cost_usd": summary.get("billed_cost_usd", 0.0),
                "markup_percentage": config.billing.get("markup_percentage", 40),
            },
            "cost_savings": {
                "savings_usd": roi.get("savings_usd", 0.0),
                "message": roi.get("message", ""),
            },
            "training": {
                "next_retrain": next_retrain,
                "queue_position": training_queue_position,
                "auto_retrain": config.distillation.get("auto_retrain", True),
                "training_threshold": config.distillation.get("training_threshold", 500),
            },
            "routing": {
                "max_tier": config.routing.get("max_tier", 4),
                "opus_monthly_budget_usd": config.routing.get(
                    "opus_monthly_budget_usd", 50.0
                ),
            },
        }
