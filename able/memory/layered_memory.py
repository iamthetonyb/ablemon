"""MemPalace 4-Layer Memory Stack.

Inspired by the MemPalace architecture (96.6% R@5 on LongMemEval):

  L0 (~50 tokens)    — Identity: name, role, current objectives. Always loaded.
  L1 (~500-800 tokens)— Essential story: auto-generated summary of top facts.
  L2 (on-demand)      — Filtered retrieval: query-driven search via HybridMemory.
  L3 (deep search)    — Full semantic search: FTS5 + vector similarity.

Wake-up cost drops from unbounded to ~170 tokens (L0+L1).
L2/L3 only fetched when context demands specific recall.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Target token budgets per layer (approximate — 1 token ≈ 4 chars)
L0_MAX_CHARS = 200   # ~50 tokens
L1_MAX_CHARS = 3200  # ~800 tokens
L2_DEFAULT_LIMIT = 5
L3_DEFAULT_LIMIT = 10


@dataclass
class MemoryLayer:
    """A single layer's content and metadata."""
    level: int  # 0-3
    content: str = ""
    token_estimate: int = 0
    source_count: int = 0

    @property
    def is_loaded(self) -> bool:
        return bool(self.content.strip())


@dataclass
class LayeredMemoryConfig:
    """Configuration for the 4-layer memory stack."""
    identity_path: Path = field(
        default_factory=lambda: Path.home() / ".able" / "memory" / "identity.yaml"
    )
    objectives_path: Path = field(
        default_factory=lambda: Path.home() / ".able" / "memory" / "current_objectives.yaml"
    )
    learnings_path: Path = field(
        default_factory=lambda: Path.home() / ".able" / "memory" / "learnings.md"
    )
    l0_max_chars: int = L0_MAX_CHARS
    l1_max_chars: int = L1_MAX_CHARS
    l2_limit: int = L2_DEFAULT_LIMIT
    l3_limit: int = L3_DEFAULT_LIMIT


