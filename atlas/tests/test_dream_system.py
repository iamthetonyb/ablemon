"""Tests for the ATLAS Dream System (memory consolidation)."""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from atlas.core.agi.dream_system import DreamConsolidator, DreamGates, DreamResult


# ── Helpers ───────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class FakeMemory:
    """Minimal stand-in for a MemoryEntry."""

    id: str = ""
    content: str = ""
    memory_type: str = "conversation"
    timestamp: datetime = field(default_factory=_now)


class MockMemoryStore:
    """In-memory mock that exposes get_since / delete / count."""

    def __init__(self, entries=None):
        self.entries = list(entries or [])
        self.deleted_ids: list[str] = []

    def get_since(self, since: datetime, memory_types=None):
        return [e for e in self.entries if e.timestamp >= since]

    def delete(self, entry_id: str):
        self.deleted_ids.append(entry_id)
        self.entries = [e for e in self.entries if e.id != entry_id]

    def count(self):
        return len(self.entries)


def _make_entries(n: int, prefix="mem", age_hours=1, mem_type="conversation"):
    """Create *n* FakeMemory entries."""
    return [
        FakeMemory(
            id=f"{prefix}-{i}",
            content=f"content for {prefix}-{i}",
            memory_type=mem_type,
            timestamp=_now() - timedelta(hours=age_hours),
        )
        for i in range(n)
    ]


# ── Tests ─────────────────────────────────────────────────────────


class TestDreamGates:
    def test_defaults(self):
        g = DreamGates()
        assert g.min_hours_since_last == 24.0
        assert g.min_new_memories == 5
        assert g.lock_path == "data/.dream_lock"

    def test_custom(self):
        g = DreamGates(min_hours_since_last=12.0, min_new_memories=3)
        assert g.min_hours_since_last == 12.0
        assert g.min_new_memories == 3


class TestShouldRun:
    def test_no_lock_enough_memories(self, tmp_path):
        """No lock file + enough memories -> should run."""
        store = MockMemoryStore(_make_entries(10))
        gates = DreamGates(lock_path=str(tmp_path / "lock"))
        dreamer = DreamConsolidator(memory=store, gates=gates)
        dreamer._lock_path = tmp_path / "lock"

        ok, reason = asyncio.run(dreamer.should_run())
        assert ok is True
        assert "All gates" in reason

    def test_recent_lock_blocks(self, tmp_path):
        """Lock file written just now -> should NOT run."""
        lock = tmp_path / "lock"
        lock.write_text(str(time.time()))

        store = MockMemoryStore(_make_entries(10))
        gates = DreamGates(lock_path=str(lock))
        dreamer = DreamConsolidator(memory=store, gates=gates)
        dreamer._lock_path = lock

        ok, reason = asyncio.run(dreamer.should_run())
        assert ok is False
        assert "since last run" in reason

    def test_old_lock_passes(self, tmp_path):
        """Lock file written 25h ago -> gate 1 passes."""
        lock = tmp_path / "lock"
        lock.write_text(str(time.time() - 25 * 3600))

        store = MockMemoryStore(_make_entries(10))
        gates = DreamGates(lock_path=str(lock))
        dreamer = DreamConsolidator(memory=store, gates=gates)
        dreamer._lock_path = lock

        ok, _ = asyncio.run(dreamer.should_run())
        assert ok is True

    def test_too_few_memories(self, tmp_path):
        """Fewer than min_new_memories -> should NOT run."""
        store = MockMemoryStore(_make_entries(2))
        gates = DreamGates(lock_path=str(tmp_path / "lock"), min_new_memories=5)
        dreamer = DreamConsolidator(memory=store, gates=gates)
        dreamer._lock_path = tmp_path / "lock"

        ok, reason = asyncio.run(dreamer.should_run())
        assert ok is False
        assert "new memories" in reason


class TestRunCycle:
    def test_creates_lock_and_runs_phases(self, tmp_path):
        store = MockMemoryStore(_make_entries(6))
        gates = DreamGates(lock_path=str(tmp_path / "lock"))
        learnings = tmp_path / "learnings.md"

        dreamer = DreamConsolidator(memory=store, gates=gates, learnings_path=str(learnings))
        dreamer._lock_path = tmp_path / "lock"

        result = asyncio.run(dreamer.run_cycle())

        assert isinstance(result, DreamResult)
        assert result.memories_scanned == 6
        assert result.duration_seconds > 0
        assert dreamer._lock_path.exists()

    def test_no_memory_returns_empty_result(self, tmp_path):
        gates = DreamGates(lock_path=str(tmp_path / "lock"))
        dreamer = DreamConsolidator(memory=None, gates=gates)
        dreamer._lock_path = tmp_path / "lock"

        result = asyncio.run(dreamer.run_cycle())
        assert result.memories_scanned == 0


