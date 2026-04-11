"""
F1 — Pluggable Compaction Strategy Registry.

Registry of compaction strategies that the ContextCompactor can select
from based on remaining budget, content type, and session state.

Forked from OpenClaw v4.7 pluggable compaction pattern.

Usage:
    registry = CompactionStrategyRegistry()
    strategy = registry.select(budget_ratio=0.3, content_type="code-heavy")
    result = strategy.compact(messages, budget_tokens=5000)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass
class CompactionResult:
    """Result from a compaction strategy."""
    messages: List[Dict[str, Any]]
    strategy_name: str
    tokens_before: int
    tokens_after: int
    messages_before: int
    messages_after: int

    @property
    def reduction_ratio(self) -> float:
        if self.tokens_before == 0:
            return 0
        return 1 - (self.tokens_after / self.tokens_before)


class CompactionStrategy(Protocol):
    """Protocol for compaction strategies."""

    @property
    def name(self) -> str: ...

    @property
    def aggressiveness(self) -> float:
        """0.0 = gentle, 1.0 = maximum compression."""
        ...

    def compact(
        self,
        messages: List[Dict[str, Any]],
        budget_tokens: int,
        estimate_fn: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]: ...


# ── Built-in strategies ──────────────────────────────────────────


class StripThinkingStrategy:
    """Remove <think> blocks from assistant messages.

    Cheapest strategy — no information loss for user-facing content.
    """
    name = "strip-thinking"
    aggressiveness = 0.1

    _THINK_RE = re.compile(
        r'<think>.*?</think>\s*|'
        r'\[Internal reasoning\].*?\[/Internal reasoning\]\s*',
        re.DOTALL | re.IGNORECASE,
    )

    def compact(
        self,
        messages: List[Dict[str, Any]],
        budget_tokens: int = 0,
        estimate_fn: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]:
        result = []
        for msg in messages:
            content = msg.get("content", "")
            if msg.get("role") == "assistant" and isinstance(content, str):
                stripped = self._THINK_RE.sub("", content).strip()
                if stripped != content:
                    result.append({**msg, "content": stripped})
                    continue
            result.append(msg)
        return result


class TruncateOldStrategy:
    """Truncate oldest messages to a character limit.

    Moderate strategy — preserves structure, loses detail in old messages.
    """
    name = "truncate-old"
    aggressiveness = 0.4

    def __init__(self, old_limit: int = 180, mid_limit: int = 500, recent_limit: int = 900):
        self._old = old_limit
        self._mid = mid_limit
        self._recent = recent_limit

    def compact(
        self,
        messages: List[Dict[str, Any]],
        budget_tokens: int = 0,
        estimate_fn: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]:
        n = len(messages)
        if n < 4:
            return messages

        old_end = int(n * 0.4)
        mid_end = int(n * 0.7)

        result = []
        for i, msg in enumerate(messages):
            if i < old_end:
                limit = self._old
            elif i < mid_end:
                limit = self._mid
            else:
                limit = self._recent
            result.append(self._truncate(msg, limit))
        return result

    @staticmethod
    def _truncate(msg: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
        content = msg.get("content", "")
        if isinstance(content, str) and len(content) > max_chars:
            return {**msg, "content": content[:max_chars] + "..."}
        return msg


class AggressiveSummaryStrategy:
    """Replace oldest chunk with a minimal summary.

    Most aggressive — significant information loss but maximum space recovery.
    """
    name = "aggressive-summary"
    aggressiveness = 0.8

    def __init__(self, compact_ratio: float = 0.7):
        self._ratio = compact_ratio

    def compact(
        self,
        messages: List[Dict[str, Any]],
        budget_tokens: int = 0,
        estimate_fn: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]:
        n = len(messages)
        compact_count = int(n * self._ratio)
        if compact_count < 2:
            return messages

        keep = max(n - compact_count, 3)
        compact_count = n - keep

        old = messages[:compact_count]
        recent = messages[compact_count:]

        # Build minimal summary
        user_msgs = [m.get("content", "")[:100] for m in old if m.get("role") == "user"]
        summary = f"[Compacted {compact_count} messages. "
        if user_msgs:
            summary += f"Topics: {'; '.join(user_msgs[:3])}"
        summary += "]"

        return [{"role": "system", "content": summary}] + recent


class DedupReadFileStrategy:
    """Remove duplicate read_file tool results for the same path.

    Targeted strategy for tool-heavy conversations.
    """
    name = "dedup-reads"
    aggressiveness = 0.2

    def compact(
        self,
        messages: List[Dict[str, Any]],
        budget_tokens: int = 0,
        estimate_fn: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]:
        written: set = set()
        reads: Dict[str, List[int]] = {}

        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            path = inp.get("file_path") or inp.get("path", "")
                            if name in ("read_file", "Read") and path:
                                reads.setdefault(path, []).append(i)
                            elif name in ("write_file", "Write", "edit_file", "Edit") and path:
                                written.add(path)

        remove = set()
        for path, indices in reads.items():
            if path in written:
                continue
            if len(indices) > 1:
                for idx in indices[:-1]:
                    remove.add(idx)

        if not remove:
            return messages
        return [m for i, m in enumerate(messages) if i not in remove]


# ── Strategy registry ────────────────────────────────────────────

_BUILTIN_STRATEGIES = [
    StripThinkingStrategy,
    DedupReadFileStrategy,
    TruncateOldStrategy,
    AggressiveSummaryStrategy,
]


class CompactionStrategyRegistry:
    """Registry of compaction strategies.

    Selects the best strategy based on remaining budget ratio
    and content characteristics.
    """

    def __init__(self):
        self._strategies: Dict[str, CompactionStrategy] = {}
        for cls in _BUILTIN_STRATEGIES:
            s = cls()
            self._strategies[s.name] = s

    def register(self, strategy: CompactionStrategy) -> None:
        """Register a custom compaction strategy."""
        self._strategies[strategy.name] = strategy

    def get(self, name: str) -> Optional[CompactionStrategy]:
        """Get strategy by name."""
        return self._strategies.get(name)

    def available(self) -> List[str]:
        """List available strategy names, sorted by aggressiveness."""
        return sorted(self._strategies.keys(), key=lambda n: self._strategies[n].aggressiveness)

    def select(
        self,
        budget_ratio: float = 0.5,
        content_type: str = "mixed",
    ) -> CompactionStrategy:
        """Select the best strategy for the current situation.

        Args:
            budget_ratio: Remaining budget as fraction (0=empty, 1=full).
                         Lower values → more aggressive strategies.
            content_type: Hint about content ("code-heavy", "tool-heavy", "mixed").

        Returns:
            The selected strategy.
        """
        # Tool-heavy → try dedup first
        if content_type == "tool-heavy" and budget_ratio > 0.3:
            return self._strategies["dedup-reads"]

        # Plenty of room → gentle
        if budget_ratio > 0.5:
            return self._strategies["strip-thinking"]

        # Moderate pressure → truncate
        if budget_ratio > 0.2:
            return self._strategies["truncate-old"]

        # High pressure → aggressive summary
        return self._strategies["aggressive-summary"]

    def select_chain(
        self,
        budget_ratio: float = 0.5,
        content_type: str = "mixed",
    ) -> List[CompactionStrategy]:
        """Select an ordered chain of strategies to try.

        Starts gentle, escalates if needed. Caller should try each
        in order until the budget is satisfied.
        """
        sorted_strategies = sorted(
            self._strategies.values(),
            key=lambda s: s.aggressiveness,
        )

        if budget_ratio > 0.5:
            return sorted_strategies[:2]  # Gentle only
        if budget_ratio > 0.2:
            return sorted_strategies[:3]  # Gentle + moderate
        return sorted_strategies  # Full chain

    def stats(self) -> Dict[str, Any]:
        """Return registry stats."""
        return {
            "count": len(self._strategies),
            "strategies": {
                name: {"aggressiveness": s.aggressiveness}
                for name, s in self._strategies.items()
            },
        }
