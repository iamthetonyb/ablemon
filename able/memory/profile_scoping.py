"""
D16 — Profile-Scoped Memory.

Adds profile_id scoping to memory operations so that multi-user
environments (Telegram groups, shared instances) get per-user
memory isolation.

Usage:
    scope = ProfileScope(profile_id="user-123")
    scope.scoped_key("preferences")  # → "user-123:preferences"

    manager = ProfileMemoryManager(base_dir=Path("~/.able/memory"))
    manager.get_scope("user-123")     # Returns ProfileScope
    manager.list_profiles()           # ["user-123", "user-456"]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Default profile for single-user mode
DEFAULT_PROFILE = "default"


@dataclass
class ProfileScope:
    """Scoping context for a single user profile."""
    profile_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def scoped_key(self, key: str) -> str:
        """Prefix a key with profile scope."""
        if self.profile_id == DEFAULT_PROFILE:
            return key  # No prefix for default profile
        return f"{self.profile_id}:{key}"

    def unscoped_key(self, scoped_key: str) -> str:
        """Remove profile prefix from a scoped key."""
        prefix = f"{self.profile_id}:"
        if scoped_key.startswith(prefix):
            return scoped_key[len(prefix):]
        return scoped_key

    def is_own_key(self, scoped_key: str) -> bool:
        """Check if a scoped key belongs to this profile."""
        if self.profile_id == DEFAULT_PROFILE:
            return ":" not in scoped_key
        return scoped_key.startswith(f"{self.profile_id}:")


@dataclass
class ProfileStats:
    """Stats for a profile."""
    profile_id: str
    memory_count: int = 0
    last_active: str = ""


class ProfileMemoryManager:
    """Manages profile-scoped memory directories and isolation.

    Each profile gets its own subdirectory under the base memory dir.
    Memory operations are isolated: profile A cannot read profile B's data.
    The 'default' profile stores data at the base level (backwards compatible).
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self._base_dir = base_dir or Path.home() / ".able" / "memory"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._scopes: Dict[str, ProfileScope] = {}
        self._active_locks: Set[str] = set()  # Token lock isolation

    def get_scope(self, profile_id: str = DEFAULT_PROFILE) -> ProfileScope:
        """Get or create a profile scope.

        Args:
            profile_id: User identifier.

        Returns:
            ProfileScope for the given user.
        """
        if profile_id not in self._scopes:
            self._scopes[profile_id] = ProfileScope(profile_id=profile_id)
            # Ensure directory exists
            self.profile_dir(profile_id).mkdir(parents=True, exist_ok=True)
        return self._scopes[profile_id]

    def profile_dir(self, profile_id: str = DEFAULT_PROFILE) -> Path:
        """Get the directory for a profile's memory storage."""
        if profile_id == DEFAULT_PROFILE:
            return self._base_dir
        # Sanitize profile_id for filesystem
        safe_id = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in profile_id
        )
        return self._base_dir / "profiles" / safe_id

    def list_profiles(self) -> List[str]:
        """List all known profile IDs."""
        profiles = [DEFAULT_PROFILE]
        profiles_dir = self._base_dir / "profiles"
        if profiles_dir.exists():
            for d in profiles_dir.iterdir():
                if d.is_dir():
                    profiles.append(d.name)
        return profiles

    def acquire_token_lock(self, profile_id: str, token_id: str) -> bool:
        """Acquire a token lock for a profile (prevents credential sharing).

        Args:
            profile_id: The profile requesting the lock.
            token_id: The token/credential identifier.

        Returns:
            True if lock acquired, False if already held by another profile.
        """
        lock_key = f"token:{token_id}"
        if lock_key in self._active_locks:
            # Check if same profile holds it
            holder = self._lock_holders.get(lock_key)
            if holder and holder != profile_id:
                return False
        self._active_locks.add(lock_key)
        if not hasattr(self, "_lock_holders"):
            self._lock_holders: Dict[str, str] = {}
        self._lock_holders[lock_key] = profile_id
        return True

    def release_token_lock(self, profile_id: str, token_id: str) -> None:
        """Release a token lock."""
        lock_key = f"token:{token_id}"
        holder = getattr(self, "_lock_holders", {}).get(lock_key)
        if holder == profile_id:
            self._active_locks.discard(lock_key)
            self._lock_holders.pop(lock_key, None)

    def stats(self, profile_id: str = DEFAULT_PROFILE) -> ProfileStats:
        """Get stats for a profile."""
        pdir = self.profile_dir(profile_id)
        count = 0
        if pdir.exists():
            count = sum(1 for f in pdir.rglob("*") if f.is_file())
        return ProfileStats(
            profile_id=profile_id,
            memory_count=count,
        )
