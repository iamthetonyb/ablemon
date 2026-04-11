"""Tests for D16 — Profile-Scoped Memory.

Covers: profile_id on HybridMemory, store with profile, search filtering,
instance-level vs per-call profile, profile isolation.
"""

import pytest
from unittest.mock import MagicMock, patch

from able.memory.hybrid_memory import (
    HybridMemory,
    MemoryEntry,
    MemoryType,
    SearchResult,
)


@pytest.fixture
def mock_memory():
    """Create HybridMemory with mocked backends."""
    with patch("able.memory.db.sqlite_store.SQLiteStore") as MockDB, \
         patch("able.memory.embeddings.vector_store.VectorStore") as MockVec:

        mock_db = MockDB.return_value
        mock_vec = MockVec.return_value
        mock_vec.compute_embedding.return_value = None

        # Mock insert to accept any entry
        mock_db.insert.return_value = True
        # Mock search to return empty by default
        mock_db.search.return_value = []

        mem = HybridMemory(use_v1_bridge=False)
        mem._mock_db = mock_db
        mem._mock_vec = mock_vec
        return mem


# ── Profile on constructor ────────────────────────────────────


class TestProfileConstructor:

    def test_default_no_profile(self, mock_memory):
        assert mock_memory.profile_id is None

    def test_set_profile_on_init(self):
        with patch("able.memory.db.sqlite_store.SQLiteStore"), \
             patch("able.memory.embeddings.vector_store.VectorStore"):
            mem = HybridMemory(use_v1_bridge=False, profile_id="user_123")
            assert mem.profile_id == "user_123"


# ── Store with profile ────────────────────────────────────────


class TestStoreWithProfile:

    def test_store_adds_profile_to_metadata(self, mock_memory):
        mock_memory.profile_id = "user_abc"
        mock_memory.store(
            content="test memory",
            memory_type=MemoryType.LEARNING,
        )
        # Check the entry passed to db.insert
        call_args = mock_memory._mock_db.insert.call_args
        entry = call_args[0][0]
        assert entry.metadata.get("profile_id") == "user_abc"

    def test_store_per_call_profile_overrides(self, mock_memory):
        mock_memory.profile_id = "default_user"
        mock_memory.store(
            content="test",
            memory_type=MemoryType.LEARNING,
            profile_id="specific_user",
        )
        entry = mock_memory._mock_db.insert.call_args[0][0]
        assert entry.metadata["profile_id"] == "specific_user"

    def test_store_no_profile_no_metadata_key(self, mock_memory):
        mock_memory.store(
            content="test",
            memory_type=MemoryType.LEARNING,
        )
        entry = mock_memory._mock_db.insert.call_args[0][0]
        assert "profile_id" not in entry.metadata

    def test_store_preserves_existing_metadata(self, mock_memory):
        mock_memory.profile_id = "user_x"
        mock_memory.store(
            content="test",
            memory_type=MemoryType.LEARNING,
            metadata={"source": "telegram"},
        )
        entry = mock_memory._mock_db.insert.call_args[0][0]
        assert entry.metadata["source"] == "telegram"
        assert entry.metadata["profile_id"] == "user_x"


# ── Search with profile ──────────────────────────────────────


class TestSearchWithProfile:

    def _make_result(self, profile=None, score=0.8):
        meta = {}
        if profile:
            meta["profile_id"] = profile
        entry = MemoryEntry(
            id="test_id",
            content="test content",
            memory_type=MemoryType.LEARNING,
            timestamp=__import__("datetime").datetime.utcnow(),
            metadata=meta,
        )
        return (entry, score)

    def test_search_filters_by_profile(self, mock_memory):
        mock_memory.profile_id = "user_a"
        # DB returns entries from different profiles
        mock_memory._mock_db.search.return_value = [
            self._make_result(profile="user_a"),
            self._make_result(profile="user_b"),
        ]
        results = mock_memory.search("test query")
        # Only user_a should remain
        assert all(
            r.entry.metadata.get("profile_id") == "user_a"
            for r in results
        )

    def test_search_no_profile_returns_all(self, mock_memory):
        mock_memory._mock_db.search.return_value = [
            self._make_result(profile="user_a"),
            self._make_result(profile="user_b"),
        ]
        results = mock_memory.search("test query")
        assert len(results) == 2

    def test_search_per_call_profile(self, mock_memory):
        mock_memory._mock_db.search.return_value = [
            self._make_result(profile="user_a"),
            self._make_result(profile="user_b"),
        ]
        results = mock_memory.search("test query", profile_id="user_b")
        assert all(
            r.entry.metadata.get("profile_id") == "user_b"
            for r in results
        )


# ── Profile isolation ─────────────────────────────────────────


class TestProfileIsolation:

    def test_different_profiles_are_isolated(self):
        with patch("able.memory.db.sqlite_store.SQLiteStore") as MockDB, \
             patch("able.memory.embeddings.vector_store.VectorStore") as MockVec:
            MockVec.return_value.compute_embedding.return_value = None
            MockDB.return_value.insert.return_value = True
            MockDB.return_value.search.return_value = []

            mem_a = HybridMemory(use_v1_bridge=False, profile_id="alice")
            mem_b = HybridMemory(use_v1_bridge=False, profile_id="bob")

            assert mem_a.profile_id != mem_b.profile_id
