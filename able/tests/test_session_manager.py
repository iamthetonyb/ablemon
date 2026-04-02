#!/usr/bin/env python3
"""
Tests for the centralized session state manager.

Covers: creation, update, cache behavior, expiry, stats, persistence.
"""

import os
import sys
import time

import pytest

# Ensure able package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from able.core.session.session_manager import Session, SessionManager


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_db(tmp_path):
    """Yield a temporary DB path that gets cleaned up."""
    return str(tmp_path / "test_sessions.db")


@pytest.fixture
def mgr(tmp_db):
    """Session manager with a temp database."""
    return SessionManager(db_path=tmp_db, ttl_seconds=1800, cache_max=10)


# ═══════════════════════════════════════════════════════════════
# SESSION DATACLASS
# ═══════════════════════════════════════════════════════════════


class TestSessionDataclass:
    def test_defaults(self):
        s = Session()
        assert s.messages == 0
        assert s.tools_used == []
        assert s.complexity_scores == []
        assert s.total_input_tokens == 0
        assert s.cost_usd == 0.0
        assert s.tenant_id == "default"
        assert s.conversation_id  # UUID generated

    def test_avg_complexity_empty(self):
        s = Session()
        assert s.avg_complexity == 0.0

    def test_avg_complexity(self):
        s = Session(complexity_scores=[0.2, 0.4, 0.6])
        assert abs(s.avg_complexity - 0.4) < 1e-9

    def test_total_tokens(self):
        s = Session(total_input_tokens=100, total_output_tokens=200)
        assert s.total_tokens == 300


# ═══════════════════════════════════════════════════════════════
# GET OR CREATE
# ═══════════════════════════════════════════════════════════════


class TestGetOrCreate:
    def test_creates_new_session(self, mgr):
        s = mgr.get_or_create("conv-1", tenant_id="acme")
        assert s.conversation_id == "conv-1"
        assert s.tenant_id == "acme"
        assert s.messages == 0

    def test_returns_existing(self, mgr):
        s1 = mgr.get_or_create("conv-1")
        mgr.update("conv-1", input_tokens=50)
        s2 = mgr.get_or_create("conv-1")
        assert s2.messages == 1
        assert s2.total_input_tokens == 50

    def test_default_tenant(self, mgr):
        s = mgr.get_or_create("conv-2")
        assert s.tenant_id == "default"


# ═══════════════════════════════════════════════════════════════
# UPDATE
# ═══════════════════════════════════════════════════════════════


class TestUpdate:
    def test_increments_messages(self, mgr):
        mgr.get_or_create("conv-1")
        s = mgr.update("conv-1")
        assert s.messages == 1
        s = mgr.update("conv-1")
        assert s.messages == 2

    def test_appends_tools(self, mgr):
        mgr.get_or_create("conv-1")
        mgr.update("conv-1", tools_used=["web_search"])
        s = mgr.update("conv-1", tools_used=["github_create_pr", "shell"])
        assert s.tools_used == ["web_search", "github_create_pr", "shell"]

    def test_appends_complexity_scores(self, mgr):
        mgr.get_or_create("conv-1")
        mgr.update("conv-1", complexity_score=0.3)
        s = mgr.update("conv-1", complexity_score=0.7)
        assert s.complexity_scores == [0.3, 0.7]
        assert abs(s.avg_complexity - 0.5) < 1e-9

    def test_accumulates_tokens_and_cost(self, mgr):
        mgr.get_or_create("conv-1")
        mgr.update("conv-1", input_tokens=100, output_tokens=200, cost_usd=0.01)
        s = mgr.update("conv-1", input_tokens=50, output_tokens=75, cost_usd=0.005)
        assert s.total_input_tokens == 150
        assert s.total_output_tokens == 275
        assert abs(s.cost_usd - 0.015) < 1e-9

    def test_metadata_patch(self, mgr):
        mgr.get_or_create("conv-1")
        mgr.update("conv-1", metadata_patch={"channel": "telegram"})
        s = mgr.update("conv-1", metadata_patch={"user_id": "u42"})
        assert s.metadata == {"channel": "telegram", "user_id": "u42"}

    def test_updates_timestamp(self, mgr):
        mgr.get_or_create("conv-1")
        s1 = mgr.get_or_create("conv-1")
        ts_before = s1.updated_at
        # Small delay to ensure timestamp differs
        time.sleep(0.01)
        s2 = mgr.update("conv-1")
        assert s2.updated_at >= ts_before


