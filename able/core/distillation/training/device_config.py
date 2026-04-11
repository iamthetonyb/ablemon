"""
D7 — Apple Silicon Training Path.

Device-aware training configuration: MPS > CUDA > CPU selection with
per-device config overrides, bfloat16 capability testing, and memory
pressure monitoring.

Forked from mattmireles/gemma-tuner-multimodal pattern.

Usage:
    selector = DeviceSelector()
    config = selector.select()  # Auto-detects best device
    print(config.device)        # "mps" | "cuda" | "cpu"
    print(config.dtype)         # torch.float32 on MPS, bfloat16 on CUDA
    print(config.memory_gb)     # Available VRAM/unified memory

    # Memory pressure monitoring
    pressure = selector.memory_pressure()  # 0.0 - 1.0
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DeviceType(str, Enum):
    MPS = "mps"
    CUDA = "cuda"
    CPU = "cpu"


class ChipGeneration(str, Enum):
    M1 = "m1"
    M2 = "m2"
    M3 = "m3"
    M4 = "m4"
    UNKNOWN = "unknown"


@dataclass
class DeviceProfile:
    """Hardware profile for a specific chip."""

    chip: ChipGeneration
    cores_gpu: int = 0
    cores_cpu: int = 0
    unified_memory_gb: float = 0.0
    max_batch_size: int = 4
    recommended_grad_accum: int = 8
    supports_bfloat16: bool = False
    notes: str = ""


# Per-chip profiles based on Apple Silicon characteristics
CHIP_PROFILES: Dict[ChipGeneration, DeviceProfile] = {
    ChipGeneration.M1: DeviceProfile(
        chip=ChipGeneration.M1,
        cores_gpu=8,
        cores_cpu=8,
        unified_memory_gb=16,
        max_batch_size=2,
        recommended_grad_accum=16,
        supports_bfloat16=False,
        notes="MPS bfloat16 unreliable — use float32",
    ),
    ChipGeneration.M2: DeviceProfile(
        chip=ChipGeneration.M2,
        cores_gpu=10,
        cores_cpu=8,
        unified_memory_gb=24,
        max_batch_size=4,
        recommended_grad_accum=8,
        supports_bfloat16=False,
        notes="MPS bfloat16 matmul works but reduction fails — use float32",
    ),
    ChipGeneration.M3: DeviceProfile(
        chip=ChipGeneration.M3,
        cores_gpu=10,
        cores_cpu=8,
        unified_memory_gb=36,
        max_batch_size=4,
        recommended_grad_accum=8,
        supports_bfloat16=False,
        notes="MPS bfloat16 backward pass issues — use float32",
    ),
    ChipGeneration.M4: DeviceProfile(
        chip=ChipGeneration.M4,
        cores_gpu=10,
        cores_cpu=10,
        unified_memory_gb=32,
        max_batch_size=8,
        recommended_grad_accum=4,
        supports_bfloat16=False,
        notes="MPS bfloat16 may work in PyTorch 2.5+ — test before enabling",
    ),
}


@dataclass
class TrainingDeviceConfig:
    """Selected training device configuration."""

    device: DeviceType
    device_name: str = ""
    dtype_name: str = "float32"
    memory_gb: float = 0.0
    max_batch_size: int = 4
    gradient_accumulation_steps: int = 8
    attention_impl: str = "eager"
    chip: ChipGeneration = ChipGeneration.UNKNOWN
    warnings: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_gpu(self) -> bool:
        return self.device in (DeviceType.MPS, DeviceType.CUDA)

    @property
    def is_apple_silicon(self) -> bool:
        return self.device == DeviceType.MPS

    def to_training_args(self) -> Dict[str, Any]:
        """Convert to HuggingFace TrainingArguments-compatible dict."""
        args: Dict[str, Any] = {
            "per_device_train_batch_size": self.max_batch_size,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
        }

        if self.device == DeviceType.MPS:
            args["use_mps_device"] = True
            args["fp16"] = False
            args["bf16"] = False  # MPS bfloat16 unsafe
            args["dataloader_pin_memory"] = False
        elif self.device == DeviceType.CUDA:
            args["fp16"] = self.dtype_name == "float16"
            args["bf16"] = self.dtype_name == "bfloat16"
        else:
            args["no_cuda"] = True
            args["fp16"] = False
            args["bf16"] = False

        return args

    def to_model_kwargs(self) -> Dict[str, Any]:
        """Convert to model loading kwargs."""
        kwargs: Dict[str, Any] = {
            "device_map": self.device.value if self.device != DeviceType.CPU else "cpu",
            "attn_implementation": self.attention_impl,
        }
        return kwargs


class DeviceSelector:
    """Selects the best training device with per-chip configuration.

    Priority: MPS > CUDA > CPU

    On MPS (Apple Silicon):
    - Forces float32 (bfloat16 unreliable across all current chips)
    - Uses eager attention (flash attention not available)
    - Monitors memory pressure via sysctl

    On CUDA:
    - Enables bfloat16 if Ampere+ (compute capability >= 8.0)
    - Enables flash attention 2 if available
    - Reports per-GPU VRAM
    """

    def __init__(self):
        self._torch_available = False
        self._torch = None
        self._cached_config: Optional[TrainingDeviceConfig] = None
        try:
            import torch
            self._torch = torch
            self._torch_available = True
        except ImportError:
            pass

    def select(self, force_device: Optional[DeviceType] = None) -> TrainingDeviceConfig:
        """Auto-detect and return optimal training config."""
        if force_device:
            return self._build_config(force_device)

        if self._cached_config:
            return self._cached_config

        # Priority: MPS > CUDA > CPU
        if self._torch_available:
            if (
                hasattr(self._torch.backends, "mps")
                and self._torch.backends.mps.is_available()
            ):
                config = self._build_config(DeviceType.MPS)
            elif self._torch.cuda.is_available():
                config = self._build_config(DeviceType.CUDA)
            else:
                config = self._build_config(DeviceType.CPU)
        else:
            config = self._build_config(DeviceType.CPU)
            config.warnings.append("PyTorch not installed — CPU only")

        self._cached_config = config
        return config

    def _build_config(self, device: DeviceType) -> TrainingDeviceConfig:
        """Build config for a specific device type."""
        config = TrainingDeviceConfig(device=device)

        if device == DeviceType.MPS:
            self._configure_mps(config)
        elif device == DeviceType.CUDA:
            self._configure_cuda(config)
        else:
            self._configure_cpu(config)

        return config

    def _configure_mps(self, config: TrainingDeviceConfig) -> None:
        """Configure for Apple Silicon MPS."""
        config.device_name = _detect_chip_name()
        config.chip = _detect_chip_generation()
        config.dtype_name = "float32"  # Always float32 on MPS
        config.attention_impl = "eager"  # No flash attention on MPS
        config.memory_gb = _get_unified_memory_gb()

        profile = CHIP_PROFILES.get(config.chip)
        if profile:
            config.max_batch_size = profile.max_batch_size
            config.gradient_accumulation_steps = profile.recommended_grad_accum
            if profile.notes:
                config.warnings.append(profile.notes)
        else:
            config.max_batch_size = 2
            config.gradient_accumulation_steps = 16
            config.warnings.append("Unknown Apple Silicon chip — using conservative defaults")

        # Scale batch size by available memory
        if config.memory_gb >= 64:
            config.max_batch_size = min(config.max_batch_size * 4, 32)
            config.gradient_accumulation_steps = max(
                config.gradient_accumulation_steps // 4, 1
            )
        elif config.memory_gb >= 32:
            config.max_batch_size = min(config.max_batch_size * 2, 16)
            config.gradient_accumulation_steps = max(
                config.gradient_accumulation_steps // 2, 2
            )

    def _configure_cuda(self, config: TrainingDeviceConfig) -> None:
        """Configure for NVIDIA CUDA."""
        if not self._torch_available:
            return

        torch = self._torch
        config.device_name = torch.cuda.get_device_name(0)

        # Check compute capability for bfloat16
        cap = torch.cuda.get_device_capability(0)
        if cap[0] >= 8:  # Ampere+
            config.dtype_name = "bfloat16"
            config.attention_impl = "flash_attention_2"
        else:
            config.dtype_name = "float16"
            config.attention_impl = "eager"

        # VRAM
        total = torch.cuda.get_device_properties(0).total_mem
        config.memory_gb = round(total / (1024**3), 1)

        # Scale batch size by VRAM
        if config.memory_gb >= 48:
            config.max_batch_size = 16
            config.gradient_accumulation_steps = 2
        elif config.memory_gb >= 24:
            config.max_batch_size = 8
            config.gradient_accumulation_steps = 4
        elif config.memory_gb >= 12:
            config.max_batch_size = 4
            config.gradient_accumulation_steps = 8
        else:
            config.max_batch_size = 2
            config.gradient_accumulation_steps = 16
            config.warnings.append(f"Low VRAM ({config.memory_gb}GB) — may OOM on larger models")

    def _configure_cpu(self, config: TrainingDeviceConfig) -> None:
        """Configure for CPU fallback."""
        config.device_name = platform.processor() or "unknown"
        config.dtype_name = "float32"
        config.attention_impl = "eager"
        config.max_batch_size = 1
        config.gradient_accumulation_steps = 32
        config.memory_gb = _get_system_memory_gb()
        config.warnings.append("CPU training — very slow, use for testing only")

    def test_bfloat16(self) -> Dict[str, bool]:
        """Comprehensive bfloat16 capability test.

        Tests matmul, reduction, backward pass — not just tensor creation.
        Returns dict of test_name → passed.
        """
        if not self._torch_available:
            return {"torch_available": False}

        torch = self._torch
        results: Dict[str, bool] = {}

        device = "mps" if (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ) else "cuda" if torch.cuda.is_available() else "cpu"

        try:
            # Test 1: Tensor creation
            t = torch.randn(4, 4, dtype=torch.bfloat16, device=device)
            results["tensor_creation"] = True
        except Exception:
            results["tensor_creation"] = False
            return results  # If creation fails, skip rest

        try:
            # Test 2: Matrix multiplication
            a = torch.randn(8, 8, dtype=torch.bfloat16, device=device)
            b = torch.randn(8, 8, dtype=torch.bfloat16, device=device)
            c = torch.matmul(a, b)
            results["matmul"] = not torch.isnan(c).any().item()
        except Exception:
            results["matmul"] = False

        try:
            # Test 3: Reduction (sum, mean)
            t = torch.randn(16, 16, dtype=torch.bfloat16, device=device)
            s = t.sum()
            m = t.mean()
            results["reduction"] = (
                not torch.isnan(s).item() and not torch.isnan(m).item()
            )
        except Exception:
            results["reduction"] = False

        try:
            # Test 4: Backward pass (autograd)
            x = torch.randn(4, 4, dtype=torch.bfloat16, device=device, requires_grad=True)
            y = (x * x).sum()
            y.backward()
            results["backward"] = (
                x.grad is not None and not torch.isnan(x.grad).any().item()
            )
        except Exception:
            results["backward"] = False

        return results

    def memory_pressure(self) -> float:
        """Get current memory pressure (0.0 = free, 1.0 = critical).

        On macOS: uses vm_stat to estimate pressure.
        On Linux/CUDA: uses torch.cuda.memory_allocated / total.
        """
        if self._torch_available and self._torch.cuda.is_available():
            allocated = self._torch.cuda.memory_allocated(0)
            total = self._torch.cuda.get_device_properties(0).total_mem
            return allocated / total if total > 0 else 0.0

        # macOS: estimate from vm_stat
        if platform.system() == "Darwin":
            return _macos_memory_pressure()

        return 0.0


# ── Platform helpers ───────────────────────────────────────────


def _detect_chip_name() -> str:
    """Detect Apple Silicon chip name via sysctl."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "Unknown"


