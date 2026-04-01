"""
ATLAS Dream System -- Memory Consolidation Engine

Inspired by Claude Code's KAIROS dream system.
Three gates must pass before consolidation runs.
Four phases: Orient -> Gather -> Consolidate -> Prune.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DreamGates:
    min_hours_since_last: float = 24.0
    min_new_memories: int = 5
    lock_path: str = "data/.dream_lock"


@dataclass
class DreamResult:
    memories_scanned: int = 0
    clusters_found: int = 0
    duplicates_merged: int = 0
    insights_extracted: int = 0
    memories_pruned: int = 0
    learnings_updated: int = 0
    duration_seconds: float = 0.0


class DreamConsolidator:
    def __init__(self, memory=None, gates: DreamGates = None, learnings_path: str = None):
        self.memory = memory
        self.gates = gates or DreamGates()
        self.learnings_path = Path(
            learnings_path or os.path.expanduser("~/.atlas/memory/learnings.md")
        )
        _project_root = Path(__file__).resolve().parents[3]
        self._lock_path = _project_root / self.gates.lock_path

    async def should_run(self) -> tuple:
        """Check all three gates. Returns (should_run, reason)."""
        # Gate 1: Time since last run
        if self._lock_path.exists():
            try:
                last_run = float(self._lock_path.read_text().strip())
                hours_since = (time.time() - last_run) / 3600
                if hours_since < self.gates.min_hours_since_last:
                    return False, f"Only {hours_since:.1f}h since last run (need {self.gates.min_hours_since_last}h)"
            except (ValueError, OSError):
                pass

        # Gate 2: Minimum new memories
        if self.memory:
            since = datetime.utcnow() - timedelta(hours=self.gates.min_hours_since_last)
            recent = await self._get_recent_memories(since)
            if len(recent) < self.gates.min_new_memories:
                return False, f"Only {len(recent)} new memories (need {self.gates.min_new_memories})"

        # Gate 3: No active lock (concurrent run prevention checked via Gate 1)

        return True, "All gates passed"

    async def run_cycle(self) -> DreamResult:
        """Full four-phase dream cycle."""
        start = time.time()
        result = DreamResult()

        # Acquire lock
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.write_text(str(time.time()))

        try:
            # Phase 1: Orient -- load recent memories
            recent = await self._orient(result)

            # Phase 2: Gather -- cluster by topic
            clusters = await self._gather(recent, result)

            # Phase 3: Consolidate -- merge dupes, extract insights
            await self._consolidate(clusters, result)

            # Phase 4: Prune -- archive old low-value memories
            await self._prune(result)

            result.duration_seconds = time.time() - start
            logger.info(f"Dream cycle complete: {asdict(result)}")

        except Exception as e:
            logger.error(f"Dream cycle failed: {e}")
            result.duration_seconds = time.time() - start

        return result

    async def _orient(self, result: DreamResult) -> list:
        """Phase 1: Load recent memories."""
        if not self.memory:
            return []

        since = datetime.utcnow() - timedelta(hours=48)
        recent = await self._get_recent_memories(since)
        result.memories_scanned = len(recent)
        return recent

    async def _gather(self, recent: list, result: DreamResult) -> dict:
        """Phase 2: Cluster related memories by type."""
        clusters = {}
        for mem in recent:
            mem_type = getattr(mem, "memory_type", "general")
            key = str(mem_type)
            clusters.setdefault(key, []).append(mem)

        result.clusters_found = len(clusters)
        return clusters

    async def _consolidate(self, clusters: dict, result: DreamResult) -> list:
        """Phase 3: Merge duplicates, extract insights."""
        insights = []

        for cluster_key, memories in clusters.items():
            if len(memories) < 2:
                continue

            # Find near-duplicates (same content prefix)
            seen_content = {}
            for mem in memories:
                content = getattr(mem, "content", str(mem))[:200]
                if content in seen_content:
                    try:
                        await self._safe_delete(getattr(seen_content[content], "id", None))
                        result.duplicates_merged += 1
                    except Exception:
                        pass
                else:
                    seen_content[content] = mem

            # Extract insight from cluster
            if len(memories) >= 3:
                insight = f"Pattern in {cluster_key}: {len(memories)} related memories"
                insights.append(insight)
                result.insights_extracted += 1

        if insights:
            await self._append_learnings(insights)
            result.learnings_updated = len(insights)

        return insights

    async def _prune(self, result: DreamResult):
        """Phase 4: Archive old, low-value memories."""
        if not self.memory or not hasattr(self.memory, "get_since"):
            return

        cutoff = datetime.utcnow() - timedelta(days=30)
        try:
            old_memories = await self._get_old_memories(cutoff)
            pruned = 0
            for mem in old_memories:
                mem_type = getattr(mem, "memory_type", None)
                # Never prune objectives or client context
                if mem_type and str(mem_type) in (
                    "OBJECTIVE",
                    "CLIENT_CONTEXT",
                    "MemoryType.OBJECTIVE",
                    "MemoryType.CLIENT_CONTEXT",
                ):
                    continue
                try:
                    await self._safe_delete(getattr(mem, "id", None))
                    pruned += 1
                except Exception:
                    pass
            result.memories_pruned = pruned
        except Exception as e:
            logger.debug(f"Prune phase skipped: {e}")

    async def _get_recent_memories(self, since: datetime) -> list:
        """Get memories since a datetime."""
        if hasattr(self.memory, "get_since"):
            method = self.memory.get_since
            if asyncio.iscoroutinefunction(method):
                return await method(since)
            return method(since)
        return []

    async def _get_old_memories(self, before: datetime) -> list:
        """Get memories older than a datetime."""
        all_mems = await self._get_recent_memories(datetime(2020, 1, 1))
        return [
            m
            for m in all_mems
            if hasattr(m, "timestamp") and m.timestamp < before
        ]

    async def _safe_delete(self, entry_id):
        """Safely delete a memory entry."""
        if entry_id and self.memory and hasattr(self.memory, "delete"):
            if asyncio.iscoroutinefunction(self.memory.delete):
                await self.memory.delete(entry_id)
            else:
                self.memory.delete(entry_id)

    async def _append_learnings(self, insights: list):
        """Append insights to learnings.md."""
        self.learnings_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

        entry = f"\n## Dream Cycle -- {timestamp}\n"
        for insight in insights:
            entry += f"- {insight}\n"

        with open(self.learnings_path, "a") as f:
            f.write(entry)
