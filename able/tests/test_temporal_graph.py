"""Tests for able.memory.temporal_graph — Temporal Knowledge Graph (C2)."""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from able.memory.temporal_graph import TemporalKnowledgeGraph, Triple


@pytest.fixture
def graph(tmp_path):
    return TemporalKnowledgeGraph(db_path=tmp_path / "test_kg.db")


# ── add_triple ────────────────��─────────────────────────────────

def test_add_triple_returns_id(graph):
    rid = graph.add_triple("alice", "role", "engineer")
    assert isinstance(rid, int)
    assert rid > 0


def test_add_triple_auto_timestamp(graph):
    graph.add_triple("alice", "role", "engineer")
    facts = graph.query_entity("alice")
    assert len(facts) == 1
    assert facts[0].valid_from  # non-empty


def test_add_triple_explicit_timestamp(graph):
    ts = "2026-01-15T10:00:00+00:00"
    graph.add_triple("bob", "joined", "2026-01-15", valid_from=ts)
    facts = graph.query_entity("bob")
    assert facts[0].valid_from == ts


def test_add_triple_with_metadata(graph):
    graph.add_triple("project_x", "status", "active", metadata={"priority": "high"})
    facts = graph.query_entity("project_x")
    assert facts[0].metadata == {"priority": "high"}


def test_add_triple_replaces_same_predicate(graph):
    graph.add_triple("alice", "rate", "$100/hr")
    graph.add_triple("alice", "rate", "$150/hr")
    current = graph.query_entity("alice")
    assert len(current) == 1
    assert current[0].object == "$150/hr"


def test_add_triple_preserves_different_predicates(graph):
    graph.add_triple("alice", "role", "engineer")
    graph.add_triple("alice", "rate", "$100/hr")
    facts = graph.query_entity("alice")
    assert len(facts) == 2


# ── invalidate ──────────────────────────────────────────────────

def test_invalidate_specific_object(graph):
    graph.add_triple("alice", "skill", "python")
    graph.add_triple("alice", "skill", "rust")
    # With auto-replace, only "rust" is current
    count = graph.invalidate("alice", "skill", "rust")
    assert count == 1
    current = graph.query_entity("alice")
    assert len(current) == 0  # both expired


def test_invalidate_all_for_predicate(graph):
    graph.add_triple("alice", "role", "engineer")
    count = graph.invalidate("alice", "role")
    assert count == 1
    assert graph.query_entity("alice") == []


def test_invalidate_nonexistent_returns_zero(graph):
    count = graph.invalidate("nobody", "nothing")
    assert count == 0


# ── query_entity ───────────────────────────────��────────────────

def test_query_entity_as_subject(graph):
    graph.add_triple("alice", "works_at", "acme")
    facts = graph.query_entity("alice")
    assert len(facts) == 1
    assert facts[0].predicate == "works_at"


def test_query_entity_as_object(graph):
    graph.add_triple("alice", "works_at", "acme")
    facts = graph.query_entity("acme")
    assert len(facts) == 1
    assert facts[0].subject == "alice"


def test_query_entity_as_of_past(graph):
    t1 = "2026-01-01T00:00:00+00:00"
    t2 = "2026-06-01T00:00:00+00:00"
    graph.add_triple("alice", "rate", "$100/hr", valid_from=t1)
    graph.add_triple("alice", "rate", "$150/hr", valid_from=t2)

    # Query as of March — should see $100
    march = "2026-03-01T00:00:00+00:00"
    facts = graph.query_entity("alice", as_of=march)
    assert len(facts) == 1
    assert facts[0].object == "$100/hr"


def test_query_entity_as_of_future(graph):
    t1 = "2026-01-01T00:00:00+00:00"
    t2 = "2026-06-01T00:00:00+00:00"
    graph.add_triple("alice", "rate", "$100/hr", valid_from=t1)
    graph.add_triple("alice", "rate", "$150/hr", valid_from=t2)

    # Query as of September — should see $150
    sept = "2026-09-01T00:00:00+00:00"
    facts = graph.query_entity("alice", as_of=sept)
    assert len(facts) == 1
    assert facts[0].object == "$150/hr"


def test_query_entity_include_expired(graph):
    graph.add_triple("alice", "rate", "$100/hr")
    graph.add_triple("alice", "rate", "$150/hr")
    # include_expired returns both the current and the expired version
    all_facts = graph.query_entity("alice", include_expired=True)
    assert len(all_facts) == 2


def test_query_entity_empty(graph):
    assert graph.query_entity("nobody") == []


# ── query_predicate ──────────────────────────���──────────────────

def test_query_predicate_current(graph):
    graph.add_triple("alice", "role", "engineer")
    graph.add_triple("bob", "role", "designer")
    facts = graph.query_predicate("role")
    assert len(facts) == 2


def test_query_predicate_include_expired(graph):
    graph.add_triple("alice", "role", "intern")
    graph.add_triple("alice", "role", "engineer")
    facts = graph.query_predicate("role", current_only=False)
    assert len(facts) == 2  # intern (expired) + engineer (current)


