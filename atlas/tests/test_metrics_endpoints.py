#!/usr/bin/env python3
"""
Tests for WU-04: Metrics Dashboard + Split Testing.

Covers:
  - All GET /metrics/* endpoints return valid JSON
  - /tenant/{id}/dashboard endpoint
  - SplitTestManager extensions (significance, SQLite store, CLI)
  - Graceful behaviour when databases are empty or missing
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure atlas package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temp directory and set env vars for data paths."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    env_overrides = {
        "ATLAS_INTERACTION_DB": str(data_dir / "interaction_log.db"),
        "ATLAS_EVOLUTION_DIR": str(data_dir / "evolution_cycles"),
        "ATLAS_DATA_DIR": str(data_dir),
        "ATLAS_ROUTING_CONFIG": str(config_dir / "routing_config.yaml"),
        "ATLAS_WEIGHTS_PATH": str(config_dir / "scorer_weights.yaml"),
        "ATLAS_GPU_HOURS_USED": "3.5",
    }
    with patch.dict(os.environ, env_overrides):
        yield tmp_path


@pytest.fixture
def interaction_db(tmp_dir):
    """Create an interaction_log.db with sample data."""
    db_path = str(Path(os.environ["ATLAS_INTERACTION_DB"]))
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS interaction_log (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            message_preview TEXT,
            complexity_score REAL,
            selected_tier INTEGER,
            selected_provider TEXT,
            domain TEXT,
            features TEXT,
            scorer_version INTEGER,
            budget_gated INTEGER DEFAULT 0,
            actual_provider TEXT,
            fallback_used INTEGER DEFAULT 0,
            fallback_chain TEXT,
            latency_ms REAL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            success INTEGER DEFAULT 1,
            error_type TEXT,
            user_correction INTEGER DEFAULT 0,
            user_satisfaction INTEGER,
            escalated INTEGER DEFAULT 0,
            channel TEXT,
            session_id TEXT,
            conversation_turn INTEGER DEFAULT 0
        );
    """)

    now = datetime.now(timezone.utc).isoformat()
    rows = [
        ("id-1", now, "hello", 0.2, 1, "gpt-5.4-mini", "default", "", 2, 0,
         "gpt-5.4-mini", 0, "", 150.0, 100, 200, 0.001, 1, "", 0, None, 0, "cli", "s1", 1),
        ("id-2", now, "security audit", 0.8, 4, "claude-opus-4-6", "security", "", 2, 0,
         "claude-opus-4-6", 0, "", 2500.0, 1000, 2000, 0.15, 1, "", 0, None, 0, "telegram", "s2", 1),
        ("id-3", now, "deploy app", 0.5, 2, "gpt-5.4", "coding", "", 2, 0,
         "gpt-5.4", 0, "", 800.0, 500, 1000, 0.0, 1, "", 0, None, 0, "cli", "s3", 1),
        ("id-4", now, "broken code", 0.6, 2, "gpt-5.4", "coding", "", 2, 0,
         "mimo-v2-pro", 1, "gpt-5.4,mimo-v2-pro", 1200.0, 400, 800, 0.004, 0, "timeout", 0, None, 1, "cli", "s4", 1),
    ]
    for row in rows:
        conn.execute(
            "INSERT INTO interaction_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            row,
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def routing_config(tmp_dir):
    """Write a minimal routing config with budget caps."""
    config_path = Path(os.environ["ATLAS_ROUTING_CONFIG"])
    config_path.write_text(
        "budget:\n"
        "  opus_daily_usd: 15.00\n"
        "  opus_monthly_usd: 100.00\n"
        "  total_monthly_cap_usd: 200.00\n"
    )
    return config_path


@pytest.fixture
def scorer_weights(tmp_dir):
    """Write a minimal scorer weights file."""
    weights_path = Path(os.environ["ATLAS_WEIGHTS_PATH"])
    weights_path.write_text(
        "version: 2\n"
        "last_updated: '2026-03-19'\n"
    )
    return weights_path


@pytest.fixture
def corpus_files(tmp_dir):
    """Create sample distillation JSONL files."""
    data_dir = Path(os.environ["ATLAS_DATA_DIR"])
    f1 = data_dir / "distillation_security.jsonl"
    f1.write_text(
        '{"domain": "security", "prompt": "audit", "gold": "..."}\n'
        '{"domain": "security", "prompt": "pentest", "gold": "..."}\n'
    )
    f2 = data_dir / "distillation_code.jsonl"
    f2.write_text(
        '{"domain": "coding", "prompt": "refactor", "gold": "..."}\n'
    )
    return [f1, f2]


@pytest.fixture
def split_config(tmp_dir):
    """Return path to a temp split_tests.yaml."""
    return str(tmp_dir / "config" / "split_tests.yaml")


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _build_app(tmp_dir):
    """Build an aiohttp app from the WebhookServer."""
    from tools.webhooks.server import WebhookServer
    server = WebhookServer(port=0)
    return server.build_app()


def _has_aiohttp():
    try:
        import aiohttp
        return True
    except ImportError:
        return False


requires_aiohttp = pytest.mark.skipif(
    not _has_aiohttp(), reason="aiohttp not installed"
)


# ═══════════════════════════════════════════════════════════════
# SPLIT TEST MANAGER TESTS
# ═══════════════════════════════════════════════════════════════

class TestSplitTestManager:
    """Tests for the extended SplitTestManager."""

    def test_create_and_assign(self, split_config):
        from core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=split_config)
        test = mgr.create_test(
            name="test-1",
            description="unit test",
            experiment_overrides={"features.safety_critical_weight": 0.35},
        )
        assert test.status == "active"
        assert test.name == "test-1"

        assignment = mgr.assign("session-abc")
        assert assignment is not None
        assert assignment.group in ("control", "experiment")
        assert assignment.test_name == "test-1"

    def test_deterministic_assignment(self, split_config):
        from core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=split_config)
        mgr.create_test(name="det-test")

        group1 = mgr.assign("fixed-session").group
        group2 = mgr.assign("fixed-session").group
        assert group1 == group2

    def test_record_outcome_and_results(self, split_config):
        from core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=split_config)
        mgr.create_test(name="outcome-test")
        mgr.record_outcome("outcome-test", "control", success=True, cost_usd=0.01, latency_ms=100)
        mgr.record_outcome("outcome-test", "experiment", success=False, cost_usd=0.02, latency_ms=200)

        results = mgr.get_results("outcome-test")
        assert results["control"]["count"] == 1
        assert results["experiment"]["count"] == 1
        assert results["control"]["success_rate_pct"] == 100.0
        assert results["experiment"]["success_rate_pct"] == 0.0

    def test_conclude_test(self, split_config):
        from core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=split_config)
        mgr.create_test(name="conclude-test")
        results = mgr.conclude_test("conclude-test")
        assert results["status"] == "concluded"
        assert results["concluded_at"] is not None

    def test_pause_resume(self, split_config):
        from core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=split_config)
        mgr.create_test(name="pause-test")
        mgr.pause_test("pause-test")
        assert mgr.all_tests["pause-test"].status == "paused"
        # No active tests -> assign returns None
        assert mgr.assign("any-session") is None
        mgr.resume_test("pause-test")
        assert mgr.all_tests["pause-test"].status == "active"

    def test_duplicate_name_raises(self, split_config):
        from core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=split_config)
        mgr.create_test(name="dup")
        with pytest.raises(ValueError, match="already exists"):
            mgr.create_test(name="dup")

    def test_bad_weights_raises(self, split_config):
        from core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=split_config)
        with pytest.raises(ValueError, match="sum to 1.0"):
            mgr.create_test(name="bad", control_weight=0.3, experiment_weight=0.3)

    def test_delete_test(self, split_config):
        from core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=split_config)
        mgr.create_test(name="to-delete")
        assert "to-delete" in mgr.all_tests
        mgr.delete_test("to-delete")
        assert "to-delete" not in mgr.all_tests

    def test_get_all_results(self, split_config):
        from core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=split_config)
        mgr.create_test(name="a")
        mgr.create_test(name="b")
        results = mgr.get_all_results()
        assert results["active_count"] == 2
        assert len(results["tests"]) == 2


