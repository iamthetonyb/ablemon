"""
Tenant Manager — Full lifecycle: onboard, operate, improve, distill, serve.

Each tenant gets:
- config/tenants/{tenant_id}.yaml — configuration
- ~/.atlas/tenants/{tenant_id}/ — isolated data (corpus, adapters, prompts, memory)
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)

# Subdirectories created per tenant under the data root.
_TENANT_SUBDIRS = ("corpus", "adapters", "prompts", "memory", "billing")


@dataclass
class TenantConfig:
    """Configuration for a single tenant."""

    tenant_id: str
    name: str
    domain: str  # legal, medical, saas, etc.
    personality: str  # System prompt customization
    channels: Dict[str, Any] = field(default_factory=dict)
    routing: Dict[str, Any] = field(
        default_factory=lambda: {
            "tier_0_enabled": False,
            "opus_monthly_budget_usd": 50.0,
            "max_tier": 4,
        }
    )
    distillation: Dict[str, Any] = field(
        default_factory=lambda: {
            "training_threshold": 500,
            "auto_retrain": True,
        }
    )
    billing: Dict[str, Any] = field(
        default_factory=lambda: {
            "plan": "standard",
            "markup_percentage": 40,
        }
    )
    data: Dict[str, Any] = field(
        default_factory=lambda: {
            "cross_tenant_training": False,  # NEVER mix tenant data
        }
    )
    status: str = "active"  # active | paused | archived
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TenantConfig:
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


class TenantManager:
    """Full lifecycle: onboard -> operate -> improve -> distill -> serve."""

    def __init__(
        self,
        config_dir: str = "config/tenants",
        data_dir: str | None = None,
    ):
        self.config_dir = Path(config_dir)
        self.data_dir = Path(data_dir or os.path.expanduser("~/.atlas/tenants"))
        self._tenants: Dict[str, TenantConfig] = {}
        self._load_tenants()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def onboard(
        self,
        tenant_id: str,
        name: str,
        domain: str,
        personality: str,
        channel_config: Dict[str, Any] | None = None,
    ) -> TenantConfig:
        """Onboard a new tenant.

        Creates:
        - config/tenants/{tenant_id}.yaml
        - data_dir/{tenant_id}/ (corpus, adapters, prompts, memory, billing)
        - Initial system prompt from personality brief
        """
        if tenant_id in self._tenants:
            raise ValueError(f"Tenant already exists: {tenant_id}")

        config = TenantConfig(
            tenant_id=tenant_id,
            name=name,
            domain=domain,
            personality=personality,
            channels=channel_config or {},
        )

        self._create_tenant_dirs(tenant_id)

        # Write initial system prompt
        prompt_path = self._tenant_data_path(tenant_id) / "prompts" / "system.txt"
        prompt_path.write_text(
            f"You are a {domain} assistant for {name}.\n\n{personality}\n"
        )

        self._save_tenant(config)
        self._tenants[tenant_id] = config

        logger.info(f"Tenant onboarded: {tenant_id} ({name})")
        return config

    def get_tenant(self, tenant_id: str) -> TenantConfig | None:
        """Get tenant config by ID."""
        return self._tenants.get(tenant_id)

    def list_tenants(self, status: str | None = None) -> List[TenantConfig]:
        """List all tenants, optionally filtered by status."""
        tenants = list(self._tenants.values())
        if status is not None:
            tenants = [t for t in tenants if t.status == status]
        return tenants

    def update_tenant(self, tenant_id: str, **kwargs: Any) -> TenantConfig:
        """Update tenant configuration fields."""
        config = self._tenants.get(tenant_id)
        if config is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        for key, value in kwargs.items():
            if not hasattr(config, key):
                raise ValueError(f"Unknown tenant field: {key}")
            setattr(config, key, value)

        self._save_tenant(config)
        logger.info(f"Tenant updated: {tenant_id} fields={list(kwargs.keys())}")
        return config

    def pause_tenant(self, tenant_id: str) -> None:
        """Pause a tenant (stop processing, keep data)."""
        self.update_tenant(tenant_id, status="paused")

    def archive_tenant(self, tenant_id: str) -> None:
        """Archive a tenant (stop processing, compress data)."""
        self.update_tenant(tenant_id, status="archived")

    # ------------------------------------------------------------------
    # Data isolation helpers
    # ------------------------------------------------------------------

    def tenant_data_path(self, tenant_id: str) -> Path:
        """Return the isolated data directory for a tenant.

        Raises ValueError if the tenant does not exist — prevents
        accidental cross-tenant access via fabricated IDs.
        """
        if tenant_id not in self._tenants:
            raise ValueError(f"Tenant not found: {tenant_id}")
        return self._tenant_data_path(tenant_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tenant_data_path(self, tenant_id: str) -> Path:
        """Raw data path without existence check."""
        return self.data_dir / tenant_id

    def _load_tenants(self) -> None:
        """Load all tenant configs from config_dir/."""
        if not self.config_dir.exists():
            return

        for path in sorted(self.config_dir.glob("*.yaml")):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
                if data and isinstance(data, dict):
                    config = TenantConfig.from_dict(data)
                    self._tenants[config.tenant_id] = config
            except Exception as exc:
                logger.warning(f"Failed to load tenant config {path}: {exc}")

    def _save_tenant(self, config: TenantConfig) -> None:
        """Save tenant config to YAML file."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        path = self.config_dir / f"{config.tenant_id}.yaml"
        with open(path, "w") as f:
            yaml.dump(config.to_dict(), f, default_flow_style=False, sort_keys=False)

    def _create_tenant_dirs(self, tenant_id: str) -> None:
        """Create tenant data directories."""
        base = self._tenant_data_path(tenant_id)
        for subdir in _TENANT_SUBDIRS:
            (base / subdir).mkdir(parents=True, exist_ok=True)
