"""
Tests for interaction log extensions — multi-tenant fields, schema migration,
and standalone log_queries helper functions.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import uuid

import pytest

from atlas.core.routing.interaction_log import InteractionLogger, InteractionRecord
from atlas.core.routing.log_queries import (
    get_corpus_eligible,
    get_cost_by_tier,
    get_domain_accuracy,
    get_escalation_rate,
    get_failures_by_tier,
    get_scoring_drift,
    get_tenant_summary,
    get_wins_by_tier,
)


# ── Fixtures ────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path):
    """Temporary database path (not yet initialised)."""
    return str(tmp_path / "test_log.db")


@pytest.fixture()
def logger(db_path):
    """InteractionLogger pointing at a fresh temp database."""
    return InteractionLogger(db_path=db_path)


def _make_record(**overrides) -> InteractionRecord:
    """Build an InteractionRecord with sensible defaults + overrides."""
    defaults = dict(
        id=str(uuid.uuid4()),
        timestamp="2026-03-21T12:00:00+00:00",
        message_preview="test message",
        complexity_score=0.5,
        selected_tier=1,
        selected_provider="gpt-5.4-mini",
        domain="default",
        success=True,
        latency_ms=100.0,
        cost_usd=0.001,
        tenant_id="default",
    )
    defaults.update(overrides)
    return InteractionRecord(**defaults)


# ── Schema migration tests ──────────────────────────────────


class TestSchemaMigration:
    """Migration adds columns without losing data and is idempotent."""

    def test_migration_adds_new_columns(self, db_path):
        """After init, the new columns exist in the table."""
        logger = InteractionLogger(db_path=db_path)
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute("PRAGMA table_info(interaction_log)")
            col_names = {row[1] for row in cursor.fetchall()}
        finally:
            conn.close()

        for expected in (
            "tenant_id",
            "corpus_eligible",
            "corpus_version",
            "raw_input",
            "raw_output",
            "enrichment_level",
            "split_test_group",
            "thinking_tokens_preserved",
        ):
            assert expected in col_names, f"Column {expected!r} missing after migration"

    def test_migration_is_idempotent(self, db_path):
        """Calling __init__ (which calls _migrate_schema) twice does not error."""
        InteractionLogger(db_path=db_path)
        InteractionLogger(db_path=db_path)  # second call must not raise

    def test_existing_data_survives_migration(self, db_path):
        """Records inserted before migration are still readable after."""
        # Insert a row using raw SQL (old schema, no new columns).
        conn = sqlite3.connect(db_path)
        conn.executescript(InteractionLogger.SCHEMA)
        conn.execute(
            "INSERT INTO interaction_log (id, timestamp) VALUES (?, ?)",
            ("old-row", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        # Now let InteractionLogger run its migration.
        logger = InteractionLogger(db_path=db_path)
        row = logger.get("old-row")
        assert row is not None
        assert row["id"] == "old-row"
        # New columns should have their defaults.
        assert row["tenant_id"] == "default"
        assert row["corpus_eligible"] == 0
        assert row["enrichment_level"] == ""


# ── Logging with new fields ─────────────────────────────────


class TestLoggingNewFields:
    """Insert and read-back records with the extended fields."""

    def test_defaults_are_applied(self, logger):
        rec = _make_record()
        logger.log(rec)
        row = logger.get(rec.id)
        assert row["tenant_id"] == "default"
        assert row["corpus_eligible"] == 0
        assert row["corpus_version"] is None
        assert row["raw_input"] is None
        assert row["raw_output"] is None
        assert row["enrichment_level"] == ""
        assert row["split_test_group"] == ""
        assert row["thinking_tokens_preserved"] == 0

    def test_custom_values_round_trip(self, logger):
        rec = _make_record(
            tenant_id="acme-corp",
            corpus_eligible=True,
            corpus_version="v3",
            raw_input="hello world",
            raw_output="hi there",
            enrichment_level="deep",
            split_test_group="experiment-42",
            thinking_tokens_preserved=True,
        )
        logger.log(rec)
        row = logger.get(rec.id)
        assert row["tenant_id"] == "acme-corp"
        assert row["corpus_eligible"] == 1
        assert row["corpus_version"] == "v3"
        assert row["raw_input"] == "hello world"
        assert row["raw_output"] == "hi there"
        assert row["enrichment_level"] == "deep"
        assert row["split_test_group"] == "experiment-42"
        assert row["thinking_tokens_preserved"] == 1

    def test_update_result_with_new_fields(self, logger):
        rec = _make_record()
        logger.log(rec)
        logger.update_result(
            rec.id,
            corpus_eligible=True,
            corpus_version="v1",
            raw_output="generated text",
            enrichment_level="standard",
        )
        row = logger.get(rec.id)
        assert row["corpus_eligible"] == 1
        assert row["corpus_version"] == "v1"
        assert row["raw_output"] == "generated text"
        assert row["enrichment_level"] == "standard"


# ── Standalone log_queries helpers ──────────────────────────


def _seed(logger, records: list[InteractionRecord]):
    """Insert a batch of records."""
    for r in records:
        logger.log(r)


class TestGetFailuresByTier:
    def test_returns_failures(self, logger, db_path):
        _seed(
            logger,
            [
                _make_record(selected_tier=1, success=True),
                _make_record(selected_tier=1, success=False, error_type="timeout"),
                _make_record(selected_tier=2, success=False, error_type="rate_limit"),
            ],
        )
        result = get_failures_by_tier(db_path, tier=1, since="2020-01-01")
        assert len(result) == 1
        assert result[0]["success"] == 0

    def test_tenant_filter(self, logger, db_path):
        _seed(
            logger,
            [
                _make_record(selected_tier=1, success=False, tenant_id="a"),
                _make_record(selected_tier=1, success=False, tenant_id="b"),
            ],
        )
        result = get_failures_by_tier(db_path, tier=1, since="2020-01-01", tenant_id="a")
        assert len(result) == 1


class TestGetEscalationRate:
    def test_rate_computation(self, logger, db_path):
        _seed(
            logger,
            [
                _make_record(escalated=False),
                _make_record(escalated=True),
                _make_record(user_correction=True),
                _make_record(escalated=False),
            ],
        )
        rate = get_escalation_rate(db_path, since="2020-01-01")
        assert rate == pytest.approx(0.5)

    def test_empty_db_returns_zero(self, logger, db_path):
        rate = get_escalation_rate(db_path, since="2020-01-01")
        assert rate == 0.0

    def test_tenant_filter(self, logger, db_path):
        _seed(
            logger,
            [
                _make_record(escalated=True, tenant_id="x"),
                _make_record(escalated=False, tenant_id="y"),
            ],
        )
        rate = get_escalation_rate(db_path, since="2020-01-01", tenant_id="x")
        assert rate == 1.0


class TestGetCostByTier:
    def test_sums_cost(self, logger, db_path):
        _seed(
            logger,
            [
                _make_record(selected_tier=1, cost_usd=0.01),
                _make_record(selected_tier=1, cost_usd=0.02),
                _make_record(selected_tier=4, cost_usd=0.50),
            ],
        )
        result = get_cost_by_tier(db_path, since="2020-01-01")
        assert result[1] == pytest.approx(0.03)
        assert result[4] == pytest.approx(0.50)


class TestGetWinsByTier:
    def test_clean_wins(self, logger, db_path):
        _seed(
            logger,
            [
                _make_record(selected_tier=1, success=True, fallback_used=False, escalated=False),
                _make_record(selected_tier=1, success=True, fallback_used=True, escalated=False),
                _make_record(selected_tier=1, success=False, fallback_used=False, escalated=False),
                _make_record(selected_tier=2, success=True, fallback_used=False, escalated=False),
            ],
        )
        result = get_wins_by_tier(db_path, since="2020-01-01")
        assert result[1] == 1
        assert result[2] == 1


class TestGetDomainAccuracy:
    def test_accuracy(self, logger, db_path):
        _seed(
            logger,
            [
                _make_record(domain="security", selected_tier=1, success=True),
                _make_record(domain="security", selected_tier=1, success=True),
                _make_record(domain="security", selected_tier=1, success=False),
            ],
        )
        acc = get_domain_accuracy(db_path, domain="security", tier=1)
        assert acc == pytest.approx(2 / 3)

    def test_missing_domain_returns_zero(self, logger, db_path):
        acc = get_domain_accuracy(db_path, domain="nonexistent", tier=1)
        assert acc == 0.0


class TestGetScoringDrift:
    def test_drift_computation(self, logger, db_path):
        # Two records — one low score, one high.
        _seed(
            logger,
            [
                _make_record(complexity_score=0.2, timestamp="2026-03-21T01:00:00+00:00"),
                _make_record(complexity_score=0.8, timestamp="2026-03-21T02:00:00+00:00"),
            ],
        )
        result = get_scoring_drift(db_path, since="2020-01-01")
        assert result["first_half_avg"] == pytest.approx(0.2)
        assert result["second_half_avg"] == pytest.approx(0.8)
        assert result["drift"] == pytest.approx(0.6)

    def test_empty_db(self, logger, db_path):
        result = get_scoring_drift(db_path, since="2020-01-01")
        assert result["drift"] == 0.0


class TestGetCorpusEligible:
    def test_returns_eligible_only(self, logger, db_path):
        _seed(
            logger,
            [
                _make_record(corpus_eligible=True, raw_input="in1", raw_output="out1"),
                _make_record(corpus_eligible=False),
                _make_record(corpus_eligible=True, raw_input="in2", raw_output="out2"),
            ],
        )
        result = get_corpus_eligible(db_path, since="2020-01-01")
        assert len(result) == 2
        assert all(r["corpus_eligible"] == 1 for r in result)

    def test_tenant_filter(self, logger, db_path):
        _seed(
            logger,
            [
                _make_record(corpus_eligible=True, tenant_id="a"),
                _make_record(corpus_eligible=True, tenant_id="b"),
            ],
        )
        result = get_corpus_eligible(db_path, since="2020-01-01", tenant_id="a")
        assert len(result) == 1


class TestGetTenantSummary:
    def test_summary(self, logger, db_path):
        _seed(
            logger,
            [
                _make_record(
                    tenant_id="acme",
                    success=True,
                    cost_usd=0.05,
                    input_tokens=100,
                    output_tokens=200,
                    corpus_eligible=True,
                ),
                _make_record(
                    tenant_id="acme",
                    success=False,
                    cost_usd=0.10,
                    input_tokens=50,
                    output_tokens=75,
                    escalated=True,
                ),
                _make_record(tenant_id="other", success=True, cost_usd=1.00),
            ],
        )
        summary = get_tenant_summary(db_path, tenant_id="acme", since="2020-01-01")
        assert summary["total_interactions"] == 2
        assert summary["successes"] == 1
        assert summary["failures"] == 1
        assert summary["escalations"] == 1
        assert summary["corpus_eligible_count"] == 1
        assert summary["total_cost_usd"] == pytest.approx(0.15)
        assert summary["total_input_tokens"] == 150
        assert summary["total_output_tokens"] == 275
