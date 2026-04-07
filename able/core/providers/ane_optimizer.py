"""
ANE (Apple Neural Engine) Optimizer — M-series chip-aware inference routing.

Based on research findings:
- ANE is best for small background models (2W vs 20W GPU)
- CoreML overhead makes it poor for latency-sensitive decode, but good for prefill
- INT8 and FP16 have identical throughput on ANE
- 1x1 convolution conversion for matmuls is the native ANE pattern
- For large matmuls, CoreML adds little overhead vs lower-level interface

Provides per-chip profiles and battery-aware routing for T5 edge deployment.
"""

import logging
import os
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ChipProfile:
    """Performance profile for a specific Apple Silicon chip."""
    chip: str  # "M1", "M2", "M3", "M4", "M5"
    ane_tops: float  # Peak ANE performance in TOPS
    gpu_cores: int
    max_memory_gb: int
    optimal_batch_size: int
    ane_prefill_speedup: float  # Multiplier vs GPU-only for prefill
    power_efficiency: float  # Tokens per watt estimate
    notes: str = ""


# Per-chip profiles based on Apple Silicon specifications and benchmarks
CHIP_PROFILES: Dict[str, ChipProfile] = {
    "M1": ChipProfile(
        chip="M1", ane_tops=11.0, gpu_cores=8, max_memory_gb=16,
        optimal_batch_size=1, ane_prefill_speedup=1.3, power_efficiency=5.0,
        notes="First gen ANE, 16-core, 11 TOPS. Good for small models only.",
    ),
    "M2": ChipProfile(
        chip="M2", ane_tops=15.8, gpu_cores=10, max_memory_gb=24,
        optimal_batch_size=1, ane_prefill_speedup=1.5, power_efficiency=6.5,
        notes="16-core ANE, 15.8 TOPS. Improved bandwidth for prefill.",
    ),
    "M3": ChipProfile(
        chip="M3", ane_tops=18.0, gpu_cores=10, max_memory_gb=24,
        optimal_batch_size=2, ane_prefill_speedup=1.6, power_efficiency=7.0,
        notes="16-core ANE, 18 TOPS. Dynamic caching improves throughput.",
    ),
    "M4": ChipProfile(
        chip="M4", ane_tops=38.0, gpu_cores=10, max_memory_gb=32,
        optimal_batch_size=4, ane_prefill_speedup=2.0, power_efficiency=9.0,
        notes="16-core ANE, 38 TOPS. Major ANE upgrade. Best for prefill offload.",
    ),
}


def detect_chip() -> Optional[str]:
    """Detect which Apple Silicon chip is present."""
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=5,
        )
        brand = result.stdout.strip()
        for chip in ("M4", "M3", "M2", "M1"):
            if chip in brand:
                return chip
        # Also check for "Apple" prefix (M5 future-proofing)
        if "Apple" in brand:
            # Extract M-number
            import re
            match = re.search(r"M(\d+)", brand)
            if match:
                return f"M{match.group(1)}"
        return None
    except Exception:
        return None


def get_chip_profile() -> Optional[ChipProfile]:
    """Get the performance profile for the current chip."""
    chip = detect_chip()
    if not chip:
        return None
    return CHIP_PROFILES.get(chip)


def is_on_battery() -> bool:
    """Check if running on battery (macOS)."""
    if platform.system() != "Darwin":
        return False
    try:
        result = subprocess.run(
            ["pmset", "-g", "batt"],
            capture_output=True, text=True, timeout=5,
        )
        return "'Battery Power'" in result.stdout
    except Exception:
        return False


@dataclass
class InferenceRouting:
    """Recommended inference routing for current conditions."""
    use_ane_prefill: bool = False
    use_gpu_decode: bool = True
    batch_size: int = 1
    quantization: str = "Q4_K_XL"
    estimated_tps: float = 0.0
    power_mode: str = "balanced"  # "efficiency", "balanced", "performance"
    notes: str = ""


def get_inference_routing(
    model_size_b: float = 9.0,
    context_length: int = 4096,
) -> InferenceRouting:
    """
    Get optimal inference routing for current hardware and conditions.

    Considers: chip capability, battery state, model size, context length.
    """
    profile = get_chip_profile()
    routing = InferenceRouting()

    if not profile:
        routing.notes = "Not Apple Silicon — using GPU-only"
        return routing

    on_battery = is_on_battery()

    # Battery-aware routing
    if on_battery:
        routing.power_mode = "efficiency"
        routing.use_ane_prefill = True  # ANE is 10x more power efficient
        routing.use_gpu_decode = True  # Decode still needs GPU (memory bandwidth)
        routing.batch_size = 1
        routing.quantization = "IQ2_M"  # Smaller quant on battery
        routing.estimated_tps = profile.power_efficiency * 2
        routing.notes = f"Battery mode on {profile.chip}: ANE prefill + GPU decode, IQ2_M quant"
    else:
        routing.power_mode = "performance"
        # ANE prefill worthwhile for larger contexts
        routing.use_ane_prefill = context_length > 2048 and profile.ane_tops > 15
        routing.use_gpu_decode = True
        routing.batch_size = profile.optimal_batch_size
        routing.quantization = "Q4_K_XL"
        routing.estimated_tps = profile.ane_tops * 1.5 if model_size_b <= 9 else profile.ane_tops * 0.5
        routing.notes = (
            f"{profile.chip}: {'ANE prefill + ' if routing.use_ane_prefill else ''}"
            f"GPU decode, {routing.quantization}"
        )

    return routing


def generate_modelfile(
    model_name: str,
    gguf_path: str,
    chip: Optional[str] = None,
) -> str:
    """
    Generate an Ollama Modelfile optimized for the current chip.

    Applies per-chip context length, batch size, and GPU layer recommendations.
    """
    chip = chip or detect_chip() or "M1"
    profile = CHIP_PROFILES.get(chip, CHIP_PROFILES["M1"])

    # Context length based on available memory
    ctx_length = min(profile.max_memory_gb * 4096, 131072)

    return f"""FROM {gguf_path}

# Optimized for Apple Silicon {chip} ({profile.ane_tops} TOPS ANE, {profile.gpu_cores} GPU cores)
PARAMETER num_ctx {ctx_length}
PARAMETER num_batch {profile.optimal_batch_size * 512}
PARAMETER num_gpu 999
PARAMETER temperature 0.7

TEMPLATE \"\"\"{{{{- if .System }}}}{{{{ .System }}}}{{{{- end }}}}
{{{{- range .Messages }}}}
{{{{ if eq .Role "user" }}}}User: {{{{ .Content }}}}
{{{{ else }}}}Assistant: {{{{ .Content }}}}
{{{{ end }}}}
{{{{- end }}}}
Assistant: \"\"\"

SYSTEM \"\"\"You are ABLE, an autonomous AI agent. Be direct, proactive, and action-oriented.\"\"\"
"""
