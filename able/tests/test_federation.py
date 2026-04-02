"""Tests for the federated distillation network."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Identity tests ────────────────────────────────────────────────


class TestIdentity:
    """Tests for instance identity and network enrollment."""

    def test_create_instance_id(self, tmp_path):
        from able.core.federation.identity import get_or_create_instance_id

        iid = get_or_create_instance_id(tmp_path)
        assert iid
        assert len(iid) == 36  # UUID4 format
        # Verify file was created
        assert (tmp_path / "instance.yaml").exists()

    def test_idempotent_instance_id(self, tmp_path):
        from able.core.federation.identity import get_or_create_instance_id

        first = get_or_create_instance_id(tmp_path)
        second = get_or_create_instance_id(tmp_path)
        assert first == second

    def test_get_instance_config(self, tmp_path):
        from able.core.federation.identity import (
            get_or_create_instance_id,
            get_instance_config,
        )

        iid = get_or_create_instance_id(tmp_path)
        config = get_instance_config(tmp_path)
        assert config["instance_id"] == iid
        assert config["network_enabled"] is True
        assert config["last_sync_at"] is None

    def test_ensure_enrollment_idempotent(self, tmp_path):
        from able.core.federation.identity import (
            get_or_create_instance_id,
            ensure_network_enrollment,
        )

        iid = get_or_create_instance_id(tmp_path)
        config = ensure_network_enrollment(tmp_path)
        assert config["instance_id"] == iid

    def test_ensure_enrollment_creates_if_missing(self, tmp_path):
        from able.core.federation.identity import ensure_network_enrollment

        config = ensure_network_enrollment(tmp_path)
        assert config.get("instance_id")
        assert config["network_enabled"] is True

    def test_set_network_disabled(self, tmp_path):
        from able.core.federation.identity import (
            get_or_create_instance_id,
            set_network_enabled,
            get_instance_config,
        )

        get_or_create_instance_id(tmp_path)
        set_network_enabled(False, tmp_path)
        config = get_instance_config(tmp_path)
        assert config["network_enabled"] is False

    def test_set_network_enabled(self, tmp_path):
        from able.core.federation.identity import (
            get_or_create_instance_id,
            set_network_enabled,
            get_instance_config,
        )

        get_or_create_instance_id(tmp_path)
        set_network_enabled(False, tmp_path)
        set_network_enabled(True, tmp_path)
        config = get_instance_config(tmp_path)
        assert config["network_enabled"] is True

    def test_update_sync_cursor(self, tmp_path):
        from able.core.federation.identity import (
            get_or_create_instance_id,
            update_sync_cursor,
            get_instance_config,
        )

        get_or_create_instance_id(tmp_path)
        cursor = datetime.now(timezone.utc).isoformat()
        update_sync_cursor(cursor, domains=["security", "coding"], able_home=tmp_path)
        config = get_instance_config(tmp_path)
        assert config["last_sync_cursor"] == cursor
        assert "security" in config["domains_contributed"]
        assert "coding" in config["domains_contributed"]


# ── Models tests ──────────────────────────────────────────────────


class TestModels:
    """Tests for federation data models."""

    def test_contribution_package(self):
        from able.core.federation.models import ContributionPackage

        pkg = ContributionPackage(
            path=Path("/tmp/test.jsonl"),
            pair_count=42,
            domains={"coding": 30, "security": 12},
            instance_id="test-id",
        )
        assert pkg.pair_count == 42
        assert pkg.domains["coding"] == 30

    def test_ingest_result_merge(self):
        from able.core.federation.models import IngestResult

        a = IngestResult(accepted=5, rejected=2, duplicates=1, errors=0,
                         domains_ingested={"coding": 3, "security": 2})
        b = IngestResult(accepted=3, rejected=1, duplicates=2, errors=1,
                         domains_ingested={"coding": 2, "reasoning": 1})
        a.merge(b)
        assert a.accepted == 8
        assert a.rejected == 3
        assert a.duplicates == 3
        assert a.errors == 1
        assert a.domains_ingested["coding"] == 5
        assert a.domains_ingested["security"] == 2
        assert a.domains_ingested["reasoning"] == 1

    def test_ingest_result_total(self):
        from able.core.federation.models import IngestResult

        r = IngestResult(accepted=10, rejected=3, duplicates=5, errors=2)
        assert r.total_processed == 20


# ── Contributor tests ─────────────────────────────────────────────


class TestContributor:
    """Tests for PII scrubbing and contribution export."""

    def test_scrub_pii_email(self):
        from able.core.federation.contributor import _scrub_pii

        text = "Contact user@example.com for help"
        assert "[EMAIL]" in _scrub_pii(text)
        assert "user@example.com" not in _scrub_pii(text)

    def test_scrub_pii_phone(self):
        from able.core.federation.contributor import _scrub_pii

        assert "[PHONE]" in _scrub_pii("Call 555-123-4567")

    def test_scrub_pii_ip(self):
        from able.core.federation.contributor import _scrub_pii

        assert "[IP]" in _scrub_pii("Connect to 192.168.1.1")

    def test_scrub_pii_home_path(self):
        from able.core.federation.contributor import _scrub_pii

        assert "/[USER]/" in _scrub_pii("File at /Users/tonybenton/Desktop/file.py")
        assert "tonybenton" not in _scrub_pii("File at /Users/tonybenton/Desktop/file.py")

    def test_scrub_pii_api_key(self):
        from able.core.federation.contributor import _scrub_pii

        assert "[API_KEY]" in _scrub_pii("Key: sk-abc1234567890xyz")

    def test_scrub_pii_ssh_key(self):
        from able.core.federation.contributor import _scrub_pii

        text = "Key: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE+test"
        assert "[SSH_KEY]" in _scrub_pii(text)

    def test_scrub_for_network_valid(self):
        from able.core.federation.contributor import scrub_for_network

        result = scrub_for_network(
            prompt="Write a Python function to sort a list of integers efficiently",
            response="Here is a Python function that uses quicksort to efficiently sort a list of integers with O(n log n) average time complexity.",
            domain="coding",
            quality_score=0.92,
            content_hash="abc123",
            tags=["claude_code"],
        )
        assert result is not None
        assert result["domain"] == "coding"
        assert result["quality_score"] == 0.92

    def test_scrub_for_network_rejects_short(self):
        from able.core.federation.contributor import scrub_for_network

        result = scrub_for_network(
            prompt="hi",
            response="hey",
            domain="coding",
            quality_score=0.95,
            content_hash="abc",
            tags=[],
        )
        assert result is None

    def test_scrub_strips_tenant_specific_tag(self):
        from able.core.federation.contributor import scrub_for_network

        result = scrub_for_network(
            prompt="Explain the security implications of SQL injection in web applications",
            response="SQL injection is a code injection technique that exploits vulnerabilities in applications by inserting malicious SQL statements into input fields.",
            domain="security",
            quality_score=0.90,
            content_hash="def456",
            tags=["claude_code", "tenant_specific"],
        )
        assert result is not None
        assert "tenant_specific" not in result["tags"]


# ── Ingester tests ────────────────────────────────────────────────


def _make_contribution_jsonl(tmp_path: Path, pairs: list[dict], instance_id: str = "test-inst") -> Path:
    """Helper: write a valid contribution JSONL file."""
    filepath = tmp_path / "test-contribution.jsonl"
    meta = {
        "type": "able_network_contribution",
        "version": 1,
        "instance_id": instance_id,
        "pair_count": len(pairs),
        "domains": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(filepath, "w") as f:
        f.write(json.dumps(meta) + "\n")
        for pair in pairs:
            f.write(json.dumps(pair) + "\n")
    return filepath


def _make_valid_pair(domain: str = "coding") -> dict:
    """Helper: create a valid contribution pair."""
    return {
        "prompt": "Explain how to implement a binary search tree in Python with insert, delete, and search operations",
        "response": "Here is a complete implementation of a binary search tree (BST) in Python. The BST maintains the invariant that all left children are smaller and all right children are larger than the parent node.",
        "domain": domain,
        "quality_score": 0.90,
        "content_hash": f"sha256:{uuid.uuid4().hex}",
        "tags": ["claude_code"],
        "contributed_at": datetime.now(timezone.utc).isoformat(),
    }


class TestIngester:
    """Tests for contribution ingestion and validation."""

    def test_ingest_valid_pair(self, tmp_path):
        from able.core.federation.ingester import ingest_contribution

        store = MagicMock()
        store.save_pair = MagicMock(return_value=True)

        pairs = [_make_valid_pair()]
        filepath = _make_contribution_jsonl(tmp_path, pairs)

        result = ingest_contribution(filepath, store)
        assert result.accepted == 1
        assert result.rejected == 0
        assert result.duplicates == 0

    def test_ingest_duplicate_pair(self, tmp_path):
        from able.core.federation.ingester import ingest_contribution

        store = MagicMock()
        store.save_pair = MagicMock(return_value=False)  # Duplicate

        pairs = [_make_valid_pair()]
        filepath = _make_contribution_jsonl(tmp_path, pairs)

        result = ingest_contribution(filepath, store)
        assert result.accepted == 0
        assert result.duplicates == 1

    def test_ingest_rejects_short_prompt(self, tmp_path):
        from able.core.federation.ingester import ingest_contribution

        store = MagicMock()
        pair = _make_valid_pair()
        pair["prompt"] = "hi"
        filepath = _make_contribution_jsonl(tmp_path, [pair])

        result = ingest_contribution(filepath, store)
        assert result.rejected == 1
        assert result.accepted == 0

    def test_ingest_rejects_low_quality(self, tmp_path):
        from able.core.federation.ingester import ingest_contribution

        store = MagicMock()
        pair = _make_valid_pair()
        pair["quality_score"] = 0.1
        filepath = _make_contribution_jsonl(tmp_path, [pair])

        result = ingest_contribution(filepath, store)
        assert result.rejected == 1

    def test_ingest_rejects_missing_hash(self, tmp_path):
        from able.core.federation.ingester import ingest_contribution

        store = MagicMock()
        pair = _make_valid_pair()
        del pair["content_hash"]
        filepath = _make_contribution_jsonl(tmp_path, [pair])

        result = ingest_contribution(filepath, store)
        assert result.rejected == 1

    def test_ingest_handles_bad_json_lines(self, tmp_path):
        from able.core.federation.ingester import ingest_contribution

        store = MagicMock()
        filepath = tmp_path / "bad.jsonl"
        meta = {"type": "able_network_contribution", "version": 1}
        with open(filepath, "w") as f:
            f.write(json.dumps(meta) + "\n")
            f.write("not valid json\n")
            f.write("{also broken\n")

        result = ingest_contribution(filepath, store)
        assert result.errors == 2

    def test_ingest_rejects_invalid_type(self, tmp_path):
        from able.core.federation.ingester import ingest_contribution

        store = MagicMock()
        filepath = tmp_path / "wrong-type.jsonl"
        meta = {"type": "not_a_contribution", "version": 1}
        with open(filepath, "w") as f:
            f.write(json.dumps(meta) + "\n")

        result = ingest_contribution(filepath, store)
        assert result.errors == 1

    def test_ingest_all_inbox(self, tmp_path):
        from able.core.federation.ingester import ingest_all_inbox

        inbox = tmp_path / "inbox"
        inbox.mkdir()
        processed = tmp_path / "processed"

        store = MagicMock()
        store.save_pair = MagicMock(return_value=True)

        # Create 2 contribution files
        _make_contribution_jsonl(inbox, [_make_valid_pair()]).rename(
            inbox / "contrib1.jsonl"
        )
        _make_contribution_jsonl(inbox, [_make_valid_pair(), _make_valid_pair()]).rename(
            inbox / "contrib2.jsonl"
        )

        with patch("able.core.federation.ingester._DEFAULT_INBOX", inbox), \
             patch("able.core.federation.ingester._DEFAULT_PROCESSED", processed):
            result = ingest_all_inbox(store, inbox_dir=inbox)

        assert result.accepted == 3

    def test_ingest_stores_as_network_tenant(self, tmp_path):
        from able.core.federation.ingester import ingest_contribution

        store = MagicMock()
        store.save_pair = MagicMock(return_value=True)

        pairs = [_make_valid_pair()]
        filepath = _make_contribution_jsonl(tmp_path, pairs)

        ingest_contribution(filepath, store)

        # Check the pair was saved with tenant_id='network'
        call_args = store.save_pair.call_args[0][0]
        assert call_args.tenant_id == "network"
        assert "federation" in call_args.tags


# ── Distributor tests ─────────────────────────────────────────────


class TestDistributor:
    """Tests for distribution backend and outbox handling."""

    def test_github_backend_is_distribution_backend(self):
        from able.core.federation.distributor import (
            DistributionBackend,
            GitHubReleasesBackend,
        )

        assert isinstance(GitHubReleasesBackend(), DistributionBackend)

    @pytest.mark.asyncio
    async def test_drain_empty_outbox(self, tmp_path):
        from able.core.federation.distributor import drain_outbox

        backend = MagicMock()
        with patch("able.core.federation.distributor._DEFAULT_OUTBOX", tmp_path / "empty"):
            count = await drain_outbox(backend)
        assert count == 0


# ── Sync tests ────────────────────────────────────────────────────


class TestSync:
    """Tests for the federation sync orchestrator."""

    @pytest.mark.asyncio
    async def test_sync_skips_when_not_enrolled(self, tmp_path):
        from able.core.federation.sync import federation_sync

        result = await federation_sync(able_home=tmp_path)
        assert result["skipped"] is True

    @pytest.mark.asyncio
    async def test_sync_skips_when_disabled(self, tmp_path):
        from able.core.federation.identity import (
            get_or_create_instance_id,
            set_network_enabled,
        )
        from able.core.federation.sync import federation_sync

        get_or_create_instance_id(tmp_path)
        set_network_enabled(False, tmp_path)

        result = await federation_sync(able_home=tmp_path)
        assert result["skipped"] is True


# ── Store since parameter test ────────────────────────────────────


class TestStoreSince:
    """Tests for the since parameter on DistillationStore.get_pairs()."""

    def test_get_pairs_accepts_since(self, tmp_path):
        """Verify the since parameter is accepted without error."""
        from able.core.distillation.store import DistillationStore

        db_file = str(tmp_path / "test_distillation.db")
        store = DistillationStore(db_path=db_file)
        # Should not raise
        pairs = store.get_pairs(since=datetime.now(timezone.utc) - timedelta(hours=24))
        assert isinstance(pairs, list)


# ── Unsloth exporter tests ────────────────────────────────────────


class TestUnslothExporter:
    """Tests for the Unsloth training exporter."""

    def test_export_notebook_9b(self, tmp_path):
        from able.core.distillation.training.unsloth_exporter import UnslothExporter

        exporter = UnslothExporter(output_dir=tmp_path)
        path = exporter.export_notebook(
            "able-nano-9b",
            "data/corpus/default/v001/train.jsonl",
        )
        assert path.exists()
        assert path.suffix == ".ipynb"

        # Verify it's valid JSON notebook
        nb = json.loads(path.read_text())
        assert nb["nbformat"] == 4
        assert len(nb["cells"]) >= 8
        # Check GPU type is T4 for nano
        assert nb["metadata"]["colab"]["gpuType"] == "T4"

    def test_export_notebook_27b(self, tmp_path):
        from able.core.distillation.training.unsloth_exporter import UnslothExporter

        exporter = UnslothExporter(output_dir=tmp_path)
        path = exporter.export_notebook(
            "able-student-27b",
            "data/corpus/default/v001/train.jsonl",
            runtime="h100_session",
        )
        assert path.exists()
        nb = json.loads(path.read_text())
        assert nb["metadata"]["colab"]["gpuType"] == "A100"

    def test_export_notebook_invalid_model(self, tmp_path):
        from able.core.distillation.training.unsloth_exporter import UnslothExporter

        exporter = UnslothExporter(output_dir=tmp_path)
        with pytest.raises(ValueError, match="Unknown model"):
            exporter.export_notebook("nonexistent-model", "corpus.jsonl")

    def test_export_training_script(self, tmp_path):
        from able.core.distillation.training.unsloth_exporter import UnslothExporter

        exporter = UnslothExporter(output_dir=tmp_path)
        path = exporter.export_training_script(
            "able-nano-9b",
            "data/corpus/default/v001/train.jsonl",
        )
        assert path.exists()
        assert path.suffix == ".py"

        content = path.read_text()
        assert "FastLanguageModel" in content
        assert "Qwen3.5-9B" in content
        assert "save_pretrained_gguf" in content

    def test_notebook_contains_chatml_format(self, tmp_path):
        from able.core.distillation.training.unsloth_exporter import UnslothExporter

        exporter = UnslothExporter(output_dir=tmp_path)
        path = exporter.export_notebook("able-nano-9b", "train.jsonl")
        content = path.read_text()
        assert "apply_chat_template" in content
        assert "chatml" in content.lower() or "ChatML" in content

    def test_notebook_contains_gguf_export(self, tmp_path):
        from able.core.distillation.training.unsloth_exporter import UnslothExporter

        exporter = UnslothExporter(output_dir=tmp_path)
        path = exporter.export_notebook("able-nano-9b", "train.jsonl")
        content = path.read_text()
        assert "save_pretrained_gguf" in content
        assert "iq2_m" in content
        assert "q4_k_m" in content

    def test_export_mlx_training_script(self, tmp_path):
        from able.core.distillation.training.unsloth_exporter import UnslothExporter

        exporter = UnslothExporter(output_dir=tmp_path)
        path = exporter.export_mlx_training_script(
            "able-nano-9b", "data/corpus/default/v001/train.jsonl",
        )
        assert path.exists()
        assert path.suffix == ".sh"

        content = path.read_text()
        assert "mlx_lm.lora" in content
        assert "mlx_lm.fuse" in content
        assert "convert_hf_to_gguf" in content
        assert "ollama create" in content
        # Verify it's executable
        assert path.stat().st_mode & 0o111

    def test_export_mlx_script_invalid_model(self, tmp_path):
        from able.core.distillation.training.unsloth_exporter import UnslothExporter

        exporter = UnslothExporter(output_dir=tmp_path)
        with pytest.raises(ValueError, match="Unknown model"):
            exporter.export_mlx_training_script("nonexistent", "corpus.jsonl")

    def test_export_notebook_l4_runtime(self, tmp_path):
        from able.core.distillation.training.unsloth_exporter import UnslothExporter

        exporter = UnslothExporter(output_dir=tmp_path)
        path = exporter.export_notebook(
            "able-student-27b", "train.jsonl", runtime="l4_session",
        )
        nb = json.loads(path.read_text())
        assert nb["metadata"]["colab"]["gpuType"] == "L4"

    def test_export_notebook_a100_runtime(self, tmp_path):
        from able.core.distillation.training.unsloth_exporter import UnslothExporter

        exporter = UnslothExporter(output_dir=tmp_path)
        path = exporter.export_notebook(
            "able-nano-9b", "train.jsonl", runtime="a100_session",
        )
        nb = json.loads(path.read_text())
        assert nb["metadata"]["colab"]["gpuType"] == "A100"


# ── GPU fallback chain tests ─────────────────────────────────────


class TestGPUFallback:
    """Tests for the GPU fallback chain and multi-GPU-class support."""

    def test_27b_supports_a100_and_l4(self):
        from able.core.distillation.training.model_configs import MODEL_REGISTRY

        config = MODEL_REGISTRY["able-student-27b"]
        assert "a100_session" in config.supported_gpu_classes
        assert "l4_session" in config.supported_gpu_classes
        assert "h100_session" in config.supported_gpu_classes

    def test_9b_supports_all_gpu_classes(self):
        from able.core.distillation.training.model_configs import MODEL_REGISTRY

        config = MODEL_REGISTRY["able-nano-9b"]
        for gpu in ["t4_colab", "a100_session", "l4_session", "h100_session", "local"]:
            assert gpu in config.supported_gpu_classes

    def test_fallback_chain_order_27b(self):
        from able.core.distillation.training.model_configs import GPU_FALLBACK_CHAINS

        chain = GPU_FALLBACK_CHAINS["able-student-27b"]
        assert chain == ["h100_session", "a100_session", "l4_session"]

    def test_fallback_chain_order_9b(self):
        from able.core.distillation.training.model_configs import GPU_FALLBACK_CHAINS

        chain = GPU_FALLBACK_CHAINS["able-nano-9b"]
        assert chain[0] == "t4_colab"  # free T4 always preferred for 9B

    def test_l4_profile_has_tight_settings_for_27b(self):
        from able.core.distillation.training.model_configs import (
            MODEL_REGISTRY,
            resolve_runtime_profile,
        )

        config = MODEL_REGISTRY["able-student-27b"]
        profile = resolve_runtime_profile(config, gpu_class="l4_session")
        # L4 (24GB) is tight for 27B: seq=2048, batch=1, high grad_accum
        assert profile["sequence_len"] == 2048
        assert profile["micro_batch_size"] == 1
        assert profile["gradient_accumulation"] >= 16
        assert profile["gradient_checkpointing"] is True

    def test_a100_profile_more_generous_than_l4(self):
        from able.core.distillation.training.model_configs import (
            MODEL_REGISTRY,
            resolve_runtime_profile,
        )

        config = MODEL_REGISTRY["able-student-27b"]
        a100 = resolve_runtime_profile(config, gpu_class="a100_session")
        l4 = resolve_runtime_profile(config, gpu_class="l4_session")
        assert a100["sequence_len"] >= l4["sequence_len"]

    def test_gpu_budget_has_new_pools(self):
        from able.core.distillation.training.gpu_budget import DEFAULT_POOLS

        assert "l4_session" in DEFAULT_POOLS
        assert "a100_session" in DEFAULT_POOLS
        assert DEFAULT_POOLS["t4_colab"]["monthly_hours"] == 72.0

    def test_distillation_readiness_check_below_threshold(self):
        """Check returns no actions when corpus hasn't grown enough."""
        from able.core.agi.proactive import DistillationReadinessCheck

        mock_store = MagicMock()
        mock_store.get_pairs.return_value = [MagicMock()] * 30  # 30 pairs

        check = DistillationReadinessCheck(
            corpus_threshold=100,
            last_training_pairs=0,
            auto_export=False,
        )
        with patch("able.core.distillation.store.DistillationStore", return_value=mock_store):
            actions = asyncio.run(check.run())
        assert actions == []

    def test_distillation_readiness_check_above_threshold(self):
        """Check returns alert when corpus passes threshold."""
        from able.core.agi.proactive import DistillationReadinessCheck

        mock_store = MagicMock()
        mock_store.get_pairs.return_value = [MagicMock()] * 150

        check = DistillationReadinessCheck(
            corpus_threshold=100,
            last_training_pairs=0,
            auto_export=False,
        )
        with patch("able.core.distillation.store.DistillationStore", return_value=mock_store):
            actions = asyncio.run(check.run())
        assert len(actions) == 1
        assert actions[0].data["current_pairs"] == 150
        assert actions[0].data["new_pairs"] == 150

    def test_orchestrator_fallback_resolves(self):
        """Fallback picks A100 when H100 budget is exhausted."""
        from able.core.distillation.training.gpu_budget import GPUBudget
        from able.core.distillation.training.training_orchestrator import (
            TrainingOrchestrator,
        )
        from able.core.distillation.training.model_configs import MODEL_REGISTRY

        budget = GPUBudget(budget_path="/dev/null", monthly_hours=0.0, buffer_hours=0.0)
        orch = TrainingOrchestrator(gpu_budget=budget)
        model = MODEL_REGISTRY["able-student-27b"]

        resolved = orch._resolve_gpu_with_fallback(model, corpus_size=100)
        # H100 has 0 budget → should fall to A100 or L4
        assert resolved in ("a100_session", "l4_session", "h100_session")
