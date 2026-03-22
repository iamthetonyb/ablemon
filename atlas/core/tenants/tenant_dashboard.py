"""
Tenant Dashboard — Per-tenant dashboard data for /tenant/{id}/dashboard.

Provides:
- Tier 0 percentage (free requests via adapter)
- Quality scores (hallucination, correctness, acceptance)
- Model version, next retrain date
- Improvements made by overnight daemon
- Cost savings from adapter
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from ._helpers import count_corpus_files, get_adapter_version, load_tenant_config

logger = logging.getLogger(__name__)


class TenantDashboard:
    """Per-tenant dashboard data aggregation.

    Combines data from billing, routing, and training to produce a
    single dashboard view for /tenant/{id}/dashboard.
    """

    def __init__(
        self,
        config_dir: str = "config/tenants",
        data_dir: Optional[Path] = None,
    ):
        self.config_dir = Path(config_dir)
        self.data_dir = data_dir or (Path.home() / ".atlas" / "tenants")

    async def get_dashboard(
        self,
        tenant_id: str,
        billing_summary: Optional[Dict[str, Any]] = None,
        routing_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build dashboard data for a tenant.

        Args:
            tenant_id: Tenant identifier.
            billing_summary: Pre-fetched monthly billing data (optional).
            routing_summary: Pre-fetched routing config (optional).

        Returns:
            Dashboard dict with all tenant metrics.
        """
        config = load_tenant_config(self.config_dir, tenant_id)
        if not config:
            raise ValueError(f"Tenant '{tenant_id}' not found")

        adapter_ver = get_adapter_version(self.data_dir, tenant_id)
        corpus_count = count_corpus_files(self.data_dir, tenant_id)
        distillation = config.get("distillation", {})
        routing_cfg = config.get("routing", {})
        billing_cfg = config.get("billing", {})

        billing_data = billing_summary or {}
        routing_data = routing_summary or {}
        quality = self._load_quality_scores(tenant_id)

        return {
            "tenant_id": tenant_id,
            "domain": config.get("domain", "general"),
            "status": config.get("status", "active"),
            "generated_at": datetime.now(timezone.utc).isoformat(),

            "model": {
                "adapter_version": adapter_ver,
                "has_adapter": adapter_ver is not None,
                "tier_0_enabled": routing_cfg.get("tier_0_enabled", False),
                "corpus_size": corpus_count,
                "training_threshold": distillation.get("training_threshold", 500),
                "auto_retrain": distillation.get("auto_retrain", True),
            },

            "cost": {
                "tier_0_percentage": billing_data.get("tier_0_percentage", 0.0),
                "total_saved": billing_data.get("total_tier_0_saved", 0.0),
                "total_billed": billing_data.get("total_billed_cost", 0.0),
                "total_requests": billing_data.get("request_count", 0),
                "plan": billing_cfg.get("plan", "standard"),
                "markup_percentage": billing_cfg.get("markup_percentage", 40),
                "gpu_hours_used": billing_data.get("gpu_hours_used", 0.0),
                "gpu_hours_included": billing_data.get("gpu_hours_included", 3.0),
            },

            "quality": quality,

            "routing": {
                "tier_0_enabled": routing_data.get("tier_0_enabled", False),
                "has_adapter": routing_data.get("has_adapter", adapter_ver is not None),
                "opus_monthly_budget_usd": routing_cfg.get("opus_monthly_budget_usd", 50.0),
            },
        }

    def _load_quality_scores(self, tenant_id: str) -> Dict[str, Any]:
        """Load quality scores from tenant data dir.

        Quality scores are written by the eval system and overnight daemon.
        Returns defaults if no scores exist yet.
        """
        scores_path = self.data_dir / tenant_id / "memory" / "quality_scores.yaml"
        if scores_path.exists():
            with open(scores_path) as f:
                return yaml.safe_load(f) or {}

        return {
            "hallucination_rate": None,
            "correctness_score": None,
            "acceptance_rate": None,
            "last_eval_date": None,
            "eval_count": 0,
        }

    async def update_quality_scores(
        self,
        tenant_id: str,
        hallucination_rate: Optional[float] = None,
        correctness_score: Optional[float] = None,
        acceptance_rate: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Update quality scores for a tenant.

        Called by eval system after running tenant-specific evals.
        """
        scores = self._load_quality_scores(tenant_id)

        if hallucination_rate is not None:
            scores["hallucination_rate"] = hallucination_rate
        if correctness_score is not None:
            scores["correctness_score"] = correctness_score
        if acceptance_rate is not None:
            scores["acceptance_rate"] = acceptance_rate

        scores["last_eval_date"] = datetime.now(timezone.utc).isoformat()
        scores["eval_count"] = scores.get("eval_count", 0) + 1

        scores_dir = self.data_dir / tenant_id / "memory"
        scores_dir.mkdir(parents=True, exist_ok=True)
        scores_path = scores_dir / "quality_scores.yaml"
        scores_path.write_text(yaml.dump(scores, default_flow_style=False))

        return scores