# ── find_connected (BFS) ────────────────────────────────────────

def test_find_connected_depth_1(graph):
    graph.add_triple("alice", "works_at", "acme")
    graph.add_triple("acme", "industry", "fintech")

    connected = graph.find_connected("alice", max_depth=1)
    assert "alice" in connected
    # depth=1 reaches acme via alice→acme edge
    assert "acme" in connected


def test_find_connected_depth_2(graph):
    graph.add_triple("alice", "works_at", "acme")
    graph.add_triple("acme", "industry", "fintech")
    graph.add_triple("fintech", "regulator", "SEC")

    connected = graph.find_connected("alice", max_depth=2)
    assert "alice" in connected
    assert "acme" in connected
    assert "fintech" in connected


def test_find_connected_depth_0(graph):
    graph.add_triple("alice", "works_at", "acme")
    connected = graph.find_connected("alice", max_depth=0)
    assert "alice" in connected
    assert "acme" not in connected


def test_find_connected_no_cycles(graph):
    """Circular references don't cause infinite loops."""
    graph.add_triple("a", "knows", "b")
    graph.add_triple("b", "knows", "c")
    graph.add_triple("c", "knows", "a")
    connected = graph.find_connected("a", max_depth=5)
    assert set(connected.keys()) == {"a", "b", "c"}


# ── search (FTS5) ──────────────────────────────────────────────

def test_search_by_subject(graph):
    graph.add_triple("machine_learning", "used_by", "team_alpha")
    results = graph.search("machine_learning")
    assert len(results) >= 1
    assert results[0].subject == "machine_learning"


def test_search_by_object(graph):
    graph.add_triple("alice", "expertise", "deep learning")
    results = graph.search("deep learning")
    assert len(results) >= 1


def test_search_limit(graph):
    for i in range(30):
        graph.add_triple(f"entity_{i}", "type", "test")
    results = graph.search("test", limit=5)
    assert len(results) <= 5


# ── get_history ─────────────────────────────────────────────────

def test_get_history_shows_all_versions(graph):
    t1 = "2026-01-01T00:00:00+00:00"
    t2 = "2026-06-01T00:00:00+00:00"
    t3 = "2026-12-01T00:00:00+00:00"
    graph.add_triple("alice", "rate", "$100/hr", valid_from=t1)
    graph.add_triple("alice", "rate", "$120/hr", valid_from=t2)
    graph.add_triple("alice", "rate", "$150/hr", valid_from=t3)

    history = graph.get_history("alice", "rate")
    assert len(history) == 3
    # Newest first
    assert history[0].object == "$150/hr"
    assert history[2].object == "$100/hr"


def test_get_history_empty(graph):
    assert graph.get_history("nobody", "rate") == []


# ── stats ─────────────────────────────────��───────────────────��─

def test_stats_empty(graph):
    s = graph.stats()
    assert s["total_triples"] == 0
    assert s["current_triples"] == 0


def test_stats_counts(graph):
    graph.add_triple("alice", "role", "engineer")
    graph.add_triple("bob", "role", "designer")
    graph.add_triple("alice", "rate", "$100/hr")
    s = graph.stats()
    assert s["total_triples"] == 3
    assert s["current_triples"] == 3
    assert s["unique_subjects"] == 2
    assert s["unique_predicates"] == 2


def test_stats_expired_tracked(graph):
    graph.add_triple("alice", "rate", "$100/hr")
    graph.add_triple("alice", "rate", "$150/hr")  # expires the first
    s = graph.stats()
    assert s["total_triples"] == 2
    assert s["current_triples"] == 1


# ── prune_stale ─────────────────────────────────────────────────

def test_prune_stale_archives_old(graph):
    old_time = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    graph.add_triple("old_fact", "status", "expired", valid_from=old_time)
    graph.invalidate("old_fact", "status")

    # Force the valid_to to be old enough
    conn = graph._connect()
    conn.execute(
        "UPDATE triples SET valid_to = ? WHERE subject = 'old_fact'",
        (old_time,),
    )
    conn.commit()
    conn.close()

    archived = graph.prune_stale(days=90)
    assert archived == 1
    assert graph.query_entity("old_fact", include_expired=True) == []


def test_prune_stale_keeps_current(graph):
    graph.add_triple("current_fact", "status", "active")
    archived = graph.prune_stale(days=90)
    assert archived == 0
    assert len(graph.query_entity("current_fact")) == 1


# ── Triple dataclass ────────────────────────────────────────────

def test_triple_is_current():
    t = Triple(subject="a", predicate="b", object="c", valid_from="2026-01-01")
    assert t.is_current is True

    t2 = Triple(subject="a", predicate="b", object="c",
                valid_from="2026-01-01", valid_to="2026-06-01")
    assert t2.is_current is False


def test_triple_as_dict():
    t = Triple(subject="a", predicate="b", object="c",
               valid_from="2026-01-01", id=42, metadata={"k": "v"})
    d = t.as_dict()
    assert d["id"] == 42
    assert d["subject"] == "a"
    assert d["metadata"] == {"k": "v"}
    assert d["valid_to"] is None
