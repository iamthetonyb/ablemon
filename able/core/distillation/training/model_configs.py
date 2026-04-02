"""Model configurations for ABLE student models.

Defines QLoRA hyperparameters, quantization targets, and runtime profiles
for the canonical 27B server model and the 9B T4-first edge model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StudentModelConfig:
    """Configuration for a single student model."""

    name: str
    base_model: str  # HuggingFace model ID
    role: str  # "server" | "edge"
    lora_r: int
    lora_alpha: int
    sequence_len: int
    micro_batch_size: int
    gradient_accumulation: int
    learning_rate: float
    min_gpu_memory_gb: int
    quantization_targets: list[str] = field(default_factory=list)
    description: str = ""
    default_gpu_class: str = "h100_session"
    default_runtime: str = "cloud"
    supported_gpu_classes: list[str] = field(default_factory=lambda: ["h100_session"])
    checkpointing: bool = True
    resume_first: bool = True
    runtime_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)


ABLE_STUDENT_27B = StudentModelConfig(
    name="able-student-27b",
    base_model="Qwen/Qwen3.5-27B",
    role="server",
    lora_r=32,
    lora_alpha=64,
    sequence_len=8192,
    micro_batch_size=1,
    gradient_accumulation=8,
    learning_rate=1.5e-4,
    min_gpu_memory_gb=24,
    quantization_targets=["UD-Q4_K_XL", "Q5_K_M", "Q8_0"],
    description=(
        "Primary server model. UD-Q4_K_XL (17.6GB) is the default deployment target. "
        "Training: H100 preferred, falls back to A100 (40GB+) or L4 (24GB, tight)."
    ),
    default_gpu_class="h100_session",
    default_runtime="cloud",
    supported_gpu_classes=["h100_session", "a100_session", "l4_session"],
    checkpointing=True,
    resume_first=True,
    runtime_profiles={
        "h100_session": {
            "runtime": "cloud",
            "sequence_len": 8192,
            "micro_batch_size": 1,
            "gradient_accumulation": 8,
            "gradient_checkpointing": True,
            "bf16": True,
            "fp16": False,
            "save_strategy": "epoch",
            "save_steps": 250,
        },
        "a100_session": {
            "runtime": "colab",
            "sequence_len": 4096,
            "micro_batch_size": 1,
            "gradient_accumulation": 8,
            "gradient_checkpointing": True,
            "bf16": True,
            "fp16": False,
            "save_strategy": "steps",
            "save_steps": 100,
        },
        "l4_session": {
            "runtime": "colab",
            "sequence_len": 2048,
            "micro_batch_size": 1,
            "gradient_accumulation": 16,
            "gradient_checkpointing": True,
            "bf16": True,
            "fp16": False,
            "save_strategy": "steps",
            "save_steps": 50,
        },
    },
)

ABLE_NANO_9B = StudentModelConfig(
    name="able-nano-9b",
    base_model="Qwen/Qwen3.5-9B",
    role="edge",
    lora_r=16,
    lora_alpha=32,
    sequence_len=2048,
    micro_batch_size=1,
    gradient_accumulation=8,
    learning_rate=2e-4,
    min_gpu_memory_gb=12,
    quantization_targets=["UD-IQ2_M", "UD-Q4_K_XL", "Q5_K_M"],
    description=(
        "T4-first edge model. UD-IQ2_M (3.65GB) is the compact edge target, "
        "UD-Q4_K_XL (5.97GB) is the balanced target, and Q5_K_M (6.58GB) is the higher-fidelity export."
    ),
    default_gpu_class="t4_colab",
    default_runtime="colab",
    supported_gpu_classes=["t4_colab", "a100_session", "l4_session", "h100_session", "local"],
    checkpointing=True,
    resume_first=True,
    runtime_profiles={
        "t4_colab": {
            "runtime": "colab",
            "sequence_len": 2048,
            "micro_batch_size": 1,
            "gradient_accumulation": 8,
            "gradient_checkpointing": True,
            "bf16": False,
            "fp16": True,
            "save_strategy": "steps",
            "save_steps": 100,
        },
        "a100_session": {
            "runtime": "colab",
            "sequence_len": 4096,
            "micro_batch_size": 2,
            "gradient_accumulation": 4,
            "gradient_checkpointing": True,
            "bf16": True,
            "fp16": False,
            "save_strategy": "steps",
            "save_steps": 100,
        },
        "l4_session": {
            "runtime": "colab",
            "sequence_len": 2048,
            "micro_batch_size": 1,
            "gradient_accumulation": 8,
            "gradient_checkpointing": True,
            "bf16": True,
            "fp16": False,
            "save_strategy": "steps",
            "save_steps": 100,
        },
        "h100_session": {
            "runtime": "cloud",
            "sequence_len": 4096,
            "micro_batch_size": 2,
            "gradient_accumulation": 4,
            "gradient_checkpointing": True,
            "bf16": True,
            "fp16": False,
            "save_strategy": "epoch",
            "save_steps": 250,
        },
        "local": {
            "runtime": "local",
            "sequence_len": 2048,
            "micro_batch_size": 1,
            "gradient_accumulation": 8,
            "gradient_checkpointing": True,
            "bf16": False,
            "fp16": True,
            "save_strategy": "steps",
            "save_steps": 100,
        },
    },
)

MODEL_REGISTRY: dict[str, StudentModelConfig] = {
    "able-student-27b": ABLE_STUDENT_27B,
    "able-nano-9b": ABLE_NANO_9B,
}

# GPU fallback chains — when the preferred GPU class is unavailable or
# budget-exhausted, try the next one in order.  Each chain only includes
# GPU classes the model actually supports.
GPU_FALLBACK_CHAINS: dict[str, list[str]] = {
    "able-student-27b": ["h100_session", "a100_session", "l4_session"],
    "able-nano-9b": ["t4_colab", "l4_session", "a100_session", "h100_session", "local"],
}


def resolve_models(name: str) -> list[StudentModelConfig]:
    """Resolve a model name/alias to a list of configs."""
    if name == "all":
        return list(MODEL_REGISTRY.values())
    if name in MODEL_REGISTRY:
        return [MODEL_REGISTRY[name]]

    aliases = {"27b": "able-student-27b", "9b": "able-nano-9b"}
    if name in aliases:
        return [MODEL_REGISTRY[aliases[name]]]

    raise ValueError(f"Unknown model '{name}'. Available: {', '.join(MODEL_REGISTRY)}")


def resolve_runtime_profile(
    config: StudentModelConfig,
    gpu_class: str | None = None,
    runtime: str | None = None,
) -> dict[str, Any]:
    """Resolve the effective runtime profile for a model and GPU class."""
    selected_gpu = gpu_class or config.default_gpu_class
    if selected_gpu not in config.supported_gpu_classes:
        raise ValueError(
            f"Model '{config.name}' does not support gpu_class '{selected_gpu}'. "
            f"Supported: {', '.join(config.supported_gpu_classes)}"
        )

    profile = dict(config.runtime_profiles.get(selected_gpu, {}))
    profile.setdefault("runtime", config.default_runtime)
    profile["runtime"] = runtime or profile["runtime"]
    profile.setdefault("sequence_len", config.sequence_len)
    profile.setdefault("micro_batch_size", config.micro_batch_size)
    profile.setdefault("gradient_accumulation", config.gradient_accumulation)
    profile.setdefault("gradient_checkpointing", config.checkpointing)
    profile.setdefault("bf16", selected_gpu != "t4_colab")
    profile.setdefault("fp16", selected_gpu == "t4_colab")
    profile["gpu_class"] = selected_gpu
    return profile
