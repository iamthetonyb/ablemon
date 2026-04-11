"""
E3 — Gateway Prompt Caching.

Session-scoped provider instance caching with system prompt fingerprinting.
Preserves Anthropic prompt cache tokens across turns by keeping the system
prompt + early message prefix stable per session.

Forked from Hermes v0.4 PR #2282 pattern.

Usage:
    cache = PromptCache()
    key = cache.session_key("user-123", tier=4)
    cached = cache.get(key)
    if not cached:
        cache.put(key, PromptCacheEntry(system_prompt=sp, provider_chain=chain))

    # On each turn, check if system prompt changed:
    if cache.is_prefix_stable(key, new_system_prompt):
        # Reuse cached chain — Anthropic cache hit likely
        chain = cached.provider_chain
    else:
        # System prompt changed — invalidate, rebuild
        cache.invalidate(key)

Integration:
    Wire into gateway.py process_message() / stream_message() — before
    provider selection, check cache. After system prompt assembly, verify
    prefix stability.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PromptCacheEntry:
    """A cached provider state for a session + tier combination."""

    system_prompt: str
    system_prompt_hash: str = ""
    provider_chain: Any = None  # ProviderChain instance
    early_messages: List[Dict[str, Any]] = field(default_factory=list)
    early_messages_hash: str = ""
    created_at: float = field(default_factory=time.time)
    last_hit_at: float = field(default_factory=time.time)
    hit_count: int = 0
    turn_count: int = 0

    def __post_init__(self):
        if not self.system_prompt_hash and self.system_prompt:
            self.system_prompt_hash = _fingerprint(self.system_prompt)
        if not self.early_messages_hash and self.early_messages:
            self.early_messages_hash = _fingerprint(
                "".join(str(m) for m in self.early_messages)
            )


@dataclass
class CacheStats:
    """Prompt cache performance statistics."""

    total_lookups: int = 0
    hits: int = 0
    misses: int = 0
    invalidations: int = 0
    prefix_changes: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float:
        if self.total_lookups == 0:
            return 0.0
        return self.hits / self.total_lookups

    def summary(self) -> str:
        return (
            f"lookups={self.total_lookups} hits={self.hits} "
            f"misses={self.misses} hit_rate={self.hit_rate:.1%} "
            f"invalidations={self.invalidations} evictions={self.evictions}"
        )


def _fingerprint(text: str) -> str:
    """SHA-256 fingerprint (first 16 hex chars)."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class PromptCache:
    """Session-scoped prompt cache for provider instance reuse.

    Keyed by (session_id, tier). Keeps provider chain instances alive
    so Anthropic's prompt cache can recognize the stable prefix.

    TTL-based eviction: entries older than max_age_s are cleaned up.
    Max entries cap prevents unbounded growth.
    """

    def __init__(
        self,
        max_entries: int = 100,
        max_age_s: float = 3600.0,
    ):
        self._cache: Dict[str, PromptCacheEntry] = {}
        self._max_entries = max_entries
        self._max_age_s = max_age_s
        self._stats = CacheStats()

    @staticmethod
    def session_key(session_id: str, tier: int = 0) -> str:
        """Generate cache key from session ID and tier."""
        return f"{session_id}:t{tier}"

    def get(self, key: str) -> Optional[PromptCacheEntry]:
        """Look up a cached entry."""
        self._stats.total_lookups += 1
        entry = self._cache.get(key)
        if entry is None:
            self._stats.misses += 1
            return None

        # Check TTL
        if time.time() - entry.created_at > self._max_age_s:
            self._cache.pop(key, None)
            self._stats.misses += 1
            self._stats.evictions += 1
            return None

        entry.last_hit_at = time.time()
        entry.hit_count += 1
        self._stats.hits += 1
        return entry

    def put(self, key: str, entry: PromptCacheEntry) -> None:
        """Store a cache entry, evicting oldest if at capacity."""
        if len(self._cache) >= self._max_entries and key not in self._cache:
            self._evict_oldest()

        self._cache[key] = entry

    def is_prefix_stable(self, key: str, system_prompt: str) -> bool:
        """Check if the system prompt prefix is unchanged for this key.

        If stable, the Anthropic prompt cache is likely to hit. If changed,
        the caller should invalidate and rebuild.
        """
        entry = self._cache.get(key)
        if entry is None:
            return False

        new_hash = _fingerprint(system_prompt)
        if entry.system_prompt_hash == new_hash:
            return True

        self._stats.prefix_changes += 1
        return False

    def is_early_messages_stable(
        self, key: str, early_messages: List[Dict[str, Any]]
    ) -> bool:
        """Check if the early messages (first N turns) are unchanged."""
        entry = self._cache.get(key)
        if entry is None:
            return False

        new_hash = _fingerprint("".join(str(m) for m in early_messages))
        return entry.early_messages_hash == new_hash

    def record_turn(self, key: str) -> None:
        """Increment turn count for a cached session."""
        entry = self._cache.get(key)
        if entry:
            entry.turn_count += 1
            entry.last_hit_at = time.time()

    def invalidate(self, key: str) -> bool:
        """Remove a specific cache entry."""
        removed = self._cache.pop(key, None) is not None
        if removed:
            self._stats.invalidations += 1
        return removed

    def invalidate_session(self, session_id: str) -> int:
        """Remove all cache entries for a session (any tier)."""
        prefix = f"{session_id}:"
        to_remove = [k for k in self._cache if k.startswith(prefix)]
        for k in to_remove:
            self._cache.pop(k, None)
        self._stats.invalidations += len(to_remove)
        return len(to_remove)

    def _evict_oldest(self) -> None:
        """Evict the least-recently-used entry."""
        if not self._cache:
            return
        oldest_key = min(
            self._cache, key=lambda k: self._cache[k].last_hit_at
        )
        self._cache.pop(oldest_key, None)
        self._stats.evictions += 1

    def cleanup_expired(self) -> int:
        """Remove all entries older than max_age_s. Returns count removed."""
        now = time.time()
        expired = [
            k for k, v in self._cache.items()
            if now - v.created_at > self._max_age_s
        ]
        for k in expired:
            self._cache.pop(k, None)
        self._stats.evictions += len(expired)
        return len(expired)

    @property
    def stats(self) -> CacheStats:
        return self._stats

    @property
    def size(self) -> int:
        return len(self._cache)

    def all_keys(self) -> List[str]:
        return list(self._cache.keys())
