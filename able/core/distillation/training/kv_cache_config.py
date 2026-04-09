"""KV cache quantization strategy recommendations.

Generates optimal KV cache type settings for Ollama Modelfiles and
llama-server CLI flags based on model, available VRAM, and target context.

Based on TurboQuant research (Google Research, 2025):
- Keys need more precision than values (up to 182x norm difference)
- Asymmetric K/V quantization: K at higher precision, V can go lower
- q4_0 KV cache gives ~2x effective context at same VRAM
- TurboQuant tq3/tq4 not yet in Ollama (merged in llama.cpp main)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KVCacheStrategy:
    """Recommended KV cache configuration for a model."""

    cache_type_k: str
    cache_type_v: str
    flash_attention: bool
    context_target: int
    estimated_kv_savings: float  # multiplier vs f16 baseline
    notes: str = ""


# VRAM headroom needed for KV cache at various context lengths (rough estimates)
# These assume the model weights are already loaded.
_KV_VRAM_PER_128K_F16 = {
    "gemma4-31b": 8.0,   # ~8GB for 128K context at f16 KV
    "gemma4-e4b": 2.0,   # ~2GB for 128K context at f16 KV (smaller model)
    "qwen3.5-27b": 7.0,
    "qwen3.5-9b": 2.5,
}


def recommend_kv_strategy(
    model_name: str,
    vram_gb: float,
    target_context: int = 131072,
) -> KVCacheStrategy:
    """Recommend optimal KV cache quantization for a model and hardware.

    Args:
        model_name: Model identifier (e.g. "gemma4-31b", "qwen3.5-9b").
        vram_gb: Available VRAM in GB (after model weights loaded).
        target_context: Desired context window size.

    Returns:
        KVCacheStrategy with recommended settings.
    """
    base_kv_gb = _KV_VRAM_PER_128K_F16.get(model_name, 6.0)
    context_ratio = target_context / 131072
    needed_f16 = base_kv_gb * context_ratio

    # Determine what we can afford
    if vram_gb >= needed_f16 * 1.2:
        # Plenty of VRAM — use f16 KV for max quality
        return KVCacheStrategy(
            cache_type_k="f16",
            cache_type_v="f16",
            flash_attention=True,
            context_target=target_context,
            estimated_kv_savings=1.0,
            notes="Sufficient VRAM for full f16 KV cache.",
        )

    if vram_gb >= needed_f16 * 0.5:
        # Moderate VRAM — q4_0 KV gives ~2x compression
        return KVCacheStrategy(
            cache_type_k="q4_0",
            cache_type_v="q4_0",
            flash_attention=True,
            context_target=target_context,
            estimated_kv_savings=2.0,
            notes=(
                "q4_0 KV cache: ~2x context at same VRAM. "
                "Upgrade to K=tq4, V=tq3 when Ollama supports TurboQuant."
            ),
        )

    if vram_gb >= needed_f16 * 0.3:
        # Tight VRAM — q8_0 keys (more precision), q4_0 values
        return KVCacheStrategy(
            cache_type_k="q8_0",
            cache_type_v="q4_0",
            flash_attention=True,
            context_target=min(target_context, 65536),
            estimated_kv_savings=2.5,
            notes=(
                "Asymmetric KV: keys at q8_0 (need precision), values at q4_0. "
                "Context capped to 64K due to VRAM constraints."
            ),
        )

    # Very tight — reduce context window
    reduced_ctx = max(8192, int(target_context * (vram_gb / needed_f16)))
    return KVCacheStrategy(
        cache_type_k="q4_0",
        cache_type_v="q4_0",
        flash_attention=True,
        context_target=reduced_ctx,
        estimated_kv_savings=2.0,
        notes=f"VRAM constrained — context reduced to {reduced_ctx}.",
    )


def generate_modelfile_params(strategy: KVCacheStrategy) -> str:
    """Generate Ollama Modelfile PARAMETER lines from a KV cache strategy."""
    lines = []
    if strategy.flash_attention:
        lines.append("PARAMETER flash_attention on")
    if strategy.cache_type_k != "f16":
        lines.append(f"PARAMETER cache_type_k {strategy.cache_type_k}")
    if strategy.cache_type_v != "f16":
        lines.append(f"PARAMETER cache_type_v {strategy.cache_type_v}")
    lines.append(f"PARAMETER num_ctx {strategy.context_target}")
    return "\n".join(lines)


def generate_server_flags(strategy: KVCacheStrategy) -> list[str]:
    """Generate llama-server CLI flags from a KV cache strategy."""
    flags = [f"--ctx-size {strategy.context_target}"]
    if strategy.flash_attention:
        flags.append("--flash-attn")
    if strategy.cache_type_k != "f16":
        flags.append(f"--cache-type-k {strategy.cache_type_k}")
    if strategy.cache_type_v != "f16":
        flags.append(f"--cache-type-v {strategy.cache_type_v}")
    return flags
