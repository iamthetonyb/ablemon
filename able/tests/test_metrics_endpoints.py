"""
Tests for metrics endpoints and split testing framework.

Covers:
    - Each /metrics/* endpoint returns valid JSON
    - ?hours=N parameter is respected
    - SplitTestManager create/assign/record/conclude lifecycle
    - Consistent hashing (same request_hash -> same group)
    - Statistical significance check returns p-value
"""

import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# Ensure able package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary interaction_log.db with sample data."""
    db_path = str(tmp_path / "interaction_log.db")
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

    # Insert sample interactions across tiers and channels
    now = datetime.now(timezone.utc)
    for i in range(30):
        ts = (now - timedelta(hours=i % 12)).isoformat()
        tier = (i % 3) + 1
        if tier == 3:
            tier = 4  # No tier 3 in routing (skip to 4)
        conn.execute(
            """INSERT INTO interaction_log
               (id, timestamp, message_preview, complexity_score,
                selected_tier, selected_provider, domain, features,
                scorer_version, latency_ms, input_tokens, output_tokens,
                cost_usd, success, fallback_used, escalated, channel)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                ts,
                f"test message {i}",
                0.2 + (i % 5) * 0.15,
                tier,
                f"provider-{tier}",
                ["coding", "security", "research", "creative", "default"][i % 5],
                "{}",
                2,
                100 + i * 10,
                500 + i * 50,
                200 + i * 30,
                0.001 * tier * (i + 1),
                1 if i % 7 != 0 else 0,  # ~14% failure rate
                1 if i % 10 == 0 else 0,
                1 if i % 15 == 0 else 0,
                ["cli", "telegram", "discord"][i % 3],
            ),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def tmp_split_config(tmp_path):
    """Create a temporary split_tests.yaml."""
    config_path = str(tmp_path / "split_tests.yaml")
    with open(config_path, "w") as f:
        f.write("tests: []\ndefaults:\n  min_samples: 100\n  max_duration_hours: 168\n  auto_conclude: true\n")
    return config_path


@pytest.fixture
def server(tmp_db):
    """Create a WebhookServer with temp db."""
    from able.tools.webhooks.server import WebhookServer
    return WebhookServer(port=0, host="127.0.0.1", db_path=tmp_db)


@pytest.fixture
def app(server):
    """Build the aiohttp app."""
    return server.build_app()


# ═══════════════════════════════════════════════════════════════
# METRICS ENDPOINT TESTS
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestMetricsEndpoints:
    """Test each /metrics/* endpoint returns valid JSON."""

    async def test_metrics_returns_json(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/metrics")
        assert resp.status == 200
        data = await resp.json()
        assert "total_interactions" in data
        assert "success_rate_pct" in data
        assert "total_cost_usd" in data
        assert "period_hours" in data
        assert data["period_hours"] == 24

    async def test_metrics_hours_param(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/metrics?hours=6")
        assert resp.status == 200
        data = await resp.json()
        assert data["period_hours"] == 6

    async def test_metrics_routing(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/metrics/routing")
        assert resp.status == 200
        data = await resp.json()
        assert "by_tier" in data
        assert "by_domain" in data
        assert isinstance(data["by_tier"], list)

    async def test_metrics_routing_hours_param(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/metrics/routing?hours=1")
        assert resp.status == 200
        data = await resp.json()
        assert data["period_hours"] == 1

    async def test_metrics_evolution(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/metrics/evolution")
        assert resp.status == 200
        data = await resp.json()
        assert "evolution_cycles" in data
        assert "scorer_version_drift" in data

    async def test_metrics_budget(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/metrics/budget")
        assert resp.status == 200
        data = await resp.json()
        assert "by_tier" in data
        assert "opus_spend" in data
        assert "last_24h_usd" in data["opus_spend"]

    async def test_metrics_skills(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/metrics/skills")
        assert resp.status == 200
        data = await resp.json()
        assert "by_domain" in data
        assert isinstance(data["by_domain"], list)

    async def test_metrics_corpus(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/metrics/corpus")
        assert resp.status == 200
        data = await resp.json()
        assert "total_pairs" in data
        assert "progress_pct" in data
        assert "target_pairs" in data

    async def test_metrics_tenants(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/metrics/tenants")
        assert resp.status == 200
        data = await resp.json()
        assert "tenants" in data
        assert "total_tenants" in data

    async def test_tenant_dashboard(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/tenant/cli/dashboard")
        assert resp.status == 200
        data = await resp.json()
        assert data["tenant_id"] == "cli"
        assert "summary" in data
        assert "by_tier" in data
        assert "recent_interactions" in data

    async def test_tenant_dashboard_hours_param(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/tenant/cli/dashboard?hours=48")
        assert resp.status == 200
        data = await resp.json()
        assert data["period_hours"] == 48

    async def test_tenant_dashboard_unknown_tenant(self, aiohttp_client, app):
        """Unknown tenant should return empty data, not error."""
        client = await aiohttp_client(app)
        resp = await client.get("/tenant/nonexistent/dashboard")
        assert resp.status == 200
        data = await resp.json()
        assert data["summary"].get("interactions", 0) == 0

    async def test_metrics_invalid_hours_defaults(self, aiohttp_client, app):
        """Invalid hours param should default to 24."""
        client = await aiohttp_client(app)
        resp = await client.get("/metrics?hours=notanumber")
        assert resp.status == 200
        data = await resp.json()
        assert data["period_hours"] == 24

    async def test_existing_endpoints_still_work(self, aiohttp_client, app):
        """Existing /health endpoint should still work."""
        client = await aiohttp_client(app)
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "healthy"


# ═══════════════════════════════════════════════════════════════
# SPLIT TEST MANAGER TESTS
# ═══════════════════════════════════════════════════════════════

class TestSplitTestManager:
    """Test SplitTestManager create/assign/record/conclude lifecycle."""

    def test_create_test(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="safety-weight-bump",
            groups={
                "control": {},
                "experiment": {"safety_critical_weight": 0.35},
            },
            min_samples=50,
            description="Test bumping safety weight",
        )
        assert test.id is not None
        assert test.name == "safety-weight-bump"
        assert test.status == "running"
        assert len(test.groups) == 2
        assert test.min_samples == 50

    def test_create_test_default_equal_weights(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="equal-split",
            groups={"A": {}, "B": {}},
        )
        assert abs(test.assignment_weights["A"] - 0.5) < 0.01
        assert abs(test.assignment_weights["B"] - 0.5) < 0.01

    def test_create_test_custom_weights(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="90-10-split",
            groups={"control": {}, "experiment": {"weight": 0.4}},
            weights={"control": 0.9, "experiment": 0.1},
        )
        assert test.assignment_weights["control"] == 0.9
        assert test.assignment_weights["experiment"] == 0.1

    def test_create_test_bad_weights_rejected(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        with pytest.raises(ValueError, match="sum to 1.0"):
            mgr.create_test(
                name="bad",
                groups={"A": {}, "B": {}},
                weights={"A": 0.5, "B": 0.7},
            )

    def test_create_test_too_few_groups_rejected(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        with pytest.raises(ValueError, match="at least 2 groups"):
            mgr.create_test(name="solo", groups={"only": {}})

    def test_assign_group_deterministic(self, tmp_split_config, tmp_db):
        """Same request_hash always gets the same group."""
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="hash-test",
            groups={"control": {}, "experiment": {}},
        )

        # Run same hash 100 times — must always get same group
        results = set()
        for _ in range(100):
            group = mgr.assign_group(test.id, "session-abc-123")
            results.add(group)

        assert len(results) == 1  # Always same group

    def test_assign_group_distributes_traffic(self, tmp_split_config, tmp_db):
        """Different hashes should distribute across groups roughly evenly."""
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="distribution-test",
            groups={"control": {}, "experiment": {}},
        )

        counts = {"control": 0, "experiment": 0}
        n = 1000
        for i in range(n):
            group = mgr.assign_group(test.id, f"request-{i}")
            counts[group] += 1

        # With 50/50 split and 1000 samples, each group should get 400-600
        assert 350 < counts["control"] < 650
        assert 350 < counts["experiment"] < 650

    def test_assign_group_not_running_raises(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="concluded-test",
            groups={"A": {}, "B": {}},
        )
        mgr.conclude_test(test.id)

        with pytest.raises(ValueError, match="not running"):
            mgr.assign_group(test.id, "some-hash")

    def test_record_outcome(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="outcome-test",
            groups={"control": {}, "experiment": {}},
        )

        mgr.record_outcome(test.id, "control", success=True, latency_ms=150, cost_usd=0.01)
        mgr.record_outcome(test.id, "control", success=False, latency_ms=200, cost_usd=0.02)
        mgr.record_outcome(test.id, "experiment", success=True, latency_ms=100, cost_usd=0.005)

        results = mgr.get_results(test.id)
        assert results["groups"]["control"]["count"] == 2
        assert results["groups"]["control"]["successes"] == 1
        assert results["groups"]["experiment"]["count"] == 1
        assert results["groups"]["experiment"]["successes"] == 1

    def test_record_outcome_wrong_group_ignored(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="bad-group-test",
            groups={"A": {}, "B": {}},
        )

        # Recording to nonexistent group should not crash
        mgr.record_outcome(test.id, "C", success=True)
        results = mgr.get_results(test.id)
        assert results["groups"]["A"]["count"] == 0
        assert results["groups"]["B"]["count"] == 0

    def test_get_results_structure(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="results-test",
            groups={"control": {}, "experiment": {}},
        )

        results = mgr.get_results(test.id)
        assert "id" in results
        assert "name" in results
        assert "status" in results
        assert "groups" in results
        assert "significance" in results
        assert "winner" in results
        assert results["winner"] == "inconclusive"  # No data yet

    def test_conclude_test(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="conclude-test",
            groups={"control": {}, "experiment": {}},
        )

        results = mgr.conclude_test(test.id)
        assert results["status"] == "concluded"
        assert results["concluded_at"] is not None

        # Verify test is no longer running
        assert len(mgr.list_tests(status="running")) == 0
        assert len(mgr.list_tests(status="concluded")) == 1

    def test_list_tests(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        mgr.create_test(name="test-1", groups={"A": {}, "B": {}})
        mgr.create_test(name="test-2", groups={"A": {}, "B": {}})
        t3 = mgr.create_test(name="test-3", groups={"A": {}, "B": {}})
        mgr.conclude_test(t3.id)

        assert len(mgr.list_tests()) == 3
        assert len(mgr.list_tests(status="running")) == 2
        assert len(mgr.list_tests(status="concluded")) == 1

    def test_cancel_test(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(name="cancel-me", groups={"A": {}, "B": {}})
        mgr.cancel_test(test.id)

        assert len(mgr.list_tests(status="running")) == 0
        assert len(mgr.list_tests(status="cancelled")) == 1

    def test_persistence_across_instances(self, tmp_split_config, tmp_db):
        """Tests should survive manager restart."""
        from able.core.routing.split_test import SplitTestManager

        mgr1 = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)
        test = mgr1.create_test(name="persist-test", groups={"A": {}, "B": {}})
        mgr1.record_outcome(test.id, "A", success=True, latency_ms=100)

        # Create new manager instance from same config
        mgr2 = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)
        assert len(mgr2.list_tests()) == 1
        results = mgr2.get_results(test.id)
        assert results["groups"]["A"]["count"] == 1


# ═══════════════════════════════════════════════════════════════
# SIGNIFICANCE CHECK TESTS
# ═══════════════════════════════════════════════════════════════

class TestSignificanceCheck:
    """Test the chi-squared significance check."""

    def test_significance_returns_p_value(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        group_a = {"successes": 80, "failures": 20}
        group_b = {"successes": 60, "failures": 40}

        result = mgr._check_significance(group_a, group_b)
        assert "p_value" in result
        assert "chi_squared" in result
        assert "significant" in result
        assert isinstance(result["p_value"], float)
        assert 0.0 <= result["p_value"] <= 1.0

    def test_significance_clear_difference(self, tmp_split_config, tmp_db):
        """Large difference should be statistically significant."""
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        # 95% vs 50% success — should be very significant
        group_a = {"successes": 95, "failures": 5}
        group_b = {"successes": 50, "failures": 50}

        result = mgr._check_significance(group_a, group_b)
        assert result["significant"] is True
        assert result["p_value"] < 0.01

    def test_significance_no_difference(self, tmp_split_config, tmp_db):
        """Identical groups should not be significant."""
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        group_a = {"successes": 50, "failures": 50}
        group_b = {"successes": 50, "failures": 50}

        result = mgr._check_significance(group_a, group_b)
        assert result["significant"] is False
        assert result["p_value"] >= 0.05

    def test_significance_empty_groups(self, tmp_split_config, tmp_db):
        """Empty groups should return not significant."""
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        group_a = {"successes": 0, "failures": 0}
        group_b = {"successes": 0, "failures": 0}

        result = mgr._check_significance(group_a, group_b)
        assert result["significant"] is False
        assert result["sufficient_data"] is False

    def test_significance_insufficient_data(self, tmp_split_config, tmp_db):
        """Small sample should flag insufficient data."""
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        group_a = {"successes": 9, "failures": 1}
        group_b = {"successes": 5, "failures": 5}

        result = mgr._check_significance(group_a, group_b)
        assert result["sufficient_data"] is False

    def test_full_lifecycle_with_significance(self, tmp_split_config, tmp_db):
        """End-to-end: create test, record many outcomes, check significance in results."""
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="lifecycle-sig",
            groups={"control": {}, "experiment": {"boost": 0.1}},
            min_samples=50,
        )

        # Record 100 outcomes per group with clear difference
        for i in range(100):
            mgr.record_outcome(
                test.id, "control",
                success=(i % 2 == 0),  # 50% success
                latency_ms=200,
                cost_usd=0.01,
            )
            mgr.record_outcome(
                test.id, "experiment",
                success=(i % 10 != 0),  # 90% success
                latency_ms=150,
                cost_usd=0.01,
            )

        results = mgr.get_results(test.id)
        assert results["significance"]["significant"] is True
        assert results["winner"] == "experiment"

        concluded = mgr.conclude_test(test.id)
        assert concluded["status"] == "concluded"


# ═══════════════════════════════════════════════════════════════
# THREE-GROUP SPLIT TEST
# ═══════════════════════════════════════════════════════════════

class TestThreeGroupSplit:
    """Test split tests with more than 2 groups."""

    def test_three_way_split(self, tmp_split_config, tmp_db):
        from able.core.routing.split_test import SplitTestManager
        mgr = SplitTestManager(config_path=tmp_split_config, db_path=tmp_db)

        test = mgr.create_test(
            name="three-way",
            groups={"A": {}, "B": {"x": 1}, "C": {"x": 2}},
            weights={"A": 0.34, "B": 0.33, "C": 0.33},
        )

        counts = {"A": 0, "B": 0, "C": 0}
        for i in range(900):
            group = mgr.assign_group(test.id, f"req-{i}")
            counts[group] += 1

        # Each should get roughly 300 (allow wide margin)
        for g, c in counts.items():
            assert 200 < c < 400, f"Group {g} got {c}, expected ~300"