class LayeredMemory:
    """4-layer memory stack with graduated retrieval cost.

    Usage::

        mem = LayeredMemory(config=LayeredMemoryConfig())
        # On session start — cheap, always loaded:
        context = mem.wake_up()  # Returns L0 + L1 (~170 tokens)

        # On demand — when user asks about something specific:
        results = mem.recall("routing configuration", depth=2)  # L2

        # Deep search — when comprehensive recall needed:
        results = mem.recall("how does the distillation pipeline work", depth=3)
    """

    def __init__(
        self,
        config: Optional[LayeredMemoryConfig] = None,
        hybrid_memory: Optional[Any] = None,
    ):
        self.config = config or LayeredMemoryConfig()
        self._hybrid = hybrid_memory  # Optional HybridMemory for L2/L3
        self._layers: Dict[int, MemoryLayer] = {
            0: MemoryLayer(level=0),
            1: MemoryLayer(level=1),
            2: MemoryLayer(level=2),
            3: MemoryLayer(level=3),
        }

    # ── L0: Identity ────────────────────────────────────────────

    def _load_l0(self) -> MemoryLayer:
        """Load L0 identity from disk or defaults."""
        parts = []

        # Try identity.yaml
        if self.config.identity_path.exists():
            try:
                import yaml
                data = yaml.safe_load(self.config.identity_path.read_text())
                if isinstance(data, dict):
                    name = data.get("name", "ABLE")
                    role = data.get("role", "autonomous agent")
                    parts.append(f"{name}: {role}")
                    if data.get("owner"):
                        parts.append(f"Owner: {data['owner']}")
            except Exception as exc:
                logger.debug("[LayeredMemory] Failed to load identity.yaml: %s", exc)

        if not parts:
            parts.append("ABLE: autonomous business & learning engine")

        # B6: Load buddy state into L0 identity
        try:
            from able.core.buddy.model import load_active_buddy
            buddy = load_active_buddy()
            if buddy:
                buddy_summary = (
                    f"Buddy: {buddy.name} L{buddy.level} "
                    f"({buddy.species.value})"
                )
                needs = buddy.get_needs()
                if needs.mood != "thriving":
                    buddy_summary += f" [{needs.mood}]"
                parts.append(buddy_summary)
        except Exception:
            pass  # Buddy system optional

        # Try current_objectives.yaml
        if self.config.objectives_path.exists():
            try:
                import yaml
                data = yaml.safe_load(self.config.objectives_path.read_text())
                if isinstance(data, dict):
                    objectives = data.get("objectives", data.get("current", []))
                    if isinstance(objectives, list) and objectives:
                        top = objectives[:3]
                        parts.append("Goals: " + "; ".join(
                            o if isinstance(o, str) else o.get("title", str(o))
                            for o in top
                        ))
            except Exception as exc:
                logger.debug("[LayeredMemory] Failed to load objectives: %s", exc)

        content = " | ".join(parts)
        if len(content) > self.config.l0_max_chars:
            content = content[: self.config.l0_max_chars - 3] + "..."

        layer = MemoryLayer(
            level=0,
            content=content,
            token_estimate=len(content) // 4,
            source_count=len(parts),
        )
        self._layers[0] = layer
        return layer

    # ── L1: Essential Story ─────────────────────────────────────

    def _load_l1(self) -> MemoryLayer:
        """Load L1 essential summary from learnings + recent context."""
        parts = []

        # Load learnings.md
        if self.config.learnings_path.exists():
            try:
                raw = self.config.learnings_path.read_text()
                # Extract bullet points (most learnings files are bullet lists)
                lines = [
                    line.strip().lstrip("- ").lstrip("* ")
                    for line in raw.splitlines()
                    if line.strip() and line.strip().startswith(("-", "*"))
                ]
                if lines:
                    parts.extend(lines[:10])  # Top 10 learnings
            except Exception as exc:
                logger.debug("[LayeredMemory] Failed to load learnings: %s", exc)

        # Pull top learnings from HybridMemory if available
        if self._hybrid:
            try:
                from .hybrid_memory import MemoryType
                entries = self._hybrid.search(
                    query="",  # empty query returns recent
                    memory_types=[MemoryType.LEARNING],
                    limit=10,
                    min_score=0.0,
                )
                for entry in entries:
                    summary = entry.content[:200]
                    if summary not in parts:
                        parts.append(summary)
            except Exception as exc:
                logger.debug("[LayeredMemory] L1 HybridMemory search failed: %s", exc)

        content = "\n".join(f"- {p}" for p in parts[:15])
        if len(content) > self.config.l1_max_chars:
            content = content[: self.config.l1_max_chars - 3] + "..."

        layer = MemoryLayer(
            level=1,
            content=content,
            token_estimate=len(content) // 4,
            source_count=len(parts),
        )
        self._layers[1] = layer
        return layer

    # ── L2: On-demand Filtered Retrieval ────────────────────────

    def _query_l2(self, query: str) -> MemoryLayer:
        """L2: filtered retrieval for a specific query."""
        if not self._hybrid:
            return MemoryLayer(level=2, content="", source_count=0)

        try:
            entries = self._hybrid.search(
                query=query,
                limit=self.config.l2_limit,
                min_score=0.3,
            )
            parts = []
            for entry in entries:
                tag = entry.memory_type.value if hasattr(entry.memory_type, "value") else str(entry.memory_type)
                parts.append(f"[{tag}] {entry.content[:300]}")

            content = "\n".join(parts)
            layer = MemoryLayer(
                level=2,
                content=content,
                token_estimate=len(content) // 4,
                source_count=len(entries),
            )
            self._layers[2] = layer
            return layer
        except Exception as exc:
            logger.debug("[LayeredMemory] L2 query failed: %s", exc)
            return MemoryLayer(level=2)

    # ── L3: Deep Semantic Search ────────────────────────────────

    def _query_l3(self, query: str) -> MemoryLayer:
        """L3: deep semantic search (FTS5 + vector similarity)."""
        if not self._hybrid:
            return MemoryLayer(level=3, content="", source_count=0)

        try:
            entries = self._hybrid.search(
                query=query,
                limit=self.config.l3_limit,
                min_score=0.1,  # Lower threshold for deep search
            )
            parts = []
            for entry in entries:
                tag = entry.memory_type.value if hasattr(entry.memory_type, "value") else str(entry.memory_type)
                meta = ""
                if entry.metadata:
                    meta = f" ({', '.join(f'{k}={v}' for k, v in list(entry.metadata.items())[:3])})"
                parts.append(f"[{tag}{meta}] {entry.content[:500]}")

            content = "\n".join(parts)
            layer = MemoryLayer(
                level=3,
                content=content,
                token_estimate=len(content) // 4,
                source_count=len(entries),
            )
            self._layers[3] = layer
            return layer
        except Exception as exc:
            logger.debug("[LayeredMemory] L3 query failed: %s", exc)
            return MemoryLayer(level=3)

    # ── Public API ──────────────────────────────────────────────

    def wake_up(self) -> str:
        """Load L0 + L1 for session start. ~170 tokens."""
        l0 = self._load_l0()
        l1 = self._load_l1()

        parts = []
        if l0.content:
            parts.append(f"[Identity] {l0.content}")
        if l1.content:
            parts.append(f"[Context]\n{l1.content}")

        total = "\n\n".join(parts)
        total_tokens = (l0.token_estimate or 0) + (l1.token_estimate or 0)
        logger.info(
            "[LayeredMemory] Wake-up: L0=%d tokens, L1=%d tokens, total=%d tokens",
            l0.token_estimate, l1.token_estimate, total_tokens,
        )
        return total

    def recall(self, query: str, depth: int = 2) -> str:
        """Recall memories at specified depth.

        Args:
            query: What to search for.
            depth: 2 = filtered retrieval, 3 = deep semantic search.

        Returns:
            Formatted memory context string.
        """
        if depth <= 1:
            return self.wake_up()

        parts = []

        # Always include L0 for identity grounding
        l0 = self._layers[0] if self._layers[0].is_loaded else self._load_l0()
        if l0.content:
            parts.append(f"[Identity] {l0.content}")

        if depth >= 2:
            l2 = self._query_l2(query)
            if l2.content:
                parts.append(f"[Recalled — {l2.source_count} entries]\n{l2.content}")

        if depth >= 3:
            l3 = self._query_l3(query)
            if l3.content:
                parts.append(f"[Deep Search — {l3.source_count} entries]\n{l3.content}")

        return "\n\n".join(parts)

    def get_layer(self, level: int) -> MemoryLayer:
        """Get a specific layer's current state."""
        return self._layers.get(level, MemoryLayer(level=level))

    def get_stats(self) -> Dict[str, Any]:
        """Get memory layer statistics."""
        return {
            "layers": {
                level: {
                    "loaded": layer.is_loaded,
                    "token_estimate": layer.token_estimate,
                    "source_count": layer.source_count,
                    "chars": len(layer.content),
                }
                for level, layer in self._layers.items()
            },
            "total_tokens": sum(l.token_estimate for l in self._layers.values()),
            "hybrid_memory_connected": self._hybrid is not None,
        }