class TestConsolidateDedup:
    def test_duplicate_content_merged(self, tmp_path):
        """Two entries with identical content prefix -> one gets deleted."""
        now = _now()
        entries = [
            FakeMemory(id="a", content="duplicate content here", timestamp=now),
            FakeMemory(id="b", content="duplicate content here", timestamp=now),
            FakeMemory(id="c", content="unique content", timestamp=now),
        ]
        store = MockMemoryStore(entries)
        gates = DreamGates(lock_path=str(tmp_path / "lock"))

        dreamer = DreamConsolidator(memory=store, gates=gates, learnings_path=str(tmp_path / "l.md"))
        dreamer._lock_path = tmp_path / "lock"

        result = DreamResult()
        clusters = {"conversation": entries}
        asyncio.run(dreamer._consolidate(clusters, result))

        assert result.duplicates_merged == 1
        assert len(store.deleted_ids) == 1

    def test_unique_content_not_merged(self, tmp_path):
        now = _now()
        entries = [
            FakeMemory(id="a", content="alpha", timestamp=now),
            FakeMemory(id="b", content="beta", timestamp=now),
        ]
        store = MockMemoryStore(entries)
        gates = DreamGates(lock_path=str(tmp_path / "lock"))

        dreamer = DreamConsolidator(memory=store, gates=gates, learnings_path=str(tmp_path / "l.md"))
        dreamer._lock_path = tmp_path / "lock"

        result = DreamResult()
        clusters = {"conversation": entries}
        asyncio.run(dreamer._consolidate(clusters, result))

        assert result.duplicates_merged == 0


class TestPrune:
    def test_objective_type_never_pruned(self, tmp_path):
        """Entries with OBJECTIVE memory_type must survive pruning."""
        old = _now() - timedelta(days=60)
        entries = [
            FakeMemory(id="keep", content="obj", memory_type="OBJECTIVE", timestamp=old),
            FakeMemory(id="drop", content="chat", memory_type="conversation", timestamp=old),
        ]
        store = MockMemoryStore(entries)
        gates = DreamGates(lock_path=str(tmp_path / "lock"))

        dreamer = DreamConsolidator(memory=store, gates=gates)
        dreamer._lock_path = tmp_path / "lock"

        result = DreamResult()
        asyncio.run(dreamer._prune(result))

        assert "keep" not in store.deleted_ids
        assert "drop" in store.deleted_ids
        assert result.memories_pruned == 1

    def test_client_context_never_pruned(self, tmp_path):
        old = _now() - timedelta(days=60)
        entries = [
            FakeMemory(id="ctx", content="ctx", memory_type="CLIENT_CONTEXT", timestamp=old),
        ]
        store = MockMemoryStore(entries)
        gates = DreamGates(lock_path=str(tmp_path / "lock"))

        dreamer = DreamConsolidator(memory=store, gates=gates)
        dreamer._lock_path = tmp_path / "lock"

        result = DreamResult()
        asyncio.run(dreamer._prune(result))

        assert "ctx" not in store.deleted_ids
        assert result.memories_pruned == 0


class TestAppendLearnings:
    def test_writes_to_file(self, tmp_path):
        learnings = tmp_path / "learnings.md"
        dreamer = DreamConsolidator(learnings_path=str(learnings))

        asyncio.run(dreamer._append_learnings(["insight one", "insight two"]))

        text = learnings.read_text()
        assert "Dream Cycle" in text
        assert "insight one" in text
        assert "insight two" in text

    def test_appends_without_overwrite(self, tmp_path):
        learnings = tmp_path / "learnings.md"
        learnings.write_text("# Existing content\n")

        dreamer = DreamConsolidator(learnings_path=str(learnings))
        asyncio.run(dreamer._append_learnings(["new insight"]))

        text = learnings.read_text()
        assert "Existing content" in text
        assert "new insight" in text
