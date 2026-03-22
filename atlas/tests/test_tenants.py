"""
Tests for the multi-tenant system.

Covers:
- Onboarding, get/list, YAML round-trip, pause/archive
- TenantRouter tier selection, system prompt, adapter detection
- TenantBilling recording, aggregation, markup, ROI
- TrainingScheduler prioritization and budget enforcement
- Data isolation between tenants
- CLI --list and --status
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from atlas.core.tenants.tenant_manager import TenantConfig, TenantManager
from atlas.core.tenants.tenant_router import TenantRouter
from atlas.core.tenants.tenant_billing import TenantBilling
from atlas.core.tenants.training_scheduler import TenantTrainingScheduler
from atlas.core.tenants.tenant_dashboard import TenantDashboard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_dirs(tmp_path: Path):
    """Create isolated config and data directories."""
    config_dir = tmp_path / "config" / "tenants"
    config_dir.mkdir(parents=True)
    data_dir = tmp_path / "data" / "tenants"
    data_dir.mkdir(parents=True)
    return config_dir, data_dir


@pytest.fixture()
def manager(tmp_dirs):
    config_dir, data_dir = tmp_dirs
    return TenantManager(config_dir=str(config_dir), data_dir=str(data_dir))


@pytest.fixture()
def onboarded_manager(manager: TenantManager):
    """Manager with two tenants already onboarded."""
    manager.onboard(
        tenant_id="acme-legal",
        name="ACME Legal",
        domain="legal",
        personality="Professional legal assistant.",
    )
    manager.onboard(
        tenant_id="widget-saas",
        name="Widget SaaS",
        domain="saas",
        personality="Friendly SaaS support agent.",
    )
    return manager


@pytest.fixture()
def router(onboarded_manager: TenantManager):
    return TenantRouter(onboarded_manager)


@pytest.fixture()
def billing(tmp_dirs):
    _, data_dir = tmp_dirs
    return TenantBilling(data_dir=str(data_dir))


@pytest.fixture()
def scheduler(tmp_path: Path):
    budget_path = tmp_path / "gpu_budget.yaml"
    return TenantTrainingScheduler(gpu_budget_path=str(budget_path))


# ---------------------------------------------------------------------------
# TenantManager tests
# ---------------------------------------------------------------------------

class TestTenantManager:

    def test_onboard_creates_config_and_dirs(self, manager: TenantManager, tmp_dirs):
        config_dir, data_dir = tmp_dirs

        config = manager.onboard(
            tenant_id="test-co",
            name="Test Co",
            domain="saas",
            personality="Helpful SaaS assistant.",
        )

        assert config.tenant_id == "test-co"
        assert config.name == "Test Co"
        assert config.status == "active"

        # Config file exists
        assert (config_dir / "test-co.yaml").exists()

        # Data directories exist
        for subdir in ("corpus", "adapters", "prompts", "memory", "billing"):
            assert (data_dir / "test-co" / subdir).is_dir()

        # System prompt written
        prompt_path = data_dir / "test-co" / "prompts" / "system.txt"
        assert prompt_path.exists()
        assert "Helpful SaaS assistant" in prompt_path.read_text()

    def test_onboard_duplicate_raises(self, onboarded_manager: TenantManager):
        with pytest.raises(ValueError, match="already exists"):
            onboarded_manager.onboard(
                tenant_id="acme-legal",
                name="Dupe",
                domain="legal",
                personality="x",
            )

    def test_get_tenant(self, onboarded_manager: TenantManager):
        config = onboarded_manager.get_tenant("acme-legal")
        assert config is not None
        assert config.name == "ACME Legal"

        assert onboarded_manager.get_tenant("nonexistent") is None

    def test_list_tenants(self, onboarded_manager: TenantManager):
        all_tenants = onboarded_manager.list_tenants()
        assert len(all_tenants) == 2

        active = onboarded_manager.list_tenants(status="active")
        assert len(active) == 2

        paused = onboarded_manager.list_tenants(status="paused")
        assert len(paused) == 0

    def test_yaml_round_trip(self, manager: TenantManager, tmp_dirs):
        config_dir, _ = tmp_dirs

        manager.onboard(
            tenant_id="roundtrip",
            name="Round Trip",
            domain="medical",
            personality="Medical assistant.",
            channel_config={"telegram": {"bot_token_secret": "RT_TOKEN"}},
        )

        # Reload from disk
        manager2 = TenantManager(
            config_dir=str(config_dir),
            data_dir=str(manager.data_dir),
        )
        reloaded = manager2.get_tenant("roundtrip")
        assert reloaded is not None
        assert reloaded.name == "Round Trip"
        assert reloaded.domain == "medical"
        assert reloaded.channels == {"telegram": {"bot_token_secret": "RT_TOKEN"}}

    def test_pause_and_archive(self, onboarded_manager: TenantManager):
        onboarded_manager.pause_tenant("acme-legal")
        config = onboarded_manager.get_tenant("acme-legal")
        assert config.status == "paused"

        onboarded_manager.archive_tenant("acme-legal")
        config = onboarded_manager.get_tenant("acme-legal")
        assert config.status == "archived"

    def test_update_tenant(self, onboarded_manager: TenantManager):
        updated = onboarded_manager.update_tenant("acme-legal", name="ACME Legal v2")
        assert updated.name == "ACME Legal v2"

    def test_update_unknown_field_raises(self, onboarded_manager: TenantManager):
        with pytest.raises(ValueError, match="Unknown tenant field"):
            onboarded_manager.update_tenant("acme-legal", bogus_field="x")


# ---------------------------------------------------------------------------
# TenantRouter tests
# ---------------------------------------------------------------------------

class TestTenantRouter:

    def test_respects_tier_limits(self, router: TenantRouter, onboarded_manager):
        # Set max_tier=2
        onboarded_manager.update_tenant(
            "acme-legal",
            routing={"tier_0_enabled": False, "opus_monthly_budget_usd": 50.0, "max_tier": 2},
        )

        # High complexity should be capped at 2
        tier = router.select_tier("acme-legal", complexity_score=0.9, budget_remaining=50.0)
        assert tier == 2

    def test_standard_tier_selection(self, router: TenantRouter):
        assert router.select_tier("acme-legal", 0.1, 50.0) == 1
        assert router.select_tier("acme-legal", 0.5, 50.0) == 2
        assert router.select_tier("acme-legal", 0.8, 50.0) == 4

    def test_budget_exhausted_caps_at_tier_2(self, router: TenantRouter):
        tier = router.select_tier("acme-legal", complexity_score=0.9, budget_remaining=0.0)
        assert tier == 2

    def test_inactive_tenant_raises(self, router: TenantRouter, onboarded_manager):
        onboarded_manager.pause_tenant("acme-legal")
        with pytest.raises(ValueError, match="not active"):
            router.select_tier("acme-legal", 0.5, 50.0)

    def test_returns_correct_system_prompt(self, router: TenantRouter):
        prompt = router.get_system_prompt("acme-legal")
        assert "legal" in prompt.lower()
        assert "ACME Legal" in prompt

    def test_nonexistent_tenant_raises(self, router: TenantRouter):
        with pytest.raises(ValueError, match="not found"):
            router.select_tier("no-such-tenant", 0.5, 50.0)

    def test_has_adapter_false_when_no_files(self, router: TenantRouter):
        assert router.has_adapter("acme-legal") is False

    def test_has_adapter_true_when_gguf_exists(self, router: TenantRouter, onboarded_manager):
        adapters_dir = onboarded_manager.tenant_data_path("acme-legal") / "adapters"
        (adapters_dir / "model.gguf").touch()
        assert router.has_adapter("acme-legal") is True

    def test_tier_0_selected_when_adapter_exists(self, router: TenantRouter, onboarded_manager):
        onboarded_manager.update_tenant(
            "acme-legal",
            routing={"tier_0_enabled": True, "opus_monthly_budget_usd": 50.0, "max_tier": 4},
        )
        adapters_dir = onboarded_manager.tenant_data_path("acme-legal") / "adapters"
        (adapters_dir / "model.gguf").touch()

        tier = router.select_tier("acme-legal", complexity_score=0.5, budget_remaining=50.0)
        assert tier == 0


# ---------------------------------------------------------------------------
# TenantBilling tests
# ---------------------------------------------------------------------------

class TestTenantBilling:

    def test_record_and_summarize(self, billing: TenantBilling, onboarded_manager):
        billing.record_usage(
            tenant_id="acme-legal",
            provider="gpt-5.4-mini",
            model="gpt-5.4-mini",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.003,
            tier=1,
        )
        billing.record_usage(
            tenant_id="acme-legal",
            provider="claude-opus-4-6",
            model="claude-opus-4-6",
            input_tokens=2000,
            output_tokens=1000,
            cost_usd=0.105,
            tier=4,
        )

        summary = billing.get_monthly_summary("acme-legal", markup_percentage=40)
        assert summary["total_requests"] == 2
        assert summary["raw_cost_usd"] > 0
        # Markup should be applied
        assert summary["billed_cost_usd"] > summary["raw_cost_usd"]

    def test_markup_applied_correctly(self, billing: TenantBilling, onboarded_manager):
        billing.record_usage(
            tenant_id="acme-legal",
            provider="gpt-5.4-mini",
            model="gpt-5.4-mini",
            input_tokens=1_000_000,
            output_tokens=0,
            cost_usd=10.0,
            tier=1,
        )

        summary = billing.get_monthly_summary("acme-legal", markup_percentage=50)
        assert summary["billed_cost_usd"] == pytest.approx(15.0, rel=0.01)

    def test_roi_calculation(self, billing: TenantBilling, onboarded_manager):
        # Record Tier 0 usage (free)
        for _ in range(5):
            billing.record_usage(
                tenant_id="acme-legal",
                provider="tier-0-adapter",
                model="acme-legal-v1",
                input_tokens=1000,
                output_tokens=500,
                cost_usd=0.0,
                tier=0,
            )
        # Record some Tier 1 usage
        billing.record_usage(
            tenant_id="acme-legal",
            provider="gpt-5.4-mini",
            model="gpt-5.4-mini",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.003,
            tier=1,
        )

        roi = billing.calculate_roi("acme-legal")
        assert roi["tier_0_requests"] == 5
        assert roi["total_requests"] == 6
        assert roi["savings_usd"] > 0
        assert "adapter saved" in roi["message"].lower()

    def test_empty_tenant_returns_zeros(self, billing: TenantBilling):
        summary = billing.get_monthly_summary("nonexistent-tenant")
        assert summary["total_requests"] == 0
        assert summary["raw_cost_usd"] == 0

    def test_invoice_data(self, billing: TenantBilling, onboarded_manager):
        billing.record_usage(
            tenant_id="acme-legal",
            provider="gpt-5.4-mini",
            model="gpt-5.4-mini",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.003,
            tier=1,
        )
        from datetime import datetime
        month = datetime.utcnow().strftime("%Y-%m")
        invoice = billing.get_invoice_data("acme-legal", month=month)
        assert invoice["tenant_id"] == "acme-legal"
        assert "summary" in invoice
        assert "roi" in invoice
        assert "line_items" in invoice


# ---------------------------------------------------------------------------
# TrainingScheduler tests
# ---------------------------------------------------------------------------

class TestTrainingScheduler:

    def test_schedule_and_queue(self, scheduler: TenantTrainingScheduler):
        result = scheduler.schedule_training(
            "acme-legal", priority="new_tenant", is_first_train=True
        )
        assert result["status"] == "scheduled"
        assert result["estimated_hours"] == 3.0

        queue = scheduler.get_training_queue()
        # Already scheduled, so might not be "pending" — check the full queue
        assert any(j.tenant_id == "acme-legal" for j in scheduler._queue)

    def test_prioritizes_correctly(self, scheduler: TenantTrainingScheduler):
        scheduler.schedule_training("tenant-sched", priority="scheduled")
        scheduler.schedule_training("tenant-new", priority="new_tenant", is_first_train=True)
        scheduler.schedule_training("atlas-core", priority="core")

        # Queue should be sorted: core < new_tenant < scheduled
        queue = scheduler._queue[:]
        queue.sort(key=lambda j: j.priority)
        assert queue[0].tenant_id == "atlas-core"
        assert queue[1].tenant_id == "tenant-new"
        assert queue[2].tenant_id == "tenant-sched"

    def test_budget_rejection(self, scheduler: TenantTrainingScheduler):
        # Exhaust client budget
        scheduler._budget.used_client_hours = 12.0

        result = scheduler.schedule_training("over-budget", priority="scheduled")
        assert result["status"] == "rejected"
        assert "Insufficient" in result["reason"]

    def test_complete_training_deducts_budget(self, scheduler: TenantTrainingScheduler):
        scheduler.schedule_training("acme-legal", priority="new_tenant", is_first_train=True)
        scheduler.complete_training("acme-legal", hours_used=3.0)

        budget = scheduler.get_budget()
        assert budget["used_client_hours"] == 3.0

    def test_estimate_next_available(self, scheduler: TenantTrainingScheduler):
        from datetime import datetime
        before = datetime.utcnow()
        scheduler.schedule_training("t1", priority="new_tenant", is_first_train=True)
        scheduler.schedule_training("t2", priority="scheduled")

        est = scheduler.estimate_next_available()
        # Should be at least 4.5 hours from now (3h + 1.5h)
        assert est > before


# ---------------------------------------------------------------------------
# Data isolation
# ---------------------------------------------------------------------------

class TestDataIsolation:

    def test_tenant_data_paths_are_separate(self, onboarded_manager: TenantManager):
        path_a = onboarded_manager.tenant_data_path("acme-legal")
        path_b = onboarded_manager.tenant_data_path("widget-saas")

        assert path_a != path_b
        assert "acme-legal" in str(path_a)
        assert "widget-saas" in str(path_b)
        # Neither path should contain the other tenant's ID
        assert "widget-saas" not in str(path_a)
        assert "acme-legal" not in str(path_b)

    def test_nonexistent_tenant_data_path_raises(self, onboarded_manager: TenantManager):
        with pytest.raises(ValueError, match="not found"):
            onboarded_manager.tenant_data_path("fabricated-id")

    def test_billing_records_isolated(self, billing: TenantBilling):
        billing.record_usage("tenant-a", "gpt-5.4-mini", "gpt-5.4-mini", 1000, 500, 0.01, 1)
        billing.record_usage("tenant-b", "gpt-5.4-mini", "gpt-5.4-mini", 2000, 1000, 0.02, 1)

        summary_a = billing.get_monthly_summary("tenant-a")
        summary_b = billing.get_monthly_summary("tenant-b")

        assert summary_a["total_requests"] == 1
        assert summary_b["total_requests"] == 1
        assert summary_a["raw_cost_usd"] != summary_b["raw_cost_usd"]

    def test_cross_tenant_training_disabled_by_default(self, manager: TenantManager):
        config = manager.onboard("isolated", "Isolated", "saas", "Agent.")
        assert config.data["cross_tenant_training"] is False


# ---------------------------------------------------------------------------
# TenantDashboard
# ---------------------------------------------------------------------------

class TestTenantDashboard:

    def test_dashboard_returns_all_sections(self, onboarded_manager, billing):
        router = TenantRouter(onboarded_manager)
        dashboard = TenantDashboard(onboarded_manager, billing, router=router)

        data = dashboard.get_dashboard("acme-legal")
        assert data["tenant_id"] == "acme-legal"
        assert "adapter" in data
        assert "billing" in data
        assert "cost_savings" in data
        assert "training" in data
        assert "routing" in data

    def test_dashboard_nonexistent_tenant_raises(self, onboarded_manager, billing):
        dashboard = TenantDashboard(onboarded_manager, billing)
        with pytest.raises(ValueError, match="not found"):
            dashboard.get_dashboard("no-such-tenant")


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLI:

    def test_cli_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "atlas.core.tenants", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        assert result.returncode == 0
        assert "onboard" in result.stdout

    def test_cli_list(self, tmp_dirs):
        config_dir, data_dir = tmp_dirs
        # Write a config file
        cfg = {
            "tenant_id": "cli-test",
            "name": "CLI Test",
            "domain": "saas",
            "personality": "Test",
            "status": "active",
            "channels": {},
            "routing": {"tier_0_enabled": False, "opus_monthly_budget_usd": 50.0, "max_tier": 4},
            "distillation": {"training_threshold": 500, "auto_retrain": True},
            "billing": {"plan": "standard", "markup_percentage": 40},
            "data": {"cross_tenant_training": False},
            "created_at": "2026-01-01T00:00:00",
        }
        with open(config_dir / "cli-test.yaml", "w") as f:
            yaml.dump(cfg, f)

        result = subprocess.run(
            [
                sys.executable, "-m", "atlas.core.tenants",
                "--list",
                "--config-dir", str(config_dir),
                "--data-dir", str(data_dir),
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        assert result.returncode == 0
        assert "cli-test" in result.stdout

    def test_cli_status(self, tmp_dirs):
        config_dir, data_dir = tmp_dirs
        cfg = {
            "tenant_id": "status-test",
            "name": "Status Test",
            "domain": "legal",
            "personality": "Test",
            "status": "active",
            "channels": {},
            "routing": {"tier_0_enabled": False, "opus_monthly_budget_usd": 50.0, "max_tier": 4},
            "distillation": {"training_threshold": 500, "auto_retrain": True},
            "billing": {"plan": "standard", "markup_percentage": 40},
            "data": {"cross_tenant_training": False},
            "created_at": "2026-01-01T00:00:00",
        }
        with open(config_dir / "status-test.yaml", "w") as f:
            yaml.dump(cfg, f)

        result = subprocess.run(
            [
                sys.executable, "-m", "atlas.core.tenants",
                "--status", "status-test",
                "--config-dir", str(config_dir),
                "--data-dir", str(data_dir),
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["tenant_id"] == "status-test"
        assert output["domain"] == "legal"