# ═══════════════════════════════════════════════════════════════
# STATISTICAL SIGNIFICANCE TESTS
# ═══════════════════════════════════════════════════════════════

class TestStatisticalSignificance:

    def test_chi_squared_basic(self):
        from core.routing.split_test import chi_squared_significance
        result = chi_squared_significance(90, 10, 80, 20)
        assert "chi2" in result
        assert "p_value" in result
        assert "significant" in result
        assert isinstance(result["significant"], bool)

    def test_chi_squared_identical_rates(self):
        from core.routing.split_test import chi_squared_significance
        result = chi_squared_significance(50, 50, 50, 50)
        assert result["chi2"] == 0.0
        assert result["significant"] is False

    def test_chi_squared_very_different_rates(self):
        from core.routing.split_test import chi_squared_significance
        # 95% vs 5% should be significant
        result = chi_squared_significance(95, 5, 5, 95)
        assert result["significant"] is True
        assert result["p_value"] < 0.001

    def test_chi_squared_empty(self):
        from core.routing.split_test import chi_squared_significance
        result = chi_squared_significance(0, 0, 0, 0)
        assert result["chi2"] == 0.0
        assert result["p_value"] == 1.0
        assert result["significant"] is False

    def test_compute_significance_wrapper(self, split_config):
        from core.routing.split_test import SplitTestManager, compute_significance
        mgr = SplitTestManager(config_path=split_config)
        test = mgr.create_test(name="sig-test")
        # Record enough outcomes for significance
        for _ in range(50):
            mgr.record_outcome("sig-test", "control", success=True)
            mgr.record_outcome("sig-test", "experiment", success=True)

        result = compute_significance(mgr.all_tests["sig-test"])
        assert result["min_samples_met"] is True
        assert result["significant"] is False  # identical rates


