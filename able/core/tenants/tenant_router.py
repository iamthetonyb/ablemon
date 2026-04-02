"""
Tenant Router — Per-tenant model selection.

If tenant has Tier 0 adapter + confidence > threshold -> use adapter (free).
Else -> standard tiers with tenant system prompt, capped at tenant's max_tier.
"""

from __future__ import annotations

import logging

from able.core.tenants.tenant_manager import TenantManager

logger = logging.getLogger(__name__)


class TenantRouter:
    """Per-tenant model selection with adapter awareness."""

    # Confidence threshold for using Tier 0 adapter
    ADAPTER_CONFIDENCE_THRESHOLD = 0.80

    def __init__(self, tenant_manager: TenantManager):
        self.manager = tenant_manager

    def select_tier(
        self,
        tenant_id: str,
        complexity_score: float,
        budget_remaining: float,
    ) -> int:
        """Select tier for this tenant based on their config and budget.

        Priority:
        1. If adapter exists and enabled -> Tier 0 (free, self-hosted)
        2. Standard scoring, but capped at tenant's max_tier
        3. If Opus budget exhausted, cap at Tier 2
        """
        config = self.manager.get_tenant(tenant_id)
        if config is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        if config.status != "active":
            raise ValueError(f"Tenant is not active: {tenant_id} (status={config.status})")

        routing = config.routing
        max_tier = routing.get("max_tier", 4)

        # Check for Tier 0 adapter
        if routing.get("tier_0_enabled", False) and self.has_adapter(tenant_id):
            return 0

        # Standard tier selection
        if complexity_score <= 0.4:
            tier = 1
        elif complexity_score <= 0.7:
            tier = 2
        else:
            tier = 4

        # Cap at tenant's max tier
        if tier > max_tier:
            tier = min(max_tier, 2)

        # Budget gate: if Opus budget is exhausted, cap at Tier 2
        opus_budget = routing.get("opus_monthly_budget_usd", 50.0)
        if tier == 4 and budget_remaining <= 0:
            logger.warning(
                f"Tenant {tenant_id}: Opus budget exhausted "
                f"(limit=${opus_budget:.2f}), capping at Tier 2"
            )
            tier = 2

        return tier

    def get_system_prompt(self, tenant_id: str) -> str:
        """Get the tenant-specific system prompt.

        Reads from data_dir/{tenant_id}/prompts/system.txt if it exists,
        otherwise generates from personality field.
        """
        config = self.manager.get_tenant(tenant_id)
        if config is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        prompt_path = self.manager.tenant_data_path(tenant_id) / "prompts" / "system.txt"
        if prompt_path.exists():
            return prompt_path.read_text().strip()

        # Fallback: generate from personality
        return (
            f"You are a {config.domain} assistant for {config.name}.\n\n"
            f"{config.personality}"
        )

    def has_adapter(self, tenant_id: str) -> bool:
        """Check if tenant has a trained Tier 0 adapter.

        Looks for any .gguf or .safetensors file in the adapters directory.
        """
        config = self.manager.get_tenant(tenant_id)
        if config is None:
            return False

        adapters_dir = self.manager.tenant_data_path(tenant_id) / "adapters"
        if not adapters_dir.exists():
            return False

        adapter_extensions = (".gguf", ".safetensors", ".bin")
        return any(
            f.suffix in adapter_extensions
            for f in adapters_dir.iterdir()
            if f.is_file()
        )
