"""Tests for the distillation data models and SQLite store."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import pytest

from able.core.distillation.models import (
    ConversationRecord,
    CorpusTier,
    DistillationPair,
    ThinkingTrace,
)
from able.core.distillation.store import DistillationStore


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture()
def tmp_db(tmp_path):
    """Provide a temporary database path."""
    return str(tmp_path / "test_distillation.db")


@pytest.fixture()
def store(tmp_db):
    return DistillationStore(db_path=tmp_db)


def _make_record(**overrides) -> ConversationRecord:
    defaults = dict(
        id=str(uuid.uuid4()),
        source="claude_code",
        messages=[
            {"role": "user", "content": "Explain quicksort"},
            {"role": "assistant", "content": "Quicksort is a divide-and-conquer..."},
        ],
        model="claude-opus-4-6",
        tier=4,
        domain="coding",
        quality_score=0.95,
        tenant_id="default",
    )
    defaults.update(overrides)
    return ConversationRecord(**defaults)


def _make_pair(**overrides) -> DistillationPair:
    defaults = dict(
        id=str(uuid.uuid4()),
        prompt="Explain quicksort",
        gold_response="Quicksort is a divide-and-conquer algorithm...",
        gold_model="claude-opus-4-6",
        gold_thinking="The user wants a concise explanation...",
        domain="coding",
        quality_score=0.92,
        tenant_id="default",
        tags=["algorithms", "sorting"],
    )
    defaults.update(overrides)
    return DistillationPair(**defaults)


# ── DB Initialization ──────────────────────────────────────────


class TestStoreInit:
    def test_creates_db_and_tables(self, tmp_db):
        store = DistillationStore(db_path=tmp_db)
        assert os.path.exists(tmp_db)
        # Both tables should exist
        assert store.count("conversation_records") == 0
        assert store.count("distillation_pairs") == 0

    def test_wal_mode_enabled(self, tmp_db):
        import sqlite3

        DistillationStore(db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


# ── ConversationRecord Round-Trip ──────────────────────────────


class TestConversationRecord:
    def test_content_hash_auto_generated(self):
        record = _make_record()
        assert record.content_hash != ""
        assert len(record.content_hash) == 64  # SHA256 hex

    def test_save_and_get(self, store):
        record = _make_record()
        assert store.save_record(record) is True

        results = store.get_records()
        assert len(results) == 1
        got = results[0]
        assert got.id == record.id
        assert got.source == record.source
        assert got.messages == record.messages
        assert got.model == record.model
        assert got.tier == record.tier
        assert got.domain == record.domain
        assert got.quality_score == record.quality_score
        assert got.tenant_id == record.tenant_id
        assert got.content_hash == record.content_hash

    def test_save_with_thinking_trace(self, store):
        trace = ThinkingTrace(
            model="claude-opus-4-6",
            raw_thinking="Let me think step by step...",
            stripped_output="The answer is 42.",
            extraction_method="regex",
        )
        record = _make_record(thinking_trace=trace)
        store.save_record(record)

        got = store.get_records()[0]
        assert got.thinking_trace is not None
        assert got.thinking_trace.model == "claude-opus-4-6"
        assert got.thinking_trace.raw_thinking == "Let me think step by step..."
        assert got.thinking_trace.stripped_output == "The answer is 42."
        assert got.thinking_trace.extraction_method == "regex"

    def test_save_with_metadata(self, store):
        record = _make_record(metadata={"eval_id": "abc123", "pass": True})
        store.save_record(record)

        got = store.get_records()[0]
        assert got.metadata == {"eval_id": "abc123", "pass": True}


# ── DistillationPair Round-Trip ────────────────────────────────


class TestDistillationPair:
    def test_content_hash_auto_generated(self):
        pair = _make_pair()
        assert pair.content_hash != ""
        assert len(pair.content_hash) == 64

    def test_save_and_get(self, store):
        pair = _make_pair()
        assert store.save_pair(pair) is True

        results = store.get_pairs()
        assert len(results) == 1
        got = results[0]
        assert got.id == pair.id
        assert got.prompt == pair.prompt
        assert got.gold_response == pair.gold_response
        assert got.gold_model == pair.gold_model
        assert got.gold_thinking == pair.gold_thinking
        assert got.domain == pair.domain
        assert got.quality_score == pair.quality_score
        assert got.tenant_id == pair.tenant_id
        assert got.tags == pair.tags
        assert got.content_hash == pair.content_hash

    def test_save_without_thinking(self, store):
        pair = _make_pair(gold_thinking=None)
        store.save_pair(pair)

        got = store.get_pairs()[0]
        assert got.gold_thinking is None


# ── Deduplication ──────────────────────────────────────────────


class TestDeduplication:
    def test_duplicate_record_returns_false(self, store):
        record = _make_record()
        assert store.save_record(record) is True
        # Same content_hash should fail
        dup = _make_record(id=str(uuid.uuid4()))
        dup.content_hash = record.content_hash
        assert store.save_record(dup) is False
        assert store.count("conversation_records") == 1

    def test_duplicate_pair_returns_false(self, store):
        pair = _make_pair()
        assert store.save_pair(pair) is True
        dup = _make_pair(id=str(uuid.uuid4()))
        dup.content_hash = pair.content_hash
        assert store.save_pair(dup) is False
        assert store.count("distillation_pairs") == 1

    def test_different_content_different_hash(self, store):
        p1 = _make_pair(prompt="What is quicksort?", gold_response="A sorting algo")
        p2 = _make_pair(prompt="What is mergesort?", gold_response="A different sorting algo")
        assert store.save_pair(p1) is True
        assert store.save_pair(p2) is True
        assert store.count("distillation_pairs") == 2


# ── Filtering ──────────────────────────────────────────────────


class TestFiltering:
    def test_domain_filter(self, store):
        store.save_pair(_make_pair(domain="coding"))
        store.save_pair(_make_pair(domain="security", prompt="Find vulns", gold_response="XSS..."))
        store.save_pair(_make_pair(domain="coding", prompt="Binary search", gold_response="..."))

        coding = store.get_pairs(domain="coding")
        assert len(coding) == 2
        security = store.get_pairs(domain="security")
        assert len(security) == 1

    def test_quality_filter(self, store):
        store.save_pair(_make_pair(quality_score=0.3, prompt="low q", gold_response="bad"))
        store.save_pair(_make_pair(quality_score=0.7, prompt="med q", gold_response="ok"))
        store.save_pair(_make_pair(quality_score=0.95, prompt="high q", gold_response="great"))

        high = store.get_pairs(min_quality=0.8)
        assert len(high) == 1
        assert high[0].quality_score == 0.95

        mid = store.get_pairs(min_quality=0.5)
        assert len(mid) == 2

    def test_record_domain_filter(self, store):
        store.save_record(_make_record(domain="coding"))
        store.save_record(
            _make_record(
                domain="security",
                messages=[{"role": "user", "content": "audit this"}],
            )
        )

        coding = store.get_records(domain="coding")
        assert len(coding) == 1
        assert coding[0].domain == "coding"

    def test_record_since_filter(self, store):
        old = _make_record(
            timestamp=datetime(2025, 1, 1),
            messages=[{"role": "user", "content": "old msg"}],
        )
        recent = _make_record(timestamp=datetime(2026, 3, 20))
        store.save_record(old)
        store.save_record(recent)

        results = store.get_records(since=datetime(2026, 1, 1))
        assert len(results) == 1


# ── Tenant Isolation ───────────────────────────────────────────


class TestTenantIsolation:
    def test_records_isolated_by_tenant(self, store):
        store.save_record(_make_record(tenant_id="alice"))
        store.save_record(
            _make_record(
                tenant_id="bob",
                messages=[{"role": "user", "content": "bob msg"}],
            )
        )

        alice = store.get_records(tenant_id="alice")
        assert len(alice) == 1
        bob = store.get_records(tenant_id="bob")
        assert len(bob) == 1

    def test_pairs_isolated_by_tenant(self, store):
        store.save_pair(_make_pair(tenant_id="alice"))
        store.save_pair(
            _make_pair(
                tenant_id="bob",
                prompt="Bob's prompt",
                gold_response="Bob's response",
            )
        )

        alice = store.get_pairs(tenant_id="alice")
        assert len(alice) == 1
        bob = store.get_pairs(tenant_id="bob")
        assert len(bob) == 1

    def test_count_by_tenant(self, store):
        store.save_pair(_make_pair(tenant_id="alice"))
        store.save_pair(
            _make_pair(
                tenant_id="bob",
                prompt="Bob prompt",
                gold_response="Bob response",
            )
        )

        assert store.count("distillation_pairs", tenant_id="alice") == 1
        assert store.count("distillation_pairs", tenant_id="bob") == 1
        assert store.count("distillation_pairs") == 2


# ── JSONL Export ───────────────────────────────────────────────


class TestExport:
    def test_export_jsonl(self, store, tmp_path):
        store.save_pair(_make_pair(quality_score=0.9))
        store.save_pair(
            _make_pair(
                quality_score=0.5,
                prompt="low q",
                gold_response="low resp",
            )
        )

        out = str(tmp_path / "export.jsonl")
        exported = store.export_jsonl(out, min_quality=0.8)
        assert exported == 1

        with open(out) as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert "conversations" in entry
        assert "metadata" in entry

    def test_export_with_system_prompt(self, store, tmp_path):
        store.save_pair(_make_pair(quality_score=0.95))

        out = str(tmp_path / "export_sys.jsonl")
        store.export_jsonl(out, min_quality=0.8, system_prompt="You are ABLE.")

        with open(out) as f:
            entry = json.loads(f.readline())
        assert entry["conversations"][0]["role"] == "system"
        assert entry["conversations"][0]["content"] == "You are ABLE."

    def test_export_creates_parent_dirs(self, store, tmp_path):
        store.save_pair(_make_pair(quality_score=0.95))
        out = str(tmp_path / "nested" / "deep" / "export.jsonl")
        exported = store.export_jsonl(out, min_quality=0.0)
        assert exported == 1
        assert os.path.exists(out)


# ── Stats ──────────────────────────────────────────────────────


class TestStats:
    def test_stats_empty(self, store):
        s = store.stats()
        assert s["total_records"] == 0
        assert s["total_pairs"] == 0
        assert s["by_domain"] == {}
        assert s["by_model"] == {}
        assert s["corpus_tier"] is None

    def test_stats_with_data(self, store):
        store.save_record(_make_record())
        store.save_pair(_make_pair(domain="coding", gold_model="claude-opus-4-6"))
        store.save_pair(
            _make_pair(
                domain="security",
                gold_model="gpt-5.4",
                prompt="audit this",
                gold_response="found vulns",
            )
        )

        s = store.stats()
        assert s["total_records"] == 1
        assert s["total_pairs"] == 2
        assert s["by_domain"]["coding"] == 1
        assert s["by_domain"]["security"] == 1
        assert s["by_model"]["claude-opus-4-6"] == 1
        assert s["by_model"]["gpt-5.4"] == 1

    def test_stats_tenant_scoped(self, store):
        store.save_pair(_make_pair(tenant_id="alice"))
        store.save_pair(
            _make_pair(
                tenant_id="bob",
                prompt="Bob p",
                gold_response="Bob r",
            )
        )

        alice_stats = store.stats(tenant_id="alice")
        assert alice_stats["total_pairs"] == 1


# ── Corpus Tier ────────────────────────────────────────────────


def _bulk_insert_pairs(store: DistillationStore, count: int) -> None:
    """Insert *count* stub pairs directly via SQL for threshold tests."""
    conn = store._connect()
    try:
        for i in range(count):
            conn.execute(
                """INSERT INTO distillation_pairs (
                    id, prompt, gold_response, gold_model, domain,
                    quality_score, tenant_id, tags, created_at, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    f"prompt {i}",
                    f"response {i}",
                    "test-model",
                    "coding",
                    0.9,
                    "default",
                    "[]",
                    datetime.now(timezone.utc).isoformat(),
                    f"hash_{i}",
                ),
            )
        conn.commit()
    finally:
        conn.close()