# ═══════════════════════════════════════════════════════════════
# SQLITE OUTCOME STORE TESTS
# ═══════════════════════════════════════════════════════════════

class TestSplitTestOutcomeStore:

    def test_record_and_query(self, tmp_dir):
        from core.routing.split_test import SplitTestOutcomeStore
        db_path = str(tmp_dir / "data" / "split_test_outcomes.db")
        store = SplitTestOutcomeStore(db_path=db_path)
        store.record("test-a", "control", session_id="s1", success=True, cost_usd=0.01)
        store.record("test-a", "experiment", session_id="s2", success=False, cost_usd=0.02)

        assert store.count("test-a") == 2
        assert store.count() == 2

        ctrl = store.get_outcomes("test-a", group="control")
        assert len(ctrl) == 1
        assert ctrl[0]["success"] == 1

        exp = store.get_outcomes("test-a", group="experiment")
        assert len(exp) == 1
        assert exp[0]["success"] == 0

    def test_empty_store(self, tmp_dir):
        from core.routing.split_test import SplitTestOutcomeStore
        db_path = str(tmp_dir / "data" / "outcomes_empty.db")
        store = SplitTestOutcomeStore(db_path=db_path)
        assert store.count() == 0
        assert store.get_outcomes("nonexistent") == []


# ═══════════════════════════════════════════════════════════════
# METRICS ENDPOINT TESTS (aiohttp)
# ═══════════════════════════════════════════════════════════════