# ═══════════════════════════════════════════════════════════════
# CACHE BEHAVIOR
# ═══════════════════════════════════════════════════════════════


class TestCache:
    def test_cache_hit(self, mgr):
        mgr.get_or_create("conv-1")
        # Second call should come from cache
        s = mgr.get_or_create("conv-1")
        assert s.conversation_id == "conv-1"

    def test_cache_eviction(self, tmp_db):
        mgr = SessionManager(db_path=tmp_db, cache_max=3)
        for i in range(5):
            mgr.get_or_create(f"conv-{i}")

        # Oldest should be evicted from cache but still in DB
        assert mgr._cache_get("conv-0") is None
        assert mgr._cache_get("conv-1") is None
        # Most recent should still be cached
        assert mgr._cache_get("conv-4") is not None

        # Fetching evicted session should reload from DB
        s = mgr.get_or_create("conv-0")
        assert s.conversation_id == "conv-0"


# ═══════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════


class TestPersistence:
    def test_survives_new_manager_instance(self, tmp_db):
        mgr1 = SessionManager(db_path=tmp_db)
        mgr1.get_or_create("conv-1")
        mgr1.update("conv-1", input_tokens=100, tools_used=["search"])

        # New manager, same DB — should find the session
        mgr2 = SessionManager(db_path=tmp_db)
        s = mgr2.get_or_create("conv-1")
        assert s.messages == 1
        assert s.total_input_tokens == 100
        assert s.tools_used == ["search"]


# ═══════════════════════════════════════════════════════════════
# EXPIRY
# ═══════════════════════════════════════════════════════════════


class TestExpiry:
    def test_expire_stale(self, tmp_db):
        mgr = SessionManager(db_path=tmp_db, ttl_seconds=0)
        mgr.get_or_create("conv-old")
        # TTL=0 means everything is immediately stale
        time.sleep(0.01)
        expired = mgr.expire_stale()
        assert expired == 1
        assert mgr.get("conv-old") is None

    def test_no_expire_fresh(self, tmp_db):
        mgr = SessionManager(db_path=tmp_db, ttl_seconds=3600)
        mgr.get_or_create("conv-fresh")
        expired = mgr.expire_stale()
        assert expired == 0
        assert mgr.get("conv-fresh") is not None

    def test_expire_removes_from_cache(self, tmp_db):
        mgr = SessionManager(db_path=tmp_db, ttl_seconds=0)
        mgr.get_or_create("conv-1")
        assert mgr._cache_get("conv-1") is not None
        time.sleep(0.01)
        mgr.expire_stale()
        assert mgr._cache_get("conv-1") is None


# ═══════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════


class TestStats:
    def test_empty_stats(self, mgr):
        stats = mgr.get_stats()
        assert stats["total_sessions"] == 0
        assert stats["total_messages"] == 0
        assert stats["total_cost_usd"] == 0.0

    def test_aggregate_stats(self, mgr):
        mgr.get_or_create("conv-1")
        mgr.update("conv-1", input_tokens=100, output_tokens=200, cost_usd=0.01)

        mgr.get_or_create("conv-2")
        mgr.update("conv-2", input_tokens=50, output_tokens=75, cost_usd=0.005)

        stats = mgr.get_stats()
        assert stats["total_sessions"] == 2
        assert stats["total_messages"] == 2
        assert stats["total_input_tokens"] == 150
        assert stats["total_output_tokens"] == 275
        assert abs(stats["total_cost_usd"] - 0.015) < 1e-9


# ═══════════════════════════════════════════════════════════════
# GET
# ═══════════════════════════════════════════════════════════════


class TestGet:
    def test_get_existing(self, mgr):
        mgr.get_or_create("conv-1")
        s = mgr.get("conv-1")
        assert s is not None
        assert s.conversation_id == "conv-1"

    def test_get_nonexistent(self, mgr):
        assert mgr.get("does-not-exist") is None
