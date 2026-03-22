"""Model configurations for ATLAS student models.

Defines QLoRA hyperparameters, quantization targets, and hardware
requirements for both the 27B server model and the 9B edge model.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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


ATLAS_STUDENT_27B = StudentModelConfig(
    name="atlas-student-27b",
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
        "Primary server model. UD-Q4_K_XL (17.6GB) is default deployment target."
    ),
)

ATLAS_NANO_9B = StudentModelConfig(
    name="atlas-nano-9b",
    base_model="Qwen/Qwen3.5-9B",
    role="edge",
    lora_r=16,
    lora_alpha=32,
    sequence_len=4096,
    micro_batch_size=2,
    gradient_accumulation=4,
    learning_rate=2e-4,
    min_gpu_memory_gb=12,
    quantization_targets=["UD-IQ2_M", "UD-Q4_K_XL", "Q5_K_M"],
    description=(
        "Edge model. UD-IQ2_M (3.65GB) for mobile, UD-Q4_K_XL (5.97GB) balanced."
    ),
)

MODEL_REGISTRY: dict[str, StudentModelConfig] = {
    "atlas-student-27b": ATLAS_STUDENT_27B,
    "atlas-nano-9b": ATLAS_NANO_9B,
}


def resolve_models(name: str) -> list[StudentModelConfig]:
    """Resolve a model name/alias to a list of configs.

    Args:
        name: "all", a registry key, or a short alias ("27b", "9b").

    Raises:
        ValueError: If the name is not recognized.
    """
    if name == "all":
        return list(MODEL_REGISTRY.values())
    if name in MODEL_REGISTRY:
        return [MODEL_REGISTRY[name]]
    # Short aliases
    _ALIASES = {"27b": "atlas-student-27b", "9b": "atlas-nano-9b"}
    if name in _ALIASES:
        return [MODEL_REGISTRY[_ALIASES[name]]]
    raise ValueError(
        f"Unknown model '{name}'. Available: {', '.join(MODEL_REGISTRY)}"
    )
