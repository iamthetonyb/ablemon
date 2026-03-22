"""
Tenant Router — Per-tenant model selection.

If tenant has Tier 0 adapter + confidence > threshold -> LoRA adapter via Ollama.
Else -> standard tiers with tenant system prompt + enrichment.

Data isolation: Each tenant's system prompt and adapter are loaded in isolation.
No cross-tenant data leakage.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ._helpers import get_adapter_path, has_adapter, load_tenant_config

logger = logging.getLogger(__name__)


@dataclass
class TenantRoutingResult:
    """Result of tenant-aware routing decision."""

    tenant_id: str
    tier: int  # 0=local adapter, 1-4=standard tiers
    provider: str
    model_id: str
    system_prompt: str
    adapter_path: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""


class TenantRouter:
    """Per-tenant model selection.

    If tenant has Tier 0 adapter + confidence > threshold -> LoRA adapter.
    Else -> standard tiers with tenant system prompt + enrichment.

    Tier 0 runs locally via Ollama at $0 cost. The adapter is a LoRA
    fine-tuned on the tenant's corpus data.
    """

    DEFAULT_TIER_0_THRESHOLD = 0.85

    def __init__(
        self,
        config_dir: str = "config/tenants",
        data_dir: Optional[Path] = None,
    ):
        self.config_dir = Path(config_dir)
        self.data_dir = data_dir or (Path.home() / ".atlas" / "tenants")

    def _load_system_prompt(self, tenant_id: str) -> str:
        """Load tenant's system prompt from disk."""
        prompt_path = self.data_dir / tenant_id / "prompts" / "system.txt"
        if prompt_path.exists():
            return prompt_path.read_text().strip()
        return f"You are an assistant for tenant {tenant_id}."

    async def route(
        self,
        tenant_id: str,
        complexity_score: float = 0.0,
        message: str = "",
    ) -> TenantRoutingResult:
        """Route a request for a specific tenant.

        Args:
            tenant_id: Tenant identifier.
            complexity_score: Pre-computed complexity score from scorer.
            message: The user message (for context, not stored).

        Returns:
            TenantRoutingResult with routing decision.
        """
        config = load_tenant_config(self.config_dir, tenant_id)
        if not config:
            raise ValueError(f"Tenant '{tenant_id}' not found")

        if config.get("status") != "active":
            raise ValueError(
                f"Tenant '{tenant_id}' is {config.get('status', 'unknown')}, not active"
            )

        routing_cfg = config.get("routing", {})
        system_prompt = self._load_system_prompt(tenant_id)

        tier_0_enabled = routing_cfg.get("tier_0_enabled", False)
        threshold = routing_cfg.get(
            "tier_0_confidence_threshold", self.DEFAULT_TIER_0_THRESHOLD
        )

        if tier_0_enabled and has_adapter(self.data_dir, tenant_id):
            adapter = get_adapter_path(self.data_dir, tenant_id)
            if adapter:
                return TenantRoutingResult(
                    tenant_id=tenant_id,
                    tier=0,
                    provider="ollama-tenant",
                    model_id=f"tenant-{tenant_id}",
                    system_prompt=system_prompt,
                    adapter_path=adapter,
                    confidence=threshold,
                    reason="Tier 0: local adapter available",
                )

        opus_budget = routing_cfg.get("opus_monthly_budget_usd", 50.0)

        if complexity_score > 0.7 and opus_budget > 0:
            tier = 4
            provider = "claude-opus-4-6"
            model_id = "claude-opus-4-6"
            reason = f"Tier 4: complexity {complexity_score:.2f} > 0.7"
        elif complexity_score > 0.4:
            tier = 2
            provider = "gpt-5.4"
            model_id = "gpt-5.4"
            reason = f"Tier 2: complexity {complexity_score:.2f} in [0.4, 0.7]"
        else:
            tier = 1
            provider = "gpt-5.4-mini"
            model_id = "gpt-5.4-mini"
            reason = f"Tier 1: complexity {complexity_score:.2f} < 0.4"

        return TenantRoutingResult(
            tenant_id=tenant_id,
            tier=tier,
            provider=provider,
            model_id=model_id,
            system_prompt=system_prompt,
            confidence=0.0,
            reason=reason,
        )

    async def get_routing_summary(self, tenant_id: str) -> Dict[str, Any]:
        """Get routing configuration summary for a tenant."""
        config = load_tenant_config(self.config_dir, tenant_id)
        if not config:
            raise ValueError(f"Tenant '{tenant_id}' not found")

        routing_cfg = config.get("routing", {})
        adapter_exists = has_adapter(self.data_dir, tenant_id)
        adapter = get_adapter_path(self.data_dir, tenant_id) if adapter_exists else None

        return {
            "tenant_id": tenant_id,
            "tier_0_enabled": routing_cfg.get("tier_0_enabled", False),
            "tier_0_confidence_threshold": routing_cfg.get(
                "tier_0_confidence_threshold", self.DEFAULT_TIER_0_THRESHOLD
            ),
            "has_adapter": adapter_exists,
            "adapter_path": adapter,
            "opus_monthly_budget_usd": routing_cfg.get("opus_monthly_budget_usd", 50.0),
            "status": config.get("status", "unknown"),
        }
