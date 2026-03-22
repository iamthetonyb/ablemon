"""
Tests for the multi-tenant system.

Covers: TenantManager, TenantRouter, TenantBilling,
        TenantTrainingScheduler, TenantDashboard.
"""

import os
import sys
from pathlib import Path

import pytest
import yaml

# Ensure atlas package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_dirs(tmp_path):
    """Create isolated temp directories for config, data, and DB."""
    config_dir = tmp_path / "config" / "tenants"
    config_dir.mkdir(parents=True)
    data_dir = tmp_path / "tenants"
    data_dir.mkdir(parents=True)
    db_path = str(tmp_path / "test_tenants.db")
    billing_db = str(tmp_path / "test_billing.db")
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    return {
        "config_dir": str(config_dir),
        "data_dir": data_dir,
        "db_path": db_path,
        "billing_db": billing_db,
        "log_dir": log_dir,
    }


@pytest.fixture
def manager(tmp_dirs):
    from core.tenants.tenant_manager import TenantManager
    return TenantManager(
        config_dir=tmp_dirs["config_dir"],
        data_dir=tmp_dirs["data_dir"],
        db_path=tmp_dirs["db_path"],
    )


@pytest.fixture
def router(tmp_dirs):
    from core.tenants.tenant_router import TenantRouter
    return TenantRouter(
        config_dir=tmp_dirs["config_dir"],
        data_dir=tmp_dirs["data_dir"],
    )


@pytest.fixture
def billing(tmp_dirs):
    from core.tenants.tenant_billing import TenantBilling
    return TenantBilling(
        db_path=tmp_dirs["billing_db"],
        log_dir=tmp_dirs["log_dir"],
    )


@pytest.fixture
def scheduler(tmp_dirs):
    from core.tenants.training_scheduler import TenantTrainingScheduler
    return TenantTrainingScheduler(
        config_dir=tmp_dirs["config_dir"],
        data_dir=tmp_dirs["data_dir"],
        monthly_gpu_budget_hours=12.0,
    )


@pytest.fixture
def dashboard(tmp_dirs):
    from core.tenants.tenant_dashboard import TenantDashboard
    return TenantDashboard(
        config_dir=tmp_dirs["config_dir"],
        data_dir=tmp_dirs["data_dir"],
    )


async def _create_tenant(manager, tenant_id="test-tenant", domain="legal"):
    """Helper to onboard a test tenant."""
    return await manager.onboard(
        tenant_id=tenant_id,
        domain=domain,
        personality="Professional legal assistant for contract review.",
    )


# ═══════════════════════════════════════════════════════════════
# TENANT MANAGER TESTS
# ═══════════════════════════════════════════════════════════════


