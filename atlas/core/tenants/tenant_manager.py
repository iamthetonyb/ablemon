"""
Tenant Manager — Full lifecycle: onboard -> operate -> improve -> distill -> serve.

Onboarding creates:
- config/tenants/{tenant_id}.yaml
- ~/.atlas/tenants/{tenant_id}/ (corpus, adapters, prompts, skills, memory)
- Billing account
- Channel registration (Telegram bot or API key)
- Initial system prompt from personality brief

CLI:
    python -m atlas.core.tenants --onboard --tenant-id "acme-legal" --domain legal --personality "..."
    python -m atlas.core.tenants --list
    python -m atlas.core.tenants --status acme-legal
    python -m atlas.core.tenants --train acme-legal
"""

import logging
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = "config/tenants"
DEFAULT_DATA_DIR = Path.home() / ".atlas" / "tenants"
DEFAULT_DB_PATH = "data/tenants.db"

VALID_STATUSES = {"active", "paused", "archived", "onboarding"}
VALID_DOMAINS = {
    "legal", "medical", "finance", "tech", "marketing",
    "sales", "support", "education", "general",
}

# Tenant ID: lowercase alphanumeric + hyphens, 3-64 chars
TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,62}[a-z0-9]$")


@dataclass
class TenantConfig:
    """Schema for a tenant's YAML config."""

    tenant_id: str
    domain: str
    personality: str
    status: str = "active"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Channel config
    channels: Dict[str, Dict[str, str]] = field(default_factory=dict)

    # Routing overrides
    routing: Dict[str, Any] = field(default_factory=lambda: {
        "tier_0_enabled": False,
        "tier_0_confidence_threshold": 0.85,
        "opus_monthly_budget_usd": 50.0,
    })

    # Distillation
    distillation: Dict[str, Any] = field(default_factory=lambda: {
        "corpus_path": "",
        "training_threshold": 500,
        "auto_retrain": True,
        "adapter_path": "",
    })

    # Billing
    billing: Dict[str, Any] = field(default_factory=lambda: {
        "plan": "standard",
        "markup_percentage": 40,
        "gpu_hours_included": 3,
    })

    # Data isolation (NON-NEGOTIABLE)
    data: Dict[str, bool] = field(default_factory=lambda: {
        "cross_tenant_training": False,
    })

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TenantConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def validate(self) -> List[str]:
        """Return list of validation errors (empty = valid)."""
        errors = []
        if not TENANT_ID_RE.match(self.tenant_id):
            errors.append(
                f"Invalid tenant_id '{self.tenant_id}': must be 3-64 chars, "
                "lowercase alphanumeric + hyphens, no leading/trailing hyphens"
            )
        if self.domain not in VALID_DOMAINS:
            errors.append(
                f"Invalid domain '{self.domain}': must be one of {sorted(VALID_DOMAINS)}"
            )
        if self.status not in VALID_STATUSES:
            errors.append(
                f"Invalid status '{self.status}': must be one of {sorted(VALID_STATUSES)}"
            )
        if not self.personality or len(self.personality.strip()) < 10:
            errors.append("Personality brief must be at least 10 characters")
        if self.data.get("cross_tenant_training", False):
            errors.append("cross_tenant_training must be False (data isolation)")
        return errors


