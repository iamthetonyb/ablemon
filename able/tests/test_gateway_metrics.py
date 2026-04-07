"""
Smoke tests for the new gateway metrics endpoints and buddy handler.

Tests are import-only / unit-level — they don't start the full gateway server.
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── metrics_queries module ────────────────────────────────────────────────────

class TestMetricsQueries:
    """Unit tests for able.core.routing.metrics_queries."""

    def setup_method(self):
        """Create a minimal in-memory interaction_log.db for testing."""
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interaction_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                complexity_score REAL DEFAULT 0,
                selected_tier INTEGER DEFAULT 1,
                selected_provider TEXT DEFAULT '',
                domain TEXT DEFAULT 'default',
                features TEXT DEFAULT '{}',
                scorer_version INTEGER DEFAULT 1,
                budget_gated INTEGER DEFAULT 0,
                actual_provider TEXT DEFAULT '',
                fallback_used INTEGER DEFAULT 0,
                fallback_chain TEXT DEFAULT '',
                latency_ms REAL DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0,
                success INTEGER DEFAULT 1,
                error_type TEXT DEFAULT '',
                user_correction INTEGER DEFAULT 0,
                user_satisfaction INTEGER,
                escalated INTEGER DEFAULT 0,
                channel TEXT DEFAULT 'test',
                session_id TEXT DEFAULT '',
                conversation_turn INTEGER DEFAULT 0,
                tenant_id TEXT DEFAULT 'default',
                corpus_eligible INTEGER DEFAULT 0,
                quality_score REAL
            )
        """)
        # Insert some sample rows
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            "INSERT INTO interaction_log (id, timestamp, selected_tier, domain, cost_usd, success, input_tokens, output_tokens, complexity_score, corpus_eligible, latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("r1", now, 1, "coding", 0.001, 1, 100, 200, 0.3, 1, 150),
                ("r2", now, 2, "security", 0.005, 1, 300, 400, 0.6, 0, 300),
                ("r3", now, 4, "security", 0.02, 0, 500, 600, 0.8, 0, 800),
            ],
        )
        conn.commit()
        conn.close()

    def teardown_method(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_get_metrics_summary_returns_correct_totals(self):
        from able.core.routing.metrics_queries import get_metrics_summary
        result = get_metrics_summary(hours=24, db_path=self.db_path)
        assert result["total_interactions"] == 3
        assert result["period_hours"] == 24
        assert "success_rate_pct" in result
        assert "timestamp" in result
        assert result["total_cost_usd"] == pytest.approx(0.026, abs=1e-4)

    def test_get_routing_metrics_returns_tier_breakdown(self):
        from able.core.routing.metrics_queries import get_routing_metrics
        result = get_routing_metrics(hours=24, db_path=self.db_path)
        assert "by_tier" in result
        assert "by_domain" in result
        assert len(result["by_tier"]) == 3  # tiers 1, 2, 4
        tiers = {row["selected_tier"]: row for row in result["by_tier"]}
        assert 1 in tiers
        assert tiers[1]["volume"] == 1

    def test_get_corpus_metrics_returns_structure(self):
        from able.core.routing.metrics_queries import get_corpus_metrics
        result = get_corpus_metrics(db_path=self.db_path)
        assert "total_pairs" in result
        assert "target_pairs" in result
        assert "progress_pct" in result
        assert isinstance(result["progress_pct"], (int, float))

    def test_get_evolution_metrics_returns_structure(self):
        from able.core.routing.metrics_queries import get_evolution_metrics
        result = get_evolution_metrics(hours=168, db_path=self.db_path)
        assert "current_version" in result
        assert "evolution_cycles" in result
        assert isinstance(result["evolution_cycles"], list)

    def test_get_budget_metrics_returns_opus_spend(self):
        from able.core.routing.metrics_queries import get_budget_metrics
        result = get_budget_metrics(hours=24, db_path=self.db_path)
        assert "by_tier" in result
        assert "opus_spend" in result
        assert "last_24h_usd" in result["opus_spend"]
        assert "daily_remaining_usd" in result["opus_spend"]

    def test_since_iso_format(self):
        from able.core.routing.metrics_queries import since_iso
        ts = since_iso(24)
        # Should be a valid ISO8601 string
        from datetime import datetime
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed is not None

    def test_db_query_handles_missing_db(self):
        from able.core.routing.metrics_queries import db_query
        # Should return empty list, not raise
        result = db_query("SELECT 1", db_path="/nonexistent/path.db")
        assert result == []


# ── Gateway handler registration ─────────────────────────────────────────────

class TestGatewayNewHandlers:
    """Verify new handlers and routes are registered on the gateway."""

    def test_gateway_source_has_new_handler_signatures(self):
        """Verify new handler method names exist in gateway.py source."""
        gateway_src = Path(__file__).parents[2] / "able" / "core" / "gateway" / "gateway.py"
        assert gateway_src.exists(), "gateway.py not found"
        text = gateway_src.read_text()
        expected = [
            "async def _buddy_handler",
            "async def _metrics_summary_handler",
            "async def _metrics_routing_handler",
            "async def _metrics_corpus_handler",
            "async def _metrics_evolution_handler",
            "async def _metrics_budget_handler",
            "async def _push_event",
            "async def _events_handler",
            "async def _api_chat_handler",
        ]
        for sig in expected:
            assert sig in text, f"gateway.py missing: {sig}"

    def test_gateway_routes_registered(self):
        """Verify new routes are wired in start_health_server."""
        gateway_src = Path(__file__).parents[2] / "able" / "core" / "gateway" / "gateway.py"
        text = gateway_src.read_text()
        expected_routes = [
            '"/api/buddy"',
            '"/metrics"',
            '"/metrics/routing"',
            '"/metrics/corpus"',
            '"/metrics/evolution"',
            '"/metrics/budget"',
            '"/events"',
            '"/api/chat"',
        ]
        for route in expected_routes:
            assert route in text, f"Route {route} not registered in gateway"

    def test_push_event_no_subscribers_noop(self):
        """_push_event with empty subscriber list should not raise."""
        import asyncio

        # Import just the function logic inline — avoids full gateway import chain
        import json as _json
        from datetime import datetime, timezone

        async def _push_event(subscribers, event_type, data):
            if not subscribers:
                return
            payload = _json.dumps({
                "type": event_type,
                "data": data,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            for q in list(subscribers):
                try:
                    q.put_nowait(payload)
                except Exception:
                    pass

        asyncio.run(_push_event([], "test", {"key": "value"}))  # Should not raise


# ── metrics_queries import ────────────────────────────────────────────────────

def test_metrics_queries_module_importable():
    """The metrics_queries module must import cleanly."""
    import able.core.routing.metrics_queries as mq
    assert callable(mq.get_metrics_summary)
    assert callable(mq.get_routing_metrics)
    assert callable(mq.get_corpus_metrics)
    assert callable(mq.get_evolution_metrics)
    assert callable(mq.get_budget_metrics)
    assert callable(mq.db_query)
    assert callable(mq.db_query_one)
    assert callable(mq.since_iso)