class TestCorpusTier:
    def test_below_seed(self, store):
        assert store.get_corpus_tier() is None

    def test_seed_threshold(self, store):
        _bulk_insert_pairs(store, 500)
        assert store.get_corpus_tier() == CorpusTier.SEED

    def test_growth_threshold(self, store):
        _bulk_insert_pairs(store, 2_000)
        assert store.get_corpus_tier() == CorpusTier.GROWTH

    def test_full_threshold(self, store):
        _bulk_insert_pairs(store, 10_000)
        assert store.get_corpus_tier() == CorpusTier.FULL


# ── ChatML Format ──────────────────────────────────────────────


class TestChatML:
    def test_to_chatml_basic(self):
        pair = _make_pair(gold_thinking=None)
        result = pair.to_chatml()
        assert len(result["conversations"]) == 2
        assert result["conversations"][0]["role"] == "user"
        assert result["conversations"][1]["role"] == "assistant"
        assert result["conversations"][1]["content"] == pair.gold_response

    def test_to_chatml_with_system_prompt(self):
        pair = _make_pair(gold_thinking=None)
        result = pair.to_chatml(system_prompt="You are a helpful AI.")
        assert len(result["conversations"]) == 3
        assert result["conversations"][0]["role"] == "system"
        assert result["conversations"][0]["content"] == "You are a helpful AI."

    def test_to_chatml_with_thinking(self):
        pair = _make_pair(gold_thinking="Step 1: parse the question...")
        result = pair.to_chatml()
        assistant_msg = result["conversations"][1]["content"]
        assert assistant_msg.startswith("<think>Step 1: parse the question...</think>")
        assert pair.gold_response in assistant_msg

    def test_to_chatml_metadata(self):
        pair = _make_pair(
            gold_model="claude-opus-4-6",
            domain="security",
            quality_score=0.88,
            tenant_id="acme",
        )
        result = pair.to_chatml()
        meta = result["metadata"]
        assert meta["source"] == "claude-opus-4-6"
        assert meta["domain"] == "security"
        assert meta["quality_score"] == 0.88
        assert meta["tenant_id"] == "acme"
