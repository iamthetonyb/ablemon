"""
F4 — Prompt Cache Diagnostics.

Tracks prompt cache fingerprints, hit/miss rates, break analysis,
and surfaces cache health per session. Designed for Anthropic's
prompt caching but extensible to other providers.

Usage:
    diag = CacheDiagnostics()
    diag.record_turn(fingerprint="abc123", creation=500, read=12000)
    diag.record_turn(fingerprint="abc123", creation=0, read=12500)
    report = diag.report()
    # report.hit_rate == 1.0 for second turn (full cache read)
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CacheTurn:
    """Single turn's cache telemetry."""
    fingerprint: str
    creation_tokens: int
    read_tokens: int
    timestamp: float = field(default_factory=time.time)

    @property
    def is_hit(self) -> bool:
        """Cache hit = read tokens > 0 and no creation."""
        return self.read_tokens > 0 and self.creation_tokens == 0

    @property
    def is_partial_hit(self) -> bool:
        """Partial hit = both creation and read tokens present."""
        return self.read_tokens > 0 and self.creation_tokens > 0

    @property
    def is_miss(self) -> bool:
        """Cache miss = creation tokens > 0 and no read."""
        return self.creation_tokens > 0 and self.read_tokens == 0


@dataclass
class CacheBreak:
    """Detected cache break event."""
    turn_index: int
    old_fingerprint: str
    new_fingerprint: str
    timestamp: float
    cause: str = ""  # Best-guess cause


@dataclass
class CacheReport:
    """Aggregated cache diagnostics for a session."""
    total_turns: int = 0
    hits: int = 0
    partial_hits: int = 0
    misses: int = 0
    empty_turns: int = 0  # No cache activity at all
    total_creation_tokens: int = 0
    total_read_tokens: int = 0
    breaks: List[CacheBreak] = field(default_factory=list)
    estimated_savings_pct: float = 0.0

    @property
    def hit_rate(self) -> float:
        """Full hit rate (0.0–1.0)."""
        cacheable = self.hits + self.partial_hits + self.misses
        if cacheable == 0:
            return 0.0
        return self.hits / cacheable

    @property
    def effective_rate(self) -> float:
        """Effective cache rate including partial hits (0.0–1.0)."""
        cacheable = self.hits + self.partial_hits + self.misses
        if cacheable == 0:
            return 0.0
        return (self.hits + 0.5 * self.partial_hits) / cacheable

    @property
    def healthy(self) -> bool:
        """Cache is healthy if effective rate > 50% after warmup."""
        if self.total_turns < 3:
            return True  # Not enough data
        return self.effective_rate > 0.5

    def summary(self) -> str:
        """Human-readable summary."""
        parts = [
            f"Cache: {self.total_turns} turns",
            f"{self.hits} hits / {self.partial_hits} partial / {self.misses} misses",
            f"hit_rate={self.hit_rate:.0%}",
            f"effective={self.effective_rate:.0%}",
        ]
        if self.breaks:
            parts.append(f"{len(self.breaks)} breaks detected")
        if self.estimated_savings_pct > 0:
            parts.append(f"~{self.estimated_savings_pct:.0%} cost savings")
        return " | ".join(parts)


class CacheDiagnostics:
    """Prompt cache diagnostics tracker.

    Records per-turn cache telemetry, detects fingerprint breaks,
    and produces aggregate reports for session health monitoring.
    """

    # Anthropic cache pricing: creation = 1.25x base, read = 0.1x base
    CREATION_COST_MULTIPLIER = 1.25
    READ_COST_MULTIPLIER = 0.10
    BASE_COST_MULTIPLIER = 1.0

    def __init__(self, session_id: str = ""):
        self._session_id = session_id
        self._turns: List[CacheTurn] = []
        self._breaks: List[CacheBreak] = []
        self._last_fingerprint: Optional[str] = None

    def record_turn(
        self,
        fingerprint: str = "",
        creation: int = 0,
        read: int = 0,
    ) -> None:
        """Record cache telemetry for one turn.

        Args:
            fingerprint: Hash of the cacheable prefix (system + early messages).
            creation: Cache creation input tokens from API response.
            read: Cache read input tokens from API response.
        """
        turn = CacheTurn(
            fingerprint=fingerprint,
            creation_tokens=creation,
            read_tokens=read,
        )
        self._turns.append(turn)

        # Detect fingerprint break
        if (
            self._last_fingerprint
            and fingerprint
            and fingerprint != self._last_fingerprint
        ):
            cause = self._guess_break_cause(len(self._turns) - 1)
            brk = CacheBreak(
                turn_index=len(self._turns) - 1,
                old_fingerprint=self._last_fingerprint,
                new_fingerprint=fingerprint,
                timestamp=turn.timestamp,
                cause=cause,
            )
            self._breaks.append(brk)
            logger.info(
                "Cache break at turn %d: %s → %s (%s)",
                brk.turn_index, brk.old_fingerprint[:8],
                brk.new_fingerprint[:8], cause or "unknown",
            )

        if fingerprint:
            self._last_fingerprint = fingerprint

    def report(self) -> CacheReport:
        """Generate aggregate cache report."""
        r = CacheReport(
            total_turns=len(self._turns),
            breaks=list(self._breaks),
        )

        for turn in self._turns:
            r.total_creation_tokens += turn.creation_tokens
            r.total_read_tokens += turn.read_tokens

            if turn.is_hit:
                r.hits += 1
            elif turn.is_partial_hit:
                r.partial_hits += 1
            elif turn.is_miss:
                r.misses += 1
            else:
                r.empty_turns += 1

        # Estimate savings: compare actual cost vs no-cache cost
        r.estimated_savings_pct = self._estimate_savings(r)

        return r

    def stabilize_fingerprint(
        self,
        system_prompt: str,
        early_messages: List[Dict[str, Any]],
    ) -> str:
        """Compute a stable cache fingerprint from system prompt + early messages.

        This helps detect when the cacheable prefix changes (causing breaks).
        """
        hasher = hashlib.sha256()
        hasher.update(system_prompt.encode("utf-8", errors="replace"))
        for msg in early_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = str(content)
            hasher.update(f"{role}:{content}".encode("utf-8", errors="replace"))
        return hasher.hexdigest()[:16]

    def reset(self) -> None:
        """Reset for a new session."""
        self._turns.clear()
        self._breaks.clear()
        self._last_fingerprint = None

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    def _guess_break_cause(self, turn_index: int) -> str:
        """Heuristic for why cache broke."""
        if turn_index <= 1:
            return "warmup"
        # If there's a large gap between turns, TTL expiry likely
        if len(self._turns) >= 2:
            prev = self._turns[-2]
            curr = self._turns[-1]
            gap = curr.timestamp - prev.timestamp
            if gap > 300:  # 5 min TTL
                return "ttl_expired"
        return "prefix_changed"

    def _estimate_savings(self, r: CacheReport) -> float:
        """Estimate cost savings from caching.

        Without cache: all tokens at base rate.
        With cache: creation at 1.25x, read at 0.1x.
        """
        total_tokens = r.total_creation_tokens + r.total_read_tokens
        if total_tokens == 0:
            return 0.0

        # Cost with cache
        actual_cost = (
            r.total_creation_tokens * self.CREATION_COST_MULTIPLIER
            + r.total_read_tokens * self.READ_COST_MULTIPLIER
        )

        # Cost without cache (everything at base rate)
        no_cache_cost = total_tokens * self.BASE_COST_MULTIPLIER

        if no_cache_cost == 0:
            return 0.0

        return max(0.0, 1.0 - actual_cost / no_cache_cost)
