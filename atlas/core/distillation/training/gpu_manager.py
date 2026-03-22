"""GPU availability detection.

Checks nvidia-smi for local GPUs and determines the execution mode:
- local   : GPU detected on this machine
- cloud   : ATLAS_GPU_CLOUD env var set (e.g. Colab/RunPod)
- manual  : No GPU detected, user handles provisioning
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any


class GPUManager:
    """Manages GPU availability detection."""

    def detect_local_gpu(self) -> dict[str, Any] | None:
        """Check nvidia-smi for a local GPU.

        Returns:
            Dict with gpu_name, memory_mb, driver_version; or None.
        """
        if not shutil.which("nvidia-smi"):
            return None

        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

        if result.returncode != 0 or not result.stdout.strip():
            return None

        line = result.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            return None

        return {
            "gpu_name": parts[0],
            "memory_mb": int(float(parts[1])),
            "driver_version": parts[2],
        }

    def get_mode(self) -> str:
        """Determine execution mode.

        Returns:
            "local" if a GPU is detected, "cloud" if ATLAS_GPU_CLOUD is
            set, otherwise "manual".
        """
        if self.detect_local_gpu() is not None:
            return "local"
        if os.environ.get("ATLAS_GPU_CLOUD"):
            return "cloud"
        return "manual"
