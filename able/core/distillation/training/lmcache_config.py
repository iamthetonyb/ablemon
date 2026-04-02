"""LMCache configuration for prefix caching.

Every ABLE request shares a long system prompt. Caching the KV
computation for that shared prefix avoids redundant GPU work and
cuts time-to-first-token significantly.
"""

from __future__ import annotations

from typing import Any


class LMCacheConfig:
    """Configure LMCache for prefix caching."""

    def generate_config(
        self,
        model_path: str,
        backend: str = "ollama",
        cache_size_gb: float = 4.0,
        port: int = 11434,
    ) -> dict[str, Any]:
        """Generate an LMCache configuration dict.

        Args:
            model_path: Path to the GGUF model file.
            backend: Inference backend — "ollama" or "vllm".
            cache_size_gb: Max size of the KV cache on disk.
            port: Port of the backend server.

        Returns:
            Configuration dict suitable for writing to YAML/JSON.
        """
        config: dict[str, Any] = {
            "model_path": model_path,
            "backend": backend,
            "cache": {
                "type": "disk",
                "max_size_gb": cache_size_gb,
                "eviction_policy": "lru",
            },
            "prefix_caching": {
                "enabled": True,
                "min_prefix_len": 256,
                "max_prefix_len": 8192,
            },
        }

        if backend == "ollama":
            config["backend_config"] = {
                "endpoint": f"http://localhost:{port}",
                "api_version": "v1",
            }
        elif backend == "vllm":
            config["backend_config"] = {
                "endpoint": f"http://localhost:{port}",
                "tensor_parallel": 1,
            }

        return config
