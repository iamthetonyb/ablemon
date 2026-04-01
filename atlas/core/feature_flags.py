"""
ATLAS Feature Flag System + Failure Circuit Breaker

Feature flags: YAML-backed, supports boolean/percentage/tenant/expiry.
Circuit breaker: Caps consecutive failures to prevent runaway API waste.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class FeatureFlag:
    name: str
    enabled: bool = False
    rollout_pct: float = 100.0  # 0-100, percentage of tenants
    tenant_ids: List[str] = field(default_factory=list)  # empty = all tenants
    expires_at: Optional[str] = None  # ISO datetime string
    description: str = ""


class FeatureFlagService:
    """YAML-backed feature flag service."""

    def __init__(self, config_path: str = None):
        if config_path is None:
            _project_root = Path(__file__).resolve().parents[2]
            config_path = str(_project_root / "config" / "feature_flags.yaml")
        self._config_path = Path(config_path)
        self._flags: Dict[str, FeatureFlag] = {}
        self._loaded_at: float = 0
        self.reload()

    def reload(self):
        """Load/reload flags from YAML."""
        if not self._config_path.exists():
            logger.warning(f"Feature flags config not found: {self._config_path}")
            return

        try:
            with open(self._config_path) as f:
                data = yaml.safe_load(f) or {}

            self._flags = {}
            for name, cfg in data.get("flags", {}).items():
                self._flags[name] = FeatureFlag(
                    name=name,
                    enabled=cfg.get("enabled", False),
                    rollout_pct=cfg.get("rollout_pct", 100.0),
                    tenant_ids=cfg.get("tenant_ids", []),
                    expires_at=cfg.get("expires_at"),
                    description=cfg.get("description", ""),
                )
            self._loaded_at = time.time()
            logger.debug(f"Loaded {len(self._flags)} feature flags")
        except Exception as e:
            logger.error(f"Failed to load feature flags: {e}")

    def is_enabled(self, flag_name: str, tenant_id: str = None) -> bool:
        """Check if a flag is enabled for the given tenant."""
        flag = self._flags.get(flag_name)
        if not flag:
            return False

        if not flag.enabled:
            return False

        # Check expiry
        if flag.expires_at:
            try:
                expiry = datetime.fromisoformat(flag.expires_at)
                if datetime.utcnow() > expiry:
                    return False
            except ValueError:
                pass

        # Check tenant-specific flags
        if flag.tenant_ids and tenant_id:
            if tenant_id not in flag.tenant_ids:
                return False

        # Check percentage rollout
        if flag.rollout_pct < 100.0 and tenant_id:
            # Consistent hash-based assignment
            hash_val = int(
                hashlib.md5(f"{flag_name}:{tenant_id}".encode()).hexdigest(), 16
            )
            if (hash_val % 100) >= flag.rollout_pct:
                return False

        return True

    def set_flag(self, name: str, enabled: bool):
        """Programmatically toggle a flag and persist to YAML."""
        if name in self._flags:
            self._flags[name].enabled = enabled
        else:
            self._flags[name] = FeatureFlag(name=name, enabled=enabled)
        self._save()

    def get_all(self) -> Dict[str, FeatureFlag]:
        """Get all flags."""
        return dict(self._flags)

    def _save(self):
        """Persist current flags to YAML."""
        data = {"version": 1, "flags": {}}
        for name, flag in self._flags.items():
            entry = {"enabled": flag.enabled, "description": flag.description}
            if flag.rollout_pct != 100.0:
                entry["rollout_pct"] = flag.rollout_pct
            if flag.tenant_ids:
                entry["tenant_ids"] = flag.tenant_ids
            if flag.expires_at:
                entry["expires_at"] = flag.expires_at
            data["flags"][name] = entry

        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)


class FailureCircuitBreaker:
    """
    Consecutive failure circuit breaker.

    Inspired by Claude Code's autocompact bug: 250K wasted API calls/day
    because there was no cap on consecutive failures. 3 lines of code fixed it.

    Usage:
        breaker = FailureCircuitBreaker(max_consecutive=3)

        for item in items:
            if breaker.is_tripped():
                logger.warning("Circuit breaker tripped")
                break
            try:
                result = await api_call(item)
                breaker.record_success()
            except Exception:
                breaker.record_failure()
    """

    def __init__(self, max_consecutive: int = 3, cooldown_seconds: float = 60.0):
        self.max_consecutive = max_consecutive
        self.cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._total_failures = 0
        self._total_successes = 0
        self._tripped_at: Optional[float] = None

    def record_success(self):
        """Record a successful operation. Resets consecutive counter."""
        self._consecutive_failures = 0
        self._total_successes += 1
        self._tripped_at = None

    def record_failure(self):
        """Record a failed operation."""
        self._consecutive_failures += 1
        self._total_failures += 1
        if self._consecutive_failures >= self.max_consecutive:
            self._tripped_at = time.time()

    def is_tripped(self) -> bool:
        """Check if breaker is tripped (too many consecutive failures)."""
        if self._consecutive_failures < self.max_consecutive:
            return False

        # Check cooldown
        if self._tripped_at and self.cooldown_seconds > 0:
            if time.time() - self._tripped_at > self.cooldown_seconds:
                # Cooldown expired, allow one retry
                self._consecutive_failures = self.max_consecutive - 1
                self._tripped_at = None
                return False

        return True

    def reset(self):
        """Manually reset the breaker."""
        self._consecutive_failures = 0
        self._tripped_at = None

    @property
    def stats(self) -> dict:
        return {
            "consecutive_failures": self._consecutive_failures,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "is_tripped": self.is_tripped(),
        }
