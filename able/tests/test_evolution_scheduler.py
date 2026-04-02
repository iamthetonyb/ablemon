"""
Tests for the Evolution Self-Scheduler and Morning Report.

Covers:
- SelfScheduler action generation, enactment, promote/reject
- MorningReporter report generation and Telegram formatting
- Cron registration of evolution daemon and morning report
- Per-model prompt template loading
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest
import yaml

# Ensure able package is importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from able.core.evolution.self_scheduler import (
    SelfScheduler,
    ScheduledAction,
    SchedulerCycleReport,
    MAX_CRONS_PER_CYCLE,
)
from able.core.evolution.morning_report import (
    MorningReporter,
    MorningReportData,
)


def _run(coro):
    """Run an async coroutine in a fresh event loop (Python 3.10+ safe)."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════
# SELF-SCHEDULER TESTS
# ═══════════════════════════════════════════════════════════════

class TestSelfScheduler:
    """Tests for SelfScheduler action generation and lifecycle."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.actions_dir = os.path.join(self.tmpdir, "actions")
        self.scheduler = SelfScheduler(
            scheduler=None,
            audit_trail=None,
            actions_dir=self.actions_dir,
        )

    def test_init_creates_actions_dir(self):
        assert Path(self.actions_dir).exists()

    def test_empty_analysis_produces_no_actions(self):
        report = _run(self.scheduler.run_cycle(
            analysis_results={},
            cycle_id="test_empty",
        ))
        assert report.actions_proposed == 0
        assert report.actions_created == 0
        assert report.cycle_id == "test_empty"

    def test_high_failure_rate_creates_monitoring_cron(self):
        analysis = {
            "failures_by_tier": [
                {"selected_tier": 1, "failure_rate_pct": 25, "total": 100},
            ],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_failures"))
        assert report.crons_created == 1
        assert report.actions[0].action_type == "cron_create"
        assert report.actions[0].dry_run is True
        assert "tier-1" in report.actions[0].name

    def test_failure_patterns_create_eval(self):
        analysis = {
            "failure_patterns": [
                {"domain": "security", "count": 5, "sample_prompts": ["test prompt"]},
            ],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_eval"))
        assert report.evals_created == 1
        assert report.actions[0].action_type == "eval_create"

    def test_uncovered_task_types_propose_skill(self):
        analysis = {
            "uncovered_task_types": ["data-visualization", "pdf-parsing"],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_skill"))
        assert report.skills_proposed == 2

    def test_max_crons_per_cycle_enforced(self):
        """Cannot create more than MAX_CRONS_PER_CYCLE crons in one cycle."""
        analysis = {
            "failures_by_tier": [
                {"selected_tier": i, "failure_rate_pct": 20 + i, "total": 50}
                for i in range(1, 8)  # 7 tiers with high failure rates
            ],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_cap"))
        assert report.crons_created <= MAX_CRONS_PER_CYCLE

    def test_all_actions_start_as_dry_run(self):
        analysis = {
            "failures_by_tier": [
                {"selected_tier": 1, "failure_rate_pct": 30, "total": 100},
            ],
            "failure_patterns": [
                {"domain": "coding", "count": 4, "sample_prompts": []},
            ],
            "uncovered_task_types": ["voice-transcription"],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_dryrun"))
        for action in report.actions:
            assert action.dry_run is True

    def test_promote_action(self):
        analysis = {
            "failures_by_tier": [
                {"selected_tier": 2, "failure_rate_pct": 20, "total": 50},
            ],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_promote"))
        action_id = report.actions[0].id
        assert self.scheduler.promote_action(action_id) is True

        # Verify on disk
        path = Path(self.actions_dir) / f"{action_id}.json"
        with open(path) as f:
            data = json.load(f)
        assert data["promoted"] is True
        assert data["dry_run"] is False

    def test_reject_action(self):
        analysis = {
            "uncovered_task_types": ["image-gen"],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_reject"))
        action_id = report.actions[0].id
        assert self.scheduler.reject_action(action_id) is True

        path = Path(self.actions_dir) / f"{action_id}.json"
        with open(path) as f:
            data = json.load(f)
        assert data["rejected"] is True

    def test_cannot_promote_rejected_action(self):
        analysis = {
            "uncovered_task_types": ["audio-gen"],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_rej_prom"))
        action_id = report.actions[0].id
        self.scheduler.reject_action(action_id)
        assert self.scheduler.promote_action(action_id) is False

    def test_get_pending_actions(self):
        analysis = {
            "failures_by_tier": [
                {"selected_tier": 1, "failure_rate_pct": 25, "total": 100},
            ],
            "uncovered_task_types": ["chart-gen"],
        }
        _run(self.scheduler.run_cycle(analysis, cycle_id="test_pending"))
        pending = self.scheduler.get_pending_actions()
        assert len(pending) == 2

    def test_pending_excludes_promoted_and_rejected(self):
        analysis = {
            "failures_by_tier": [
                {"selected_tier": 1, "failure_rate_pct": 25, "total": 100},
                {"selected_tier": 2, "failure_rate_pct": 30, "total": 80},
            ],
            "uncovered_task_types": ["widget-gen"],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_filter"))
        assert len(report.actions) == 3
        self.scheduler.promote_action(report.actions[0].id)
        self.scheduler.reject_action(report.actions[1].id)
        pending = self.scheduler.get_pending_actions()
        # Only the third should remain
        assert len(pending) == 1

    def test_promote_nonexistent_returns_false(self):
        assert self.scheduler.promote_action("nonexistent") is False

    def test_reject_nonexistent_returns_false(self):
        assert self.scheduler.reject_action("nonexistent") is False

    def test_status_property(self):
        status = self.scheduler.status
        assert "cycles_completed" in status
        assert status["max_crons_per_cycle"] == MAX_CRONS_PER_CYCLE

    def test_report_persisted_to_disk(self):
        _run(self.scheduler.run_cycle({}, cycle_id="test_persist"))
        report_path = Path(self.actions_dir) / "report_test_persist.json"
        assert report_path.exists()
        with open(report_path) as f:
            data = json.load(f)
        assert data["cycle_id"] == "test_persist"

    def test_weight_adjust_action(self):
        analysis = {
            "recommendations": [
                {
                    "type": "adjust_weights",
                    "target": "security",
                    "current_value": 0.20,
                    "proposed_value": 0.25,
                    "confidence": 0.85,
                    "description": "Increase security weight based on under-routing",
                },
            ],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_weight"))
        assert report.actions_created == 1
        assert report.actions[0].action_type == "weight_adjust"

    def test_low_confidence_recommendation_ignored(self):
        analysis = {
            "recommendations": [
                {
                    "type": "adjust_weights",
                    "target": "creative",
                    "current_value": -0.05,
                    "proposed_value": -0.10,
                    "confidence": 0.3,
                    "description": "Low confidence adjustment",
                },
            ],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_lowconf"))
        assert report.actions_created == 0

    def test_low_failure_count_pattern_ignored(self):
        analysis = {
            "failure_patterns": [
                {"domain": "coding", "count": 1, "sample_prompts": []},
            ],
        }
        report = _run(self.scheduler.run_cycle(analysis, cycle_id="test_lowcount"))
        assert report.evals_created == 0


# ═══════════════════════════════════════════════════════════════
# MORNING REPORT TESTS
# ═══════════════════════════════════════════════════════════════

class TestMorningReporter:
    """Tests for MorningReporter generation and formatting."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.interaction_db = os.path.join(self.tmpdir, "interactions.db")
        self.cron_db = os.path.join(self.tmpdir, "cron.db")
        self.evolution_dir = os.path.join(self.tmpdir, "evolution")
        self.actions_dir = os.path.join(self.tmpdir, "actions")
        os.makedirs(self.evolution_dir, exist_ok=True)
        os.makedirs(self.actions_dir, exist_ok=True)

        # Write a minimal routing config
        self.routing_config = os.path.join(self.tmpdir, "routing.yaml")
        with open(self.routing_config, "w") as f:
            yaml.dump({
                "budget": {
                    "opus_daily_usd": 15.0,
                    "opus_monthly_usd": 100.0,
                }
            }, f)

        self.weights_config = os.path.join(self.tmpdir, "weights.yaml")
        with open(self.weights_config, "w") as f:
            yaml.dump({"version": 2}, f)

        self.reporter = MorningReporter(
            interaction_db=self.interaction_db,
            cron_db=self.cron_db,
            evolution_dir=self.evolution_dir,
            actions_dir=self.actions_dir,
            routing_config=self.routing_config,
            weights_config=self.weights_config,
        )

    def test_generate_empty_report(self):
        """Report generation works with no data."""
        report = _run(self.reporter.generate())
        assert report.generated_at != ""
        assert report.total_requests == 0
        assert report.period_hours == 24

    def test_format_telegram_not_empty(self):
        report = MorningReportData(
            generated_at="2026-03-21T07:00:00Z",
            total_requests=150,
            tier_distribution={1: 100, 2: 40, 4: 10},
            failure_count=5,
            failure_rate_pct=3.3,
            total_cost_usd=2.50,
            corpus_pairs_total=45,
            training_threshold=100,
        )
        text = self.reporter.format_telegram(report)
        assert "ABLE MORNING REPORT" in text
        assert "150" in text
        assert "T1:100" in text
        assert "T2:40" in text
        assert "$2.50" in text

    def test_format_telegram_within_limit(self):
        """Telegram messages must be under 4096 chars."""
        report = MorningReportData(
            generated_at="2026-03-21T07:00:00Z",
            total_requests=9999,
            recommendations=["Recommendation " * 50] * 20,
            pending_actions=[{"action_type": "cron", "name": f"action-{i}"} for i in range(50)],
        )
        text = self.reporter.format_telegram(report)
        assert len(text) <= 4096

    def test_corpus_ready_shown_when_threshold_met(self):
        report = MorningReportData(
            generated_at="2026-03-21T07:00:00Z",
            corpus_pairs_total=150,
            corpus_ready_for_training=True,
            training_threshold=100,
        )
        text = self.reporter.format_telegram(report)
        assert "YES" in text

    def test_corpus_not_ready_shows_percentage(self):
        report = MorningReportData(
            generated_at="2026-03-21T07:00:00Z",
            corpus_pairs_total=50,
            corpus_ready_for_training=False,
            training_threshold=100,
        )
        text = self.reporter.format_telegram(report)
        assert "50%" in text

    def test_recommendations_generated_for_high_failure_rate(self):
        report = MorningReportData(failure_rate_pct=15.0)
        self.reporter._generate_recommendations(report)
        assert any("failure rate" in r.lower() for r in report.recommendations)

    def test_recommendations_generated_for_high_override_rate(self):
        report = MorningReportData(override_rate_pct=20.0)
        self.reporter._generate_recommendations(report)
        assert any("override" in r.lower() for r in report.recommendations)

    def test_recommendations_generated_for_budget_alert(self):
        report = MorningReportData(
            opus_daily_spend_usd=13.0,
            opus_daily_budget_usd=15.0,
        )
        self.reporter._generate_recommendations(report)
        assert any("budget" in r.lower() for r in report.recommendations)

    def test_recommendations_for_corpus_ready(self):
        report = MorningReportData(
            corpus_pairs_total=120,
            corpus_ready_for_training=True,
            training_threshold=100,
        )
        self.reporter._generate_recommendations(report)
        assert any("fine-tuning" in r.lower() or "h100" in r.lower() for r in report.recommendations)

    def test_recommendations_for_pending_actions(self):
        report = MorningReportData(
            pending_actions=[{"id": "x", "action_type": "cron_create"}],
        )
        self.reporter._generate_recommendations(report)
        assert any("pending" in r.lower() for r in report.recommendations)

    def test_recommendations_for_no_evolution(self):
        report = MorningReportData(
            evolution_cycles_run=0,
            total_requests=50,
        )
        self.reporter._generate_recommendations(report)
        assert any("daemon" in r.lower() for r in report.recommendations)

    def test_generate_with_interaction_db(self):
        """Test with actual SQLite data."""
        conn = sqlite3.connect(self.interaction_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interaction_log (
                id INTEGER PRIMARY KEY,
                timestamp REAL,
                selected_tier INTEGER,
                selected_provider TEXT,
                success INTEGER,
                escalated INTEGER DEFAULT 0,
                cost_usd REAL
            )
        """)
        now = time.time()
        # Insert some test data
        for i in range(10):
            conn.execute(
                "INSERT INTO interaction_log (timestamp, selected_tier, selected_provider, success, cost_usd) "
                "VALUES (?, ?, ?, ?, ?)",
                (now - i * 100, 1, "gpt-5.4-mini", 1, 0.001),
            )
        # Insert a failure
        conn.execute(
            "INSERT INTO interaction_log (timestamp, selected_tier, selected_provider, success, cost_usd) "
            "VALUES (?, ?, ?, ?, ?)",
            (now - 50, 2, "mimo-v2-pro", 0, 0.01),
        )
        conn.commit()
        conn.close()

        report = _run(self.reporter.generate())
        assert report.total_requests == 11
        assert report.failure_count == 1
        assert 1 in report.tier_distribution
        assert report.tier_distribution[1] == 10


# ═══════════════════════════════════════════════════════════════
# CRON REGISTRATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestCronRegistration:
    """Test that evolution daemon and morning report are registered in default jobs."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(self.tmpdir, "cron.db")
        from able.scheduler.cron import CronScheduler, register_default_jobs
        self.scheduler = CronScheduler(db_path=db_path)
        register_default_jobs(self.scheduler)

    def test_evolution_daemon_registered(self):
        assert "evolution-daemon" in self.scheduler.jobs

    def test_evolution_daemon_schedule(self):
        job = self.scheduler.jobs["evolution-daemon"]
        assert job.schedule == "0 3 * * *"

    def test_morning_report_registered(self):
        assert "morning-report" in self.scheduler.jobs

    def test_morning_report_schedule(self):
        job = self.scheduler.jobs["morning-report"]
        assert job.schedule == "0 7 * * *"

    def test_evolution_daemon_timeout(self):
        job = self.scheduler.jobs["evolution-daemon"]
        assert job.timeout_seconds == 600.0

    def test_morning_report_timeout(self):
        job = self.scheduler.jobs["morning-report"]
        assert job.timeout_seconds == 120.0

    def test_original_jobs_still_registered(self):
        """New jobs don't break existing registration."""
        assert "health-check" in self.scheduler.jobs
        assert "memory-consolidation" in self.scheduler.jobs
        assert "weekly-billing-summary" in self.scheduler.jobs
        assert "audit-log-rotation" in self.scheduler.jobs


# ═══════════════════════════════════════════════════════════════
# MODEL PROMPT TEMPLATE TESTS
# ═══════════════════════════════════════════════════════════════

class TestModelPromptTemplates:
    """Test that per-model prompt templates load and validate."""

    @staticmethod
    def _project_root():
        return Path(__file__).resolve().parents[2]

    def test_gpt_5_4_mini_template_exists(self):
        path = self._project_root() / "config" / "model_prompts" / "gpt_5_4_mini.yaml"
        assert path.exists(), f"Missing: {path}"

    def test_mimo_v2_pro_template_exists(self):
        path = self._project_root() / "config" / "model_prompts" / "mimo_v2_pro.yaml"
        assert path.exists(), f"Missing: {path}"

    def test_opus_4_6_template_exists(self):
        path = self._project_root() / "config" / "model_prompts" / "opus_4_6.yaml"
        assert path.exists(), f"Missing: {path}"

    def _load_template(self, filename: str) -> dict:
        path = self._project_root() / "config" / "model_prompts" / filename
        with open(path) as f:
            return yaml.safe_load(f)

    def test_templates_have_required_fields(self):
        for filename in ["gpt_5_4_mini.yaml", "mimo_v2_pro.yaml", "opus_4_6.yaml"]:
            data = self._load_template(filename)
            assert "model" in data, f"{filename} missing 'model'"
            assert "tier" in data, f"{filename} missing 'tier'"
            assert "system_prompt" in data, f"{filename} missing 'system_prompt'"
            assert "version" in data, f"{filename} missing 'version'"
            assert "domain_overrides" in data, f"{filename} missing 'domain_overrides'"

    def test_templates_have_enricher_format(self):
        for filename in ["gpt_5_4_mini.yaml", "mimo_v2_pro.yaml", "opus_4_6.yaml"]:
            data = self._load_template(filename)
            assert "enricher_format" in data, f"{filename} missing 'enricher_format'"

    def test_system_prompts_not_empty(self):
        for filename in ["gpt_5_4_mini.yaml", "mimo_v2_pro.yaml", "opus_4_6.yaml"]:
            data = self._load_template(filename)
            assert len(data["system_prompt"].strip()) > 50, f"{filename} system prompt too short"

    def test_tier_values_correct(self):
        assert self._load_template("gpt_5_4_mini.yaml")["tier"] == 1
        assert self._load_template("mimo_v2_pro.yaml")["tier"] == 2
        assert self._load_template("opus_4_6.yaml")["tier"] == 4

    def test_anti_sycophancy_in_prompts(self):
        """All system prompts should include the anti-sycophancy directive."""
        for filename in ["gpt_5_4_mini.yaml", "mimo_v2_pro.yaml", "opus_4_6.yaml"]:
            data = self._load_template(filename)
            prompt = data["system_prompt"].lower()
            assert "sycophancy" in prompt or "direct" in prompt, (
                f"{filename} system prompt missing directness directive"
            )

    def test_security_constraint_in_prompts(self):
        """All system prompts should include security constraints."""
        for filename in ["gpt_5_4_mini.yaml", "mimo_v2_pro.yaml", "opus_4_6.yaml"]:
            data = self._load_template(filename)
            prompt = data["system_prompt"].lower()
            assert "api key" in prompt or "secret" in prompt, (
                f"{filename} system prompt missing security constraint"
            )
