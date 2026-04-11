"""Tests for D7 — Apple Silicon Training Path.

Covers: device selection, chip detection, config generation, bfloat16 testing,
training args output, model kwargs output, memory pressure, profiles.
"""

import platform
import pytest

from able.core.distillation.training.device_config import (
    CHIP_PROFILES,
    ChipGeneration,
    DeviceProfile,
    DeviceSelector,
    DeviceType,
    TrainingDeviceConfig,
    _detect_chip_generation,
    _get_system_memory_gb,
)


@pytest.fixture
def selector():
    return DeviceSelector()


# ── Device selection ───────────────────────────────────────────

class TestDeviceSelection:

    def test_select_returns_config(self, selector):
        config = selector.select()
        assert isinstance(config, TrainingDeviceConfig)
        assert config.device in (DeviceType.MPS, DeviceType.CUDA, DeviceType.CPU)

    def test_force_cpu(self, selector):
        config = selector.select(force_device=DeviceType.CPU)
        assert config.device == DeviceType.CPU
        assert config.dtype_name == "float32"

    def test_cpu_has_warnings(self, selector):
        config = selector.select(force_device=DeviceType.CPU)
        assert any("CPU" in w for w in config.warnings)

    def test_cached_result(self, selector):
        c1 = selector.select()
        c2 = selector.select()
        assert c1 is c2  # Same object from cache

    def test_force_bypasses_cache(self, selector):
        c1 = selector.select()
        c2 = selector.select(force_device=DeviceType.CPU)
        # Forced config may differ from cached auto-select
        assert c2.device == DeviceType.CPU


# ── Config properties ──────────────────────────────────────────

class TestConfigProperties:

    def test_is_gpu_mps(self):
        c = TrainingDeviceConfig(device=DeviceType.MPS)
        assert c.is_gpu is True
        assert c.is_apple_silicon is True

    def test_is_gpu_cuda(self):
        c = TrainingDeviceConfig(device=DeviceType.CUDA)
        assert c.is_gpu is True
        assert c.is_apple_silicon is False

    def test_is_gpu_cpu(self):
        c = TrainingDeviceConfig(device=DeviceType.CPU)
        assert c.is_gpu is False


# ── Training args output ──────────────────────────────────────

class TestTrainingArgs:

    def test_cpu_args(self):
        c = TrainingDeviceConfig(device=DeviceType.CPU, max_batch_size=1, gradient_accumulation_steps=32)
        args = c.to_training_args()
        assert args["no_cuda"] is True
        assert args["fp16"] is False
        assert args["per_device_train_batch_size"] == 1

    def test_mps_args(self):
        c = TrainingDeviceConfig(device=DeviceType.MPS, max_batch_size=4, gradient_accumulation_steps=8)
        args = c.to_training_args()
        assert args["use_mps_device"] is True
        assert args["bf16"] is False  # MPS bfloat16 unsafe
        assert args["fp16"] is False
        assert args["dataloader_pin_memory"] is False

    def test_cuda_bf16_args(self):
        c = TrainingDeviceConfig(device=DeviceType.CUDA, dtype_name="bfloat16", max_batch_size=8, gradient_accumulation_steps=4)
        args = c.to_training_args()
        assert args["bf16"] is True
        assert args["fp16"] is False

    def test_cuda_fp16_args(self):
        c = TrainingDeviceConfig(device=DeviceType.CUDA, dtype_name="float16", max_batch_size=4, gradient_accumulation_steps=8)
        args = c.to_training_args()
        assert args["fp16"] is True
        assert args["bf16"] is False


# ── Model kwargs output ────────────────────────────────────────

class TestModelKwargs:

    def test_mps_kwargs(self):
        c = TrainingDeviceConfig(device=DeviceType.MPS, attention_impl="eager")
        kwargs = c.to_model_kwargs()
        assert kwargs["device_map"] == "mps"
        assert kwargs["attn_implementation"] == "eager"

    def test_cpu_kwargs(self):
        c = TrainingDeviceConfig(device=DeviceType.CPU)
        kwargs = c.to_model_kwargs()
        assert kwargs["device_map"] == "cpu"


# ── Chip profiles ──────────────────────────────────────────────

class TestChipProfiles:

    def test_all_chips_have_profiles(self):
        for chip in [ChipGeneration.M1, ChipGeneration.M2, ChipGeneration.M3, ChipGeneration.M4]:
            assert chip in CHIP_PROFILES

    def test_m1_conservative(self):
        p = CHIP_PROFILES[ChipGeneration.M1]
        assert p.max_batch_size <= 2
        assert not p.supports_bfloat16

    def test_m4_more_capable(self):
        p = CHIP_PROFILES[ChipGeneration.M4]
        assert p.max_batch_size >= 4
        assert p.cores_cpu >= 10

    def test_profile_dataclass(self):
        p = DeviceProfile(chip=ChipGeneration.M2, cores_gpu=10)
        assert p.chip == ChipGeneration.M2
        assert p.cores_gpu == 10


# ── Chip detection ─────────────────────────────────────────────

class TestChipDetection:

    def test_detection_returns_enum(self):
        gen = _detect_chip_generation()
        assert isinstance(gen, ChipGeneration)

    @pytest.mark.skipif(
        platform.system() != "Darwin",
        reason="macOS only",
    )
    def test_system_memory_positive(self):
        mem = _get_system_memory_gb()
        assert mem > 0


# ── Memory pressure ────────────────────────────────────────────

class TestMemoryPressure:

    def test_pressure_returns_float(self, selector):
        p = selector.memory_pressure()
        assert isinstance(p, float)
        assert 0.0 <= p <= 1.0


# ── Bfloat16 test ──────────────────────────────────────────────

class TestBfloat16:

    def test_returns_dict(self, selector):
        results = selector.test_bfloat16()
        assert isinstance(results, dict)

    def test_no_torch_flag(self):
        # Create selector with no torch
        sel = DeviceSelector()
        sel._torch_available = False
        results = sel.test_bfloat16()
        assert results.get("torch_available") is False