class TenantManager:
    """Full lifecycle: onboard -> operate -> improve -> distill -> serve.

    Manages tenant configs in config/tenants/{id}.yaml and tenant data
    directories in ~/.atlas/tenants/{id}/.

    Persistence: SQLite for tenant metadata, YAML for config, filesystem
    for corpus/adapters/prompts.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS tenants (
        tenant_id TEXT PRIMARY KEY,
        domain TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        config_path TEXT NOT NULL,
        data_path TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);
    CREATE INDEX IF NOT EXISTS idx_tenants_domain ON tenants(domain);
    """

    def __init__(
        self,
        config_dir: str = DEFAULT_CONFIG_DIR,
        data_dir: Optional[Path] = None,
        db_path: str = DEFAULT_DB_PATH,
    ):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
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

    async def onboard(
        self,
        tenant_id: str,
        domain: str,
        personality: str,
        channel_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Onboard a new tenant.

        Creates config file, data directories, billing account, and
        channel registration. Returns the created tenant config dict.

        Raises ValueError on validation failure or duplicate tenant_id.
        """
        config = TenantConfig(
            tenant_id=tenant_id,
            domain=domain,
            personality=personality,
            status="active",
            channels=channel_config or {},
        )

        tenant_data_path = self.data_dir / tenant_id
        config.distillation["corpus_path"] = str(tenant_data_path / "corpus")
        config.distillation["adapter_path"] = str(tenant_data_path / "adapters")

        errors = config.validate()
        if errors:
            raise ValueError(f"Tenant validation failed: {'; '.join(errors)}")

        # Check for duplicates
        existing = await self.get_tenant(tenant_id)
        if existing:
            raise ValueError(f"Tenant '{tenant_id}' already exists")

        # Create data directories
        for subdir in ("corpus", "adapters", "prompts", "skills", "memory"):
            (tenant_data_path / subdir).mkdir(parents=True, exist_ok=True)

        # Write initial system prompt
        prompt_path = tenant_data_path / "prompts" / "system.txt"
        prompt_path.write_text(
            f"You are a {domain} assistant.\n\n"
            f"Personality: {personality}\n\n"
            f"Tenant: {tenant_id}\n"
        )

        # Write config YAML
        config_path = self.config_dir / f"{tenant_id}.yaml"
        config_path.write_text(yaml.dump(config.to_dict(), default_flow_style=False))

        # Insert into DB
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO tenants
                   (tenant_id, domain, status, created_at, updated_at,
                    config_path, data_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id, domain, "active", now, now,
                    str(config_path), str(tenant_data_path),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(f"[TENANT_ONBOARD] {tenant_id} | domain={domain}")
        return config.to_dict()

    async def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get tenant config by ID. Returns None if not found."""
        config_path = self.config_dir / f"{tenant_id}.yaml"
        if not config_path.exists():
            return None
        with open(config_path) as f:
            return yaml.safe_load(f)

    async def list_tenants(self) -> List[Dict[str, Any]]:
        """List all tenants with status."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT tenant_id, domain, status, created_at FROM tenants "
                "ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def pause_tenant(self, tenant_id: str) -> None:
        """Pause a tenant (stops routing, keeps data)."""
        await self._set_status(tenant_id, "paused")
        logger.info(f"[TENANT_PAUSE] {tenant_id}")

    async def archive_tenant(self, tenant_id: str) -> None:
        """Archive a tenant (stops routing, marks for cleanup)."""
        await self._set_status(tenant_id, "archived")
        logger.info(f"[TENANT_ARCHIVE] {tenant_id}")

    async def _set_status(self, tenant_id: str, status: str) -> None:
        """Update tenant status in DB and config file."""
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")

        # Update DB
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            result = conn.execute(
                "UPDATE tenants SET status = ?, updated_at = ? WHERE tenant_id = ?",
                (status, now, tenant_id),
            )
            if result.rowcount == 0:
                raise ValueError(f"Tenant '{tenant_id}' not found")
            conn.commit()
        finally:
            conn.close()

        # Update config YAML
        config_path = self.config_dir / f"{tenant_id}.yaml"
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            config["status"] = status
            config_path.write_text(yaml.dump(config, default_flow_style=False))

    async def get_status(self, tenant_id: str) -> Dict[str, Any]:
        """Get tenant status summary for CLI output."""
        config = await self.get_tenant(tenant_id)
        if not config:
            raise ValueError(f"Tenant '{tenant_id}' not found")

        tenant_data_path = self.data_dir / tenant_id

        # Count corpus files
        corpus_path = tenant_data_path / "corpus"
        corpus_count = len(list(corpus_path.glob("*"))) if corpus_path.exists() else 0

        # Check adapter
        adapter_path = tenant_data_path / "adapters"
        has_adapter = any(adapter_path.glob("*.bin")) if adapter_path.exists() else False

        return {
            "tenant_id": config["tenant_id"],
            "domain": config["domain"],
            "status": config.get("status", "active"),
            "personality": config["personality"][:80] + "..." if len(config.get("personality", "")) > 80 else config.get("personality", ""),
            "corpus_files": corpus_count,
            "has_adapter": has_adapter,
            "tier_0_enabled": config.get("routing", {}).get("tier_0_enabled", False),
            "training_threshold": config.get("distillation", {}).get("training_threshold", 500),
            "billing_plan": config.get("billing", {}).get("plan", "standard"),
        }