def _detect_chip_generation() -> ChipGeneration:
    """Detect Apple Silicon generation from chip name."""
    name = _detect_chip_name().lower()
    if "m4" in name:
        return ChipGeneration.M4
    if "m3" in name:
        return ChipGeneration.M3
    if "m2" in name:
        return ChipGeneration.M2
    if "m1" in name:
        return ChipGeneration.M1
    return ChipGeneration.UNKNOWN


def _get_unified_memory_gb() -> float:
    """Get total unified memory on macOS."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5,
        )
        return round(int(result.stdout.strip()) / (1024**3), 1)
    except Exception:
        return 0.0


def _get_system_memory_gb() -> float:
    """Get total system RAM."""
    try:
        if platform.system() == "Darwin":
            return _get_unified_memory_gb()
        # Linux
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024**2), 1)
    except Exception:
        pass
    return 0.0


def _macos_memory_pressure() -> float:
    """Estimate memory pressure on macOS from vm_stat."""
    try:
        result = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5,
        )
        # Cap parsed output to 50 lines (vm_stat typically produces ~15)
        lines = result.stdout.strip().split("\n")[:50]
        stats: Dict[str, int] = {}
        for line in lines[1:]:
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip()
                val = parts[1].strip().rstrip(".")
                try:
                    stats[key] = int(val)
                except ValueError:
                    continue

        free = stats.get("Pages free", 0)
        active = stats.get("Pages active", 0)
        inactive = stats.get("Pages inactive", 0)
        wired = stats.get("Pages wired down", 0)
        compressed = stats.get("Pages occupied by compressor", 0)

        total = free + active + inactive + wired + compressed
        if total == 0:
            return 0.0

        used = active + wired + compressed
        return used / total
    except Exception:
        return 0.0
