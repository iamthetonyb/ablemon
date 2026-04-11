"""
F2 — Provider Auth Aliases + F11 — Credential Pool Rotation.

Shared auth profiles across providers (one OAuth token used by multiple
OpenAI-tier providers). Credential pool rotation with least-used selection
and 401/403 cooldown.

Usage:
    pool = CredentialPool()
    pool.add("openai-1", "OPENAI_API_KEY_1")
    pool.add("openai-2", "OPENAI_API_KEY_2")

    cred = pool.get_next("openai")  # Returns least-used credential
    pool.mark_exhausted("openai-1")  # 401 → 1hr cooldown
    pool.mark_used("openai-2")       # Increment usage count

    # Auth aliases
    aliases = AuthAliasRegistry()
    aliases.register("openai-shared", env_var="OPENAI_API_KEY")
    aliases.resolve("openai-shared")  # Returns the API key value
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default cooldown for exhausted credentials (1 hour)
DEFAULT_COOLDOWN_S = 3600


@dataclass
class Credential:
    """A single API credential."""
    id: str
    env_var: str
    use_count: int = 0
    last_used_at: float = 0.0
    exhausted_at: float = 0.0  # 0 = not exhausted
    cooldown_s: float = DEFAULT_COOLDOWN_S

    @property
    def api_key(self) -> Optional[str]:
        return os.environ.get(self.env_var)

    @property
    def is_available(self) -> bool:
        """Check if credential is available (has key and not in cooldown)."""
        if not self.api_key:
            return False
        if self.exhausted_at > 0:
            elapsed = time.time() - self.exhausted_at
            if elapsed < self.cooldown_s:
                return False
        return True

    @property
    def cooldown_remaining(self) -> float:
        if self.exhausted_at <= 0:
            return 0.0
        remaining = self.cooldown_s - (time.time() - self.exhausted_at)
        return max(0.0, remaining)


@dataclass
class PoolStats:
    """Aggregate stats for a credential pool."""
    total: int = 0
    available: int = 0
    exhausted: int = 0
    missing_key: int = 0
    total_uses: int = 0


class CredentialPool:
    """Pool of rotating API credentials for a provider type.

    Uses least-used selection to distribute load. On 401/403,
    marks the credential as exhausted with a 1-hour cooldown.
    After cooldown, the credential becomes available again.
    """

    def __init__(self, cooldown_s: float = DEFAULT_COOLDOWN_S):
        self._credentials: Dict[str, Credential] = {}
        self._cooldown_s = cooldown_s

    def add(self, cred_id: str, env_var: str) -> None:
        """Add a credential to the pool."""
        self._credentials[cred_id] = Credential(
            id=cred_id,
            env_var=env_var,
            cooldown_s=self._cooldown_s,
        )

    def get_next(self) -> Optional[Credential]:
        """Get the least-used available credential.

        Returns None if all credentials are exhausted or missing.
        """
        available = [
            c for c in self._credentials.values()
            if c.is_available
        ]
        if not available:
            return None

        # Least-used first, break ties by longest idle
        available.sort(key=lambda c: (c.use_count, c.last_used_at))
        return available[0]

    def mark_used(self, cred_id: str) -> None:
        """Record a successful use of a credential."""
        cred = self._credentials.get(cred_id)
        if cred:
            cred.use_count += 1
            cred.last_used_at = time.time()
            # Clear exhausted state on successful use
            cred.exhausted_at = 0.0

    def mark_exhausted(self, cred_id: str) -> None:
        """Mark a credential as exhausted (401/403 received)."""
        cred = self._credentials.get(cred_id)
        if cred:
            cred.exhausted_at = time.time()
            logger.warning(
                "Credential '%s' marked exhausted — cooldown %ds",
                cred_id, int(cred.cooldown_s),
            )

    def stats(self) -> PoolStats:
        """Get pool statistics."""
        s = PoolStats(total=len(self._credentials))
        for cred in self._credentials.values():
            s.total_uses += cred.use_count
            if cred.is_available:
                s.available += 1
            elif cred.exhausted_at > 0:
                s.exhausted += 1
            else:
                s.missing_key += 1
        return s

    def reset(self) -> None:
        """Reset all cooldowns and usage counts."""
        for cred in self._credentials.values():
            cred.use_count = 0
            cred.last_used_at = 0.0
            cred.exhausted_at = 0.0


class AuthAliasRegistry:
    """Registry of shared auth profiles.

    Multiple providers can reference the same auth alias instead of
    duplicating env var configurations. Prevents credential bleed
    by isolating sessions.
    """

    def __init__(self):
        self._aliases: Dict[str, str] = {}  # alias_name → env_var
        self._pools: Dict[str, CredentialPool] = {}  # alias_name → pool

    def register(
        self,
        alias_name: str,
        env_var: Optional[str] = None,
        env_vars: Optional[List[str]] = None,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
    ) -> None:
        """Register an auth alias.

        Args:
            alias_name: The alias name (referenced in provider configs).
            env_var: Single env var for this alias.
            env_vars: Multiple env vars for credential rotation.
            cooldown_s: Cooldown for exhausted credentials.
        """
        if env_vars and len(env_vars) > 1:
            # Multi-credential pool
            pool = CredentialPool(cooldown_s=cooldown_s)
            for i, ev in enumerate(env_vars):
                pool.add(f"{alias_name}-{i}", ev)
            self._pools[alias_name] = pool
        elif env_var:
            self._aliases[alias_name] = env_var
        elif env_vars:
            self._aliases[alias_name] = env_vars[0]

    def resolve(self, alias_name: str) -> Optional[str]:
        """Resolve an alias to an API key value.

        For pooled aliases, returns the least-used available key.
        For simple aliases, returns the env var value.
        """
        # Check pools first
        if alias_name in self._pools:
            cred = self._pools[alias_name].get_next()
            if cred:
                return cred.api_key
            return None

        # Simple alias
        env_var = self._aliases.get(alias_name)
        if env_var:
            return os.environ.get(env_var)
        return None

    def mark_used(self, alias_name: str) -> None:
        """Mark the current credential as used (for pools)."""
        pool = self._pools.get(alias_name)
        if pool:
            cred = pool.get_next()
            if cred:
                pool.mark_used(cred.id)

    def mark_exhausted(self, alias_name: str) -> None:
        """Mark the current credential as exhausted (for pools)."""
        pool = self._pools.get(alias_name)
        if pool:
            # Mark the least-used (which was just returned)
            cred = pool.get_next()
            if cred:
                pool.mark_exhausted(cred.id)

    def get_pool(self, alias_name: str) -> Optional[CredentialPool]:
        """Get the credential pool for an alias (if pooled)."""
        return self._pools.get(alias_name)

    def list_aliases(self) -> List[str]:
        """List all registered alias names."""
        return sorted(set(list(self._aliases.keys()) + list(self._pools.keys())))