@requires_aiohttp
class TestMetricsEndpoints:
    """Test all GET /metrics/* endpoints via aiohttp test client."""

    @pytest.fixture
    def cli(self, tmp_dir, interaction_db, routing_config, scorer_weights, corpus_files, aiohttp_client):
        from tools.webhooks.server import WebhookServer
        server = WebhookServer(port=0)
        app = server.build_app()
        return aiohttp_client(app)

    @pytest.mark.asyncio
    async def test_metrics_summary(self, cli):
        client = await cli
        resp = await client.get("/metrics")
        assert resp.status == 200
        data = await resp.json()
        assert data["interactions"]["total"] == 4
        assert data["interactions"]["successes"] == 3
        assert "cost_usd" in data
        assert "avg_latency_ms" in data

    @pytest.mark.asyncio
    async def test_metrics_routing(self, cli):
        client = await cli
        resp = await client.get("/metrics/routing")
        assert resp.status == 200
        data = await resp.json()
        assert "tiers" in data
        assert "providers" in data
        assert len(data["tiers"]) > 0

        # Tier 1 should have 1 interaction
        tier1 = [t for t in data["tiers"] if t["selected_tier"] == 1]
        assert len(tier1) == 1
        assert tier1[0]["volume"] == 1

    @pytest.mark.asyncio
    async def test_metrics_evolution(self, cli):
        client = await cli
        resp = await client.get("/metrics/evolution")
        assert resp.status == 200
        data = await resp.json()
        assert "current_weights_version" in data
        assert data["current_weights_version"] == 2
        assert "recent_cycles" in data

    @pytest.mark.asyncio
    async def test_metrics_budget(self, cli):
        client = await cli
        resp = await client.get("/metrics/budget")
        assert resp.status == 200
        data = await resp.json()
        assert "tier_spend" in data
        assert "budget_caps" in data
        assert data["gpu_hours_used"] == 3.5
        assert data["budget_caps"]["opus_daily_usd"] == 15.0

    @pytest.mark.asyncio
    async def test_metrics_skills(self, cli):
        client = await cli
        resp = await client.get("/metrics/skills")
        assert resp.status == 200
        data = await resp.json()
        assert "domain_usage" in data
        assert len(data["domain_usage"]) > 0

    @pytest.mark.asyncio
    async def test_metrics_corpus(self, cli):
        client = await cli
        resp = await client.get("/metrics/corpus")
        assert resp.status == 200
        data = await resp.json()
        assert data["total_pairs"] == 3
        assert data["domains"]["security"] == 2
        assert data["domains"]["coding"] == 1
        assert data["readiness_pct"] == 3.0  # 3/100 * 100

    @pytest.mark.asyncio
    async def test_metrics_tenants(self, cli):
        client = await cli
        resp = await client.get("/metrics/tenants")
        assert resp.status == 200
        data = await resp.json()
        assert data["total_tenants"] >= 1
        assert "tenants" in data

    @pytest.mark.asyncio
    async def test_tenant_dashboard(self, cli):
        client = await cli
        resp = await client.get("/tenant/cli/dashboard")
        assert resp.status == 200
        data = await resp.json()
        assert data["tenant_id"] == "cli"
        assert data["summary"]["total_interactions"] == 3  # 3 CLI interactions
        assert "tier_breakdown" in data
        assert "recent_interactions" in data

    @pytest.mark.asyncio
    async def test_tenant_dashboard_empty(self, cli):
        client = await cli
        resp = await client.get("/tenant/nonexistent/dashboard")
        assert resp.status == 200
        data = await resp.json()
        assert data["tenant_id"] == "nonexistent"
        assert data["summary"]["total_interactions"] == 0


# ═══════════════════════════════════════════════════════════════
# EMPTY DB / MISSING DB GRACEFUL HANDLING
# ═══════════════════════════════════════════════════════════════

@requires_aiohttp
class TestMetricsEndpointsEmptyDB:
    """Verify endpoints return valid JSON even with no interaction data."""

    @pytest.fixture
    def cli(self, tmp_dir, aiohttp_client):
        # No interaction_db fixture — DB doesn't exist
        from tools.webhooks.server import WebhookServer
        server = WebhookServer(port=0)
        app = server.build_app()
        return aiohttp_client(app)

    @pytest.mark.asyncio
    async def test_metrics_no_db(self, cli):
        client = await cli
        resp = await client.get("/metrics")
        assert resp.status == 200
        data = await resp.json()
        assert data["interactions"]["total"] == 0

    @pytest.mark.asyncio
    async def test_routing_no_db(self, cli):
        client = await cli
        resp = await client.get("/metrics/routing")
        assert resp.status == 200
        data = await resp.json()
        assert data["tiers"] == []

    @pytest.mark.asyncio
    async def test_budget_no_db(self, cli):
        client = await cli
        resp = await client.get("/metrics/budget")
        assert resp.status == 200
        data = await resp.json()
        assert data["total_spend_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_corpus_no_files(self, cli):
        client = await cli
        resp = await client.get("/metrics/corpus")
        assert resp.status == 200
        data = await resp.json()
        assert data["total_pairs"] == 0

    @pytest.mark.asyncio
    async def test_tenants_no_db(self, cli):
        client = await cli
        resp = await client.get("/metrics/tenants")
        assert resp.status == 200
        data = await resp.json()
        assert data["total_tenants"] == 0


# ═══════════════════════════════════════════════════════════════
# EXISTING ENDPOINTS STILL WORK
# ═══════════════════════════════════════════════════════════════

@requires_aiohttp
class TestExistingEndpoints:
    """Verify /health and /status still work after adding metrics routes."""

    @pytest.fixture
    def cli(self, tmp_dir, aiohttp_client):
        from tools.webhooks.server import WebhookServer
        server = WebhookServer(port=0)
        app = server.build_app()
        return aiohttp_client(app)

    @pytest.mark.asyncio
    async def test_health(self, cli):
        client = await cli
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_status(self, cli):
        client = await cli
        resp = await client.get("/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["system"] == "ATLAS"