class TestTenantManager:

    @pytest.mark.asyncio
    async def test_onboard_creates_config_and_dirs(self, manager, tmp_dirs):
        result = await _create_tenant(manager)

        assert result["tenant_id"] == "test-tenant"
        assert result["domain"] == "legal"
        assert result["status"] == "active"

        # Config file created
        config_path = Path(tmp_dirs["config_dir"]) / "test-tenant.yaml"
        assert config_path.exists()

        # Data directories created
        data_path = tmp_dirs["data_dir"] / "test-tenant"
        for subdir in ("corpus", "adapters", "prompts", "skills", "memory"):
            assert (data_path / subdir).exists()

        # System prompt created
        prompt = (data_path / "prompts" / "system.txt").read_text()
        assert "legal" in prompt

    @pytest.mark.asyncio
    async def test_onboard_rejects_duplicate(self, manager):
        await _create_tenant(manager)
        with pytest.raises(ValueError, match="already exists"):
            await _create_tenant(manager)

    @pytest.mark.asyncio
    async def test_onboard_validates_tenant_id(self, manager):
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            await manager.onboard("AB", "legal", "A personality description here.")

    @pytest.mark.asyncio
    async def test_onboard_validates_domain(self, manager):
        with pytest.raises(ValueError, match="Invalid domain"):
            await manager.onboard("good-id", "invalid-domain", "A personality description here.")

    @pytest.mark.asyncio
    async def test_onboard_validates_personality(self, manager):
        with pytest.raises(ValueError, match="Personality"):
            await manager.onboard("good-id", "legal", "short")

    @pytest.mark.asyncio
    async def test_get_tenant(self, manager):
        await _create_tenant(manager)
        tenant = await manager.get_tenant("test-tenant")
        assert tenant is not None
        assert tenant["domain"] == "legal"

    @pytest.mark.asyncio
    async def test_get_tenant_not_found(self, manager):
        result = await manager.get_tenant("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_tenants(self, manager):
        await _create_tenant(manager, "tenant-one", "legal")
        await _create_tenant(manager, "tenant-two", "tech")

        tenants = await manager.list_tenants()
        assert len(tenants) == 2
        ids = [t["tenant_id"] for t in tenants]
        assert "tenant-one" in ids
        assert "tenant-two" in ids

    @pytest.mark.asyncio
    async def test_pause_tenant(self, manager):
        await _create_tenant(manager)
        await manager.pause_tenant("test-tenant")

        tenant = await manager.get_tenant("test-tenant")
        assert tenant["status"] == "paused"

    @pytest.mark.asyncio
    async def test_archive_tenant(self, manager):
        await _create_tenant(manager)
        await manager.archive_tenant("test-tenant")

        tenant = await manager.get_tenant("test-tenant")
        assert tenant["status"] == "archived"

    @pytest.mark.asyncio
    async def test_get_status(self, manager):
        await _create_tenant(manager)
        status = await manager.get_status("test-tenant")
        assert status["tenant_id"] == "test-tenant"
        assert status["domain"] == "legal"
        assert status["status"] == "active"
        assert status["corpus_files"] == 0
        assert status["has_adapter"] is False

    @pytest.mark.asyncio
    async def test_data_isolation_enforced(self, manager):
        """cross_tenant_training=True must be rejected."""
        from core.tenants.tenant_manager import TenantConfig
        config = TenantConfig(
            tenant_id="bad-tenant",
            domain="legal",
            personality="A valid personality description.",
            data={"cross_tenant_training": True},
        )
        errors = config.validate()
        assert any("cross_tenant_training" in e for e in errors)


# ═══════════════════════════════════════════════════════════════
# TENANT ROUTER TESTS
# ═══════════════════════════════════════════════════════════════


class TestTenantRouter:

    @pytest.mark.asyncio
    async def test_route_tier_1_low_complexity(self, manager, router):
        await _create_tenant(manager)
        result = await router.route("test-tenant", complexity_score=0.2)

        assert result.tenant_id == "test-tenant"
        assert result.tier == 1
        assert result.provider == "gpt-5.4-mini"
        assert "legal" in result.system_prompt

    @pytest.mark.asyncio
    async def test_route_tier_2_medium_complexity(self, manager, router):
        await _create_tenant(manager)
        result = await router.route("test-tenant", complexity_score=0.5)

        assert result.tier == 2
        assert result.provider == "gpt-5.4"

    @pytest.mark.asyncio
    async def test_route_tier_4_high_complexity(self, manager, router):
        await _create_tenant(manager)
        result = await router.route("test-tenant", complexity_score=0.8)

        assert result.tier == 4
        assert result.provider == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_route_tier_0_with_adapter(self, manager, router, tmp_dirs):
        await _create_tenant(manager)

        # Enable tier 0 in config
        config_path = Path(tmp_dirs["config_dir"]) / "test-tenant.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        config["routing"]["tier_0_enabled"] = True
        config_path.write_text(yaml.dump(config, default_flow_style=False))

        # Create fake adapter
        adapter_dir = tmp_dirs["data_dir"] / "test-tenant" / "adapters"
        adapter_dir.mkdir(parents=True, exist_ok=True)
        (adapter_dir / "v1.gguf").write_bytes(b"fake-adapter")

        result = await router.route("test-tenant", complexity_score=0.2)
        assert result.tier == 0
        assert result.provider == "ollama-tenant"
        assert result.adapter_path is not None

    @pytest.mark.asyncio
    async def test_route_not_found(self, router):
        with pytest.raises(ValueError, match="not found"):
            await router.route("nonexistent")

    @pytest.mark.asyncio
    async def test_route_paused_tenant(self, manager, router):
        await _create_tenant(manager)
        await manager.pause_tenant("test-tenant")

        with pytest.raises(ValueError, match="paused"):
            await router.route("test-tenant")

    @pytest.mark.asyncio
    async def test_get_routing_summary(self, manager, router):
        await _create_tenant(manager)
        summary = await router.get_routing_summary("test-tenant")
        assert summary["tenant_id"] == "test-tenant"
        assert summary["has_adapter"] is False
        assert summary["tier_0_enabled"] is False


# ═══════════════════════════════════════════════════════════════
# TENANT BILLING TESTS
# ═══════════════════════════════════════════════════════════════


class TestTenantBilling:

    @pytest.mark.asyncio
    async def test_track_usage(self, manager, billing, tmp_dirs):
        await _create_tenant(manager)

        # TenantBilling._get_markup needs to find the config
        billing._get_markup = lambda tid, config_dir=tmp_dirs["config_dir"]: 0.40

        record = await billing.track_usage(
            tenant_id="test-tenant",
            tier=1,
            provider="gpt-5.4-mini",
            model_id="gpt-5.4-mini",
            input_tokens=1000,
            output_tokens=500,
        )

        assert record.tenant_id == "test-tenant"
        assert record.tier == 1
        assert record.raw_cost == 0.0  # GPT 5.4 Mini is free
        assert record.billed_cost == 0.0  # 0 * 1.4 = 0

    @pytest.mark.asyncio
    async def test_track_opus_usage_with_markup(self, billing):
        billing._get_markup = lambda tid, **kw: 0.40

        record = await billing.track_usage(
            tenant_id="test-tenant",
            tier=4,
            provider="claude-opus-4-6",
            model_id="claude-opus-4-6",
            input_tokens=1_000_000,
            output_tokens=100_000,
        )

        # Raw: 15.0 + 7.5 = 22.5
        assert abs(record.raw_cost - 22.5) < 0.01
        # Billed: 22.5 * 1.4 = 31.5
        assert abs(record.billed_cost - 31.5) < 0.01

    @pytest.mark.asyncio
    async def test_tier_0_tracks_savings(self, billing):
        billing._get_markup = lambda tid, **kw: 0.40

        record = await billing.track_usage(
            tenant_id="test-tenant",
            tier=0,
            provider="ollama-tenant",
            model_id="tenant-test",
            input_tokens=1_000_000,
            output_tokens=500_000,
        )

        assert record.billed_cost == 0.0  # Tier 0 is free
        assert record.tier_0_saved > 0  # Would have cost money

    @pytest.mark.asyncio
    async def test_monthly_summary(self, billing):
        billing._get_markup = lambda tid, **kw: 0.40

        await billing.track_usage(
            "test-tenant", 1, "gpt-5.4-mini", "gpt-5.4-mini", 1000, 500
        )
        await billing.track_usage(
            "test-tenant", 4, "claude-opus-4-6", "claude-opus-4-6", 100_000, 50_000
        )

        summary = await billing.get_monthly_summary("test-tenant")
        assert summary["request_count"] == 2
        assert summary["total_raw_cost"] >= 0

    @pytest.mark.asyncio
    async def test_gpu_hours_tracking(self, billing):
        result = await billing.record_gpu_hours("test-tenant", 2.0, gpu_hours_included=3.0)
        assert result["gpu_hours_used"] == 2.0
        assert result["overage_hours"] == 0.0
        assert result["overage_cost"] == 0.0

    @pytest.mark.asyncio
    async def test_gpu_hours_overage(self, billing):
        await billing.record_gpu_hours("test-tenant", 2.0, gpu_hours_included=3.0)
        result = await billing.record_gpu_hours("test-tenant", 2.0, gpu_hours_included=3.0)
        assert result["gpu_hours_used"] == 4.0
        assert result["overage_hours"] == 1.0
        assert result["overage_cost"] > 0


# ═══════════════════════════════════════════════════════════════
# TRAINING SCHEDULER TESTS
# ═══════════════════════════════════════════════════════════════


class TestTrainingScheduler:

    @pytest.mark.asyncio
    async def test_evaluate_new_tenant(self, manager, scheduler, tmp_dirs):
        await _create_tenant(manager)

        # Add corpus files above threshold
        corpus_dir = tmp_dirs["data_dir"] / "test-tenant" / "corpus"
        for i in range(600):
            (corpus_dir / f"doc_{i}.txt").write_text(f"Document {i}")

        job = await scheduler.evaluate_tenant("test-tenant")
        assert job.tenant_id == "test-tenant"
        assert job.priority == 2  # new_tenant
        assert job.priority_label == "new_tenant"
        assert job.corpus_size == 600

    @pytest.mark.asyncio
    async def test_evaluate_below_threshold(self, manager, scheduler):
        await _create_tenant(manager)

        job = await scheduler.evaluate_tenant("test-tenant")
        assert job.priority == 5  # retrain (below threshold)

    @pytest.mark.asyncio
    async def test_core_gets_highest_priority(self, scheduler, tmp_dirs):
        # Create a "core" config
        config_path = Path(tmp_dirs["config_dir"]) / "core.yaml"
        config_path.write_text(yaml.dump({
            "tenant_id": "core",
            "domain": "general",
            "personality": "ATLAS core system.",
            "status": "active",
            "distillation": {"training_threshold": 100},
        }))

        job = await scheduler.evaluate_tenant("core")
        assert job.priority == 1  # core
        assert job.priority_label == "core"

    @pytest.mark.asyncio
    async def test_build_schedule_respects_budget(self, manager, scheduler, tmp_dirs):
        # Create 5 tenants
        for i in range(5):
            tid = f"tenant-{i:02d}"
            await manager.onboard(tid, "legal", f"Legal assistant number {i} for testing.")
            corpus_dir = tmp_dirs["data_dir"] / tid / "corpus"
            for j in range(600):
                (corpus_dir / f"doc_{j}.txt").write_text(f"Doc {j}")

        tenant_ids = [f"tenant-{i:02d}" for i in range(5)]
        schedule = await scheduler.build_schedule(tenant_ids, gpu_hours_available=10.0)

        # 600 files = 3.1h each. 10h budget fits 3 (9.3h), defers 2
        assert len(schedule["scheduled"]) == 3
        assert len(schedule["deferred"]) == 2
        assert schedule["budget"]["hours_remaining"] >= 0

    @pytest.mark.asyncio
    async def test_trigger_training_blocked(self, manager, scheduler):
        await _create_tenant(manager)

        job = await scheduler.trigger_training("test-tenant")
        assert job.status == "blocked"
        assert "below threshold" in job.reason

    @pytest.mark.asyncio
    async def test_trigger_training_scheduled(self, manager, scheduler, tmp_dirs):
        await _create_tenant(manager)

        corpus_dir = tmp_dirs["data_dir"] / "test-tenant" / "corpus"
        for i in range(600):
            (corpus_dir / f"doc_{i}.txt").write_text(f"Doc {i}")

        job = await scheduler.trigger_training("test-tenant")
        assert job.status == "scheduled"

    @pytest.mark.asyncio
    async def test_domain_batching(self, manager, scheduler, tmp_dirs):
        await manager.onboard("legal-one", "legal", "Legal assistant one for testing purposes.")
        await manager.onboard("legal-two", "legal", "Legal assistant two for testing purposes.")
        await manager.onboard("tech-one", "tech", "Tech assistant one for testing purposes here.")

        schedule = await scheduler.build_schedule(
            ["legal-one", "legal-two", "tech-one"]
        )
        batches = schedule["domain_batches"]
        assert "legal" in batches
        assert len(batches["legal"]) == 2
        assert "tech" in batches


# ═══════════════════════════════════════════════════════════════
# TENANT DASHBOARD TESTS
# ═══════════════════════════════════════════════════════════════


class TestTenantDashboard:

    @pytest.mark.asyncio
    async def test_get_dashboard(self, manager, dashboard):
        await _create_tenant(manager)

        data = await dashboard.get_dashboard("test-tenant")
        assert data["tenant_id"] == "test-tenant"
        assert data["domain"] == "legal"
        assert data["status"] == "active"
        assert "model" in data
        assert "cost" in data
        assert "quality" in data
        assert "routing" in data

    @pytest.mark.asyncio
    async def test_dashboard_with_billing(self, manager, dashboard):
        await _create_tenant(manager)

        billing_summary = {
            "tier_0_percentage": 60.0,
            "total_tier_0_saved": 42.50,
            "request_count": 100,
            "total_billed_cost": 15.00,
        }

        data = await dashboard.get_dashboard(
            "test-tenant", billing_summary=billing_summary
        )
        assert data["cost"]["tier_0_percentage"] == 60.0
        assert data["cost"]["total_saved"] == 42.50

    @pytest.mark.asyncio
    async def test_dashboard_not_found(self, dashboard):
        with pytest.raises(ValueError, match="not found"):
            await dashboard.get_dashboard("nonexistent")

    @pytest.mark.asyncio
    async def test_update_quality_scores(self, manager, dashboard, tmp_dirs):
        await _create_tenant(manager)

        scores = await dashboard.update_quality_scores(
            "test-tenant",
            hallucination_rate=0.02,
            correctness_score=0.95,
            acceptance_rate=0.88,
        )
        assert scores["hallucination_rate"] == 0.02
        assert scores["correctness_score"] == 0.95
        assert scores["eval_count"] == 1

        # Verify persisted
        scores_path = (
            tmp_dirs["data_dir"] / "test-tenant" / "memory" / "quality_scores.yaml"
        )
        assert scores_path.exists()

    @pytest.mark.asyncio
    async def test_dashboard_with_adapter(self, manager, dashboard, tmp_dirs):
        await _create_tenant(manager)

        # Create fake adapter
        adapter_dir = tmp_dirs["data_dir"] / "test-tenant" / "adapters"
        (adapter_dir / "v1.gguf").write_bytes(b"fake")

        data = await dashboard.get_dashboard("test-tenant")
        assert data["model"]["has_adapter"] is True
        assert data["model"]["adapter_version"] == "v1.gguf"


# ═══════════════════════════════════════════════════════════════
# IMPORT SMOKE TEST
# ═══════════════════════════════════════════════════════════════


class TestImports:

    def test_all_imports(self):
        from core.tenants import (
            TenantManager,
            TenantRouter,
            TenantBilling,
            TenantTrainingScheduler,
            TenantDashboard,
        )
        assert TenantManager is not None
        assert TenantRouter is not None
        assert TenantBilling is not None
        assert TenantTrainingScheduler is not None
        assert TenantDashboard is not None
