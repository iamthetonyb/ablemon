"""Tests for the ABLE training pipeline.

Covers: model configs, Axolotl generation, GPU budget, preflight,
quantizer commands, orchestrator state machine, and LMCache config.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
import yaml

from able.core.distillation.training.axolotl_generator import AxolotlConfigGenerator
from able.core.distillation.training.gpu_budget import GPUBudget
from able.core.distillation.training.gpu_preflight import GPUPreflight
from able.core.distillation.training.lmcache_config import LMCacheConfig
from able.core.distillation.training.model_configs import (
    ABLE_NANO_9B,
    ABLE_STUDENT_27B,
    MODEL_REGISTRY,
)
from able.core.distillation.training.quantizer import GGUFQuantizer
from able.core.distillation.training.training_orchestrator import TrainingOrchestrator


# ── Model configs ────────────────────────────────────────────────────


class TestModelConfigs:
    def test_27b_values(self):
        c = ABLE_STUDENT_27B
        assert c.name == "able-student-27b"
        assert c.base_model == "Qwen/Qwen3.5-27B"
        assert c.role == "server"
        assert c.lora_r == 32
        assert c.lora_alpha == 64
        assert c.sequence_len == 8192
        assert c.micro_batch_size == 1
        assert c.gradient_accumulation == 8
        assert c.learning_rate == 1.5e-4
        assert c.min_gpu_memory_gb == 24
        assert "UD-Q4_K_XL" in c.quantization_targets

    def test_9b_values(self):
        c = ABLE_NANO_9B
        assert c.name == "able-nano-9b"
        assert c.base_model == "Qwen/Qwen3.5-9B"
        assert c.role == "edge"
        assert c.lora_r == 16
        assert c.lora_alpha == 32
        assert c.sequence_len == 2048
        assert c.micro_batch_size == 1
        assert c.gradient_accumulation == 8
        assert c.learning_rate == 2e-4
        assert c.min_gpu_memory_gb == 12
        assert c.default_gpu_class == "t4_colab"
        assert "UD-IQ2_M" in c.quantization_targets

    def test_registry_has_both(self):
        assert "able-student-27b" in MODEL_REGISTRY
        assert "able-nano-9b" in MODEL_REGISTRY
        assert len(MODEL_REGISTRY) == 2


# ── Axolotl config generator ────────────────────────────────────────


class TestAxolotlGenerator:
    def test_generates_valid_yaml(self):
        gen = AxolotlConfigGenerator()
        with tempfile.TemporaryDirectory() as td:
            path = gen.generate(
                ABLE_STUDENT_27B,
                corpus_path="/data/train.jsonl",
                output_path=td,
            )
            assert os.path.isfile(path)
            with open(path) as f:
                cfg = yaml.safe_load(f)
            assert isinstance(cfg, dict)
            assert cfg["base_model"] == "Qwen/Qwen3.5-27B"

    def test_train_on_inputs_false(self):
        gen = AxolotlConfigGenerator()
        cfg = gen.generate_dict(
            ABLE_STUDENT_27B,
            corpus_path="/data/train.jsonl",
            output_path="/tmp/out",
        )
        assert cfg["train_on_inputs"] is False

    def test_qlora_settings(self):
        gen = AxolotlConfigGenerator()
        cfg = gen.generate_dict(
            ABLE_NANO_9B,
            corpus_path="/data/train.jsonl",
            output_path="/tmp/out",
        )
        assert cfg["adapter"] == "qlora"
        assert cfg["load_in_4bit"] is True
        assert cfg["lora_r"] == 16
        assert cfg["lora_alpha"] == 32
        assert cfg["chat_template"] == "chatml"
        assert cfg["sequence_len"] == 2048
        assert cfg["micro_batch_size"] == 1
        assert cfg["fp16"] is True

    def test_h100_profile_overrides_t4_defaults(self):
        gen = AxolotlConfigGenerator()
        cfg = gen.generate_dict(
            ABLE_NANO_9B,
            corpus_path="/data/train.jsonl",
            output_path="/tmp/out",
            gpu_class="h100_session",
        )
        assert cfg["sequence_len"] == 4096
        assert cfg["micro_batch_size"] == 2
        assert cfg["bf16"] is True

    def test_tenant_isolation(self):
        gen = AxolotlConfigGenerator()
        cfg = gen.generate_dict(
            ABLE_STUDENT_27B,
            corpus_path="/data/train.jsonl",
            output_path="/tmp/out",
            tenant_id="acme",
        )
        assert "acme" in cfg["output_dir"]


# ── GPU budget ───────────────────────────────────────────────────────


class TestGPUBudget:
    def _make_budget(self, td: str) -> GPUBudget:
        return GPUBudget(
            budget_path=os.path.join(td, "budget.yaml"),
            monthly_hours=20.0,
            buffer_hours=2.5,
        )

    def test_initial_remaining(self):
        with tempfile.TemporaryDirectory() as td:
            b = self._make_budget(td)
            assert b.remaining() == 17.5  # 20 - 2.5

    def test_record_and_remaining(self):
        with tempfile.TemporaryDirectory() as td:
            b = self._make_budget(td)
            b.record_usage(5.0, purpose="training", tenant_id="default")
            assert b.remaining() == 12.5  # 17.5 - 5

    def test_can_train_yes(self):
        with tempfile.TemporaryDirectory() as td:
            b = self._make_budget(td)
            assert b.can_train(10.0) is True

    def test_can_train_no(self):
        with tempfile.TemporaryDirectory() as td:
            b = self._make_budget(td)
            assert b.can_train(18.0) is False

    def test_blocks_when_exhausted(self):
        with tempfile.TemporaryDirectory() as td:
            b = self._make_budget(td)
            b.record_usage(17.0, purpose="training")
            assert b.remaining() == 0.5
            assert b.can_train(1.0) is False

    def test_summary(self):
        with tempfile.TemporaryDirectory() as td:
            b = self._make_budget(td)
            b.record_usage(3.0, purpose="training", tenant_id="acme")
            b.record_usage(1.5, purpose="validation", tenant_id="default")
            s = b.get_summary()
            assert s["used_hours"] == 4.5
            assert s["by_tenant"]["acme"] == 3.0
            assert s["by_purpose"]["validation"] == 1.5
            assert s["entry_count"] == 2
            assert "t4_colab" in s["pools"]
            assert "local" in s["pools"]

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "budget.yaml")
            b1 = GPUBudget(budget_path=path)
            b1.record_usage(2.0, purpose="test")
            b2 = GPUBudget(budget_path=path)
            assert b2.remaining() == 15.5  # 17.5 - 2


# ── GPU preflight ────────────────────────────────────────────────────


class TestGPUPreflight:
    def test_detects_missing_corpus(self):
        with tempfile.TemporaryDirectory() as td:
            pf = GPUPreflight(
                corpus_dir=os.path.join(td, "nonexistent"),
                output_dir=os.path.join(td, "output"),
                gpu_budget=GPUBudget(
                    budget_path=os.path.join(td, "budget.yaml"),
                ),
            )
            result = pf.run(model_name="able-student-27b")
            assert result["ready"] is False
            checks = result["models"]["able-student-27b"]["checks"]
            assert checks["corpus_exists"]["pass"] is False

    def test_passes_with_corpus(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = os.path.join(td, "corpus")
            os.makedirs(corpus)
            for split in ("train.jsonl", "val.jsonl"):
                with open(os.path.join(corpus, split), "w") as f:
                    for i in range(10):
                        f.write(f'{{"messages": [{{"role": "user", "content": "q{i}"}}, {{"role": "assistant", "content": "a{i}"}}]}}\n')
            pf = GPUPreflight(
                corpus_dir=corpus,
                output_dir=os.path.join(td, "output"),
                gpu_budget=GPUBudget(
                    budget_path=os.path.join(td, "budget.yaml"),
                ),
            )
            result = pf.run(model_name="able-nano-9b")
            assert result["ready"] is True

    def test_rejects_27b_on_t4(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = os.path.join(td, "corpus")
            os.makedirs(corpus)
            for split in ("train.jsonl", "val.jsonl"):
                with open(os.path.join(corpus, split), "w") as f:
                    for i in range(3):
                        f.write(f'{{"messages": [{{"role": "user", "content": "q{i}"}}, {{"role": "assistant", "content": "a{i}"}}]}}\n')
            pf = GPUPreflight(
                corpus_dir=corpus,
                output_dir=os.path.join(td, "output"),
                gpu_budget=GPUBudget(budget_path=os.path.join(td, "budget.yaml")),
                gpu_class="t4_colab",
            )
            result = pf.run(model_name="able-student-27b")
            assert result["ready"] is False
            checks = result["models"]["able-student-27b"]["checks"]
            assert checks["gpu_class_supported"]["pass"] is False

    def test_invalid_model_name(self):
        with tempfile.TemporaryDirectory() as td:
            pf = GPUPreflight(
                corpus_dir=td,
                output_dir=td,
                gpu_budget=GPUBudget(budget_path=os.path.join(td, "b.yaml")),
            )
            with pytest.raises(ValueError, match="Unknown model"):
                pf.run(model_name="nonexistent")


# ── Quantizer ────────────────────────────────────────────────────────


class TestQuantizer:
    def test_merge_command(self):
        q = GGUFQuantizer()
        cmd = q.generate_merge_command(
            adapter_path="/adapters/27b",
            base_model="Qwen/Qwen3.5-27B",
            output_path="/merged/27b",
        )
        assert "unsloth" in cmd
        assert "Qwen/Qwen3.5-27B" in cmd
        assert "/adapters/27b" in cmd

    def test_quantize_ud_uses_unsloth(self):
        q = GGUFQuantizer()
        cmd = q.generate_quantize_command(
            model_path="/merged/27b",
            quant_type="UD-Q4_K_XL",
            output_path="/quants",
        )
        assert "unsloth" in cmd
        assert "UD-Q4_K_XL" in cmd

    def test_quantize_standard_uses_llama(self):
        q = GGUFQuantizer()
        cmd = q.generate_quantize_command(
            model_path="/merged/27b",
            quant_type="Q5_K_M",
            output_path="/quants",
        )
        assert "llama-quantize" in cmd
        assert "Q5_K_M" in cmd

    def test_ollama_modelfile(self):
        q = GGUFQuantizer()
        mf = q.generate_ollama_modelfile(
            gguf_path="/models/able-27b.gguf",
            model_name="able-27b",
        )
        assert "FROM /models/able-27b.gguf" in mf
        assert "im_start" in mf
        assert "im_end" in mf

    def test_estimated_size(self):
        q = GGUFQuantizer()
        assert q.estimated_size_gb("able-student-27b", "UD-Q4_K_XL") == 17.6
        assert q.estimated_size_gb("able-nano-9b", "UD-IQ2_M") == 3.65
        assert q.estimated_size_gb("able-nano-9b", "UNKNOWN") is None


# ── Training orchestrator ────────────────────────────────────────────


class TestTrainingOrchestrator:
    def test_initial_status(self):
        with tempfile.TemporaryDirectory() as td:
            o = TrainingOrchestrator(
                gpu_budget=GPUBudget(budget_path=os.path.join(td, "b.yaml")),
            )
            assert o.status == "idle"
            s = o.get_status()
            assert s["status"] == "idle"

    def test_estimate_time(self):
        with tempfile.TemporaryDirectory() as td:
            o = TrainingOrchestrator(
                gpu_budget=GPUBudget(budget_path=os.path.join(td, "b.yaml")),
            )
            est = o.estimate_time(1000)
            assert est["able-student-27b"]["hours"] == 0.9
            assert est["able-nano-9b"]["hours"] == 0.3

    def test_estimate_time_zero(self):
        with tempfile.TemporaryDirectory() as td:
            o = TrainingOrchestrator(
                gpu_budget=GPUBudget(budget_path=os.path.join(td, "b.yaml")),
            )
            est = o.estimate_time(0)
            assert est["able-student-27b"]["hours"] == 0.0

    def test_run_budget_exceeded(self):
        with tempfile.TemporaryDirectory() as td:
            budget = GPUBudget(
                budget_path=os.path.join(td, "b.yaml"),
                monthly_hours=0.4,
                buffer_hours=0.0,
            )
            o = TrainingOrchestrator(
                gpu_budget=budget,
                corpus_dir=os.path.join(td, "corpus"),
                output_dir=os.path.join(td, "output"),
            )
            # Pin to h100 so fallback chain doesn't rescue with another pool
            # 0h training + 0.5h overhead exceeds 0.4h budget
            result = asyncio.run(o.run(mode="27b", gpu_class="h100_session"))
            assert result["status"] == "budget_exceeded"
            assert o.status == "failed"

    def test_run_success(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = os.path.join(td, "corpus")
            os.makedirs(corpus)
            with open(os.path.join(corpus, "train.jsonl"), "w") as f:
                for i in range(5):
                    f.write(f'{{"messages": []}}\n')

            budget = GPUBudget(budget_path=os.path.join(td, "b.yaml"))
            o = TrainingOrchestrator(
                gpu_budget=budget,
                corpus_dir=corpus,
                output_dir=os.path.join(td, "output"),
            )
            result = asyncio.run(o.run(mode="all"))
            assert result["status"] == "done"
            assert o.status == "done"
            assert "able-student-27b" in result["configs"]
            assert "able-nano-9b" in result["configs"]
            assert result["profiles"]["able-student-27b"]["gpu_class"] == "h100_session"
            assert result["profiles"]["able-nano-9b"]["gpu_class"] == "t4_colab"

    def test_invalid_mode(self):
        with tempfile.TemporaryDirectory() as td:
            o = TrainingOrchestrator(
                gpu_budget=GPUBudget(budget_path=os.path.join(td, "b.yaml")),
            )
            with pytest.raises(ValueError, match="Unknown model"):
                asyncio.run(o.run(mode="invalid"))


# ── LMCache config ───────────────────────────────────────────────────


class TestLMCacheConfig:
    def test_generates_ollama_config(self):
        lmc = LMCacheConfig()
        cfg = lmc.generate_config("/models/able-27b.gguf", backend="ollama")
        assert cfg["backend"] == "ollama"
        assert cfg["prefix_caching"]["enabled"] is True
        assert cfg["cache"]["type"] == "disk"
        assert "endpoint" in cfg["backend_config"]

    def test_generates_vllm_config(self):
        lmc = LMCacheConfig()
        cfg = lmc.generate_config("/models/able-27b.gguf", backend="vllm", port=8000)
        assert cfg["backend"] == "vllm"
        assert "8000" in cfg["backend_config"]["endpoint"]
        assert cfg["backend_config"]["tensor_parallel"] == 1

    def test_custom_cache_size(self):
        lmc = LMCacheConfig()
        cfg = lmc.generate_config("/m.gguf", cache_size_gb=8.0)
        assert cfg["cache"]["max_size_gb"] == 8.0
