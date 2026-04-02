"""
0wav ML Pipeline Bridge — connects 0wav's audio ML training pipeline
with ABLE's training orchestrator, GPU budget, and validation gate.

0wav has its own ML pipeline:
  encoder_features.py → llm_teacher.py → label_quality.py → train_projector.py

This bridge:
  1. Wraps those tools so ABLE can orchestrate them via GPU budget
  2. Reports projector training status to ABLE metrics/Phoenix
  3. Validates projector quality using 0wav's PromptFoo eval harness
  4. Manages GPU allocation for projector training alongside ABLE QLoRA

Two pipelines coexist under ABLE's GPU budget:
  - ABLE text distillation: QLoRA fine-tune of Qwen 3.5 27B/9B
  - 0wav projector training: MLP train on H100/MPS/CPU
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_0WAV_PROJECT = "/Users/abenton333/Desktop/ai-0wav-hub"

# GPU time estimates for 0wav pipeline stages (H100 80GB)
_GPU_ESTIMATES = {
    "encoder_features": 0.1,      # ~6min for 228 samples
    "llm_teacher": 0.0,           # API calls, no GPU
    "label_quality": 0.0,         # CPU-only filtering
    "train_projector_50ep": 0.05,  # ~3min for 228 samples, 50 epochs
    "train_projector_100ep": 0.1,  # ~6min for 228 samples, 100 epochs
    "full_pipeline": 0.2,         # ~12min total
}


@dataclass
class OwavTrainingResult:
    """Result of a 0wav pipeline run."""

    stage: str
    status: str  # "success" | "failed" | "skipped"
    duration_s: float = 0
    gpu_hours_used: float = 0
    details: dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


class OwavPipelineBridge:
    """Bridges 0wav's ML pipeline with ABLE infrastructure.

    Wraps the 0wav tools/ml/ scripts and manages them through
    ABLE's GPU budget and monitoring.
    """

    STAGES = (
        "encoder_features",
        "llm_teacher",
        "label_quality",
        "train_projector",
        "eval",
    )

    def __init__(
        self,
        project_path: str = DEFAULT_0WAV_PROJECT,
        gpu_budget=None,
    ):
        self.project_path = Path(project_path)
        self.tools_dir = self.project_path / "tools" / "ml"
        self._gpu_budget = gpu_budget

    @property
    def gpu_budget(self):
        if self._gpu_budget is None:
            from able.core.distillation.training.gpu_budget import GPUBudget
            self._gpu_budget = GPUBudget()
        return self._gpu_budget

    # ── Status ────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Get current 0wav pipeline status."""
        from able.core.distillation.harvesters.owav_ml_harvester import (
            OwavPipelineStats,
        )

        stats = OwavPipelineStats(str(self.project_path)).get_stats()
        stats["gpu_budget"] = {
            "remaining_hours": self.gpu_budget.remaining(),
            "estimate_full_pipeline": _GPU_ESTIMATES["full_pipeline"],
            "can_train": self.gpu_budget.can_train(
                _GPU_ESTIMATES["full_pipeline"]
            ),
        }
        stats["tools_available"] = self._check_tools()
        return stats

    def _check_tools(self) -> dict[str, bool]:
        """Check which 0wav ML tools are available."""
        tools = {}
        for name in (
            "encoder_features",
            "llm_teacher",
            "label_quality",
            "train_projector",
            "run_accuracy_pipeline",
        ):
            tools[name] = (self.tools_dir / f"{name}.py").exists()
        return tools

    # ── Pipeline stages ───────────────────────────────────────────────

    def estimate_training(self, epochs: int = 50) -> dict[str, Any]:
        """Estimate GPU time for a full 0wav pipeline run."""
        key = (
            "train_projector_100ep" if epochs >= 100
            else "train_projector_50ep"
        )
        projector_hours = _GPU_ESTIMATES[key]
        total = (
            _GPU_ESTIMATES["encoder_features"]
            + projector_hours
        )

        manifest_path = self.project_path / "training_data" / "manifest.json"
        sample_count = 0
        if manifest_path.exists():
            with open(manifest_path) as f:
                data = json.load(f)
            sample_count = len(data.get("samples", []))

        return {
            "stages": {
                "encoder_features": {
                    "hours": _GPU_ESTIMATES["encoder_features"],
                    "gpu_required": True,
                },
                "llm_teacher": {
                    "hours": 0,
                    "gpu_required": False,
                    "note": "API calls (Gemini + Voxtral)",
                },
                "label_quality": {
                    "hours": 0,
                    "gpu_required": False,
                },
                "train_projector": {
                    "hours": projector_hours,
                    "gpu_required": True,
                    "epochs": epochs,
                },
            },
            "total_gpu_hours": total,
            "sample_count": sample_count,
            "budget_available": self.gpu_budget.remaining(),
            "can_run": self.gpu_budget.can_train(total),
        }

    def run_preflight(self) -> dict[str, Any]:
        """Check everything is ready before GPU session."""
        checks: dict[str, Any] = {}

        # Manifest
        manifest_path = self.project_path / "training_data" / "manifest.json"
        checks["manifest_exists"] = manifest_path.exists()
        if manifest_path.exists():
            with open(manifest_path) as f:
                data = json.load(f)
            checks["sample_count"] = len(data.get("samples", []))
        else:
            checks["sample_count"] = 0

        # Features
        features_dir = self.project_path / "training_data" / "features"
        checks["features_dir"] = features_dir.exists()
        if features_dir.exists():
            checks["feature_files"] = len(list(features_dir.glob("*.npy")))
        else:
            checks["feature_files"] = 0

        # Quality weights
        weights_path = self.project_path / "training_data" / "quality_weights.json"
        checks["quality_weights"] = weights_path.exists()

        # Tools
        checks["tools"] = self._check_tools()

        # GPU budget
        estimate = _GPU_ESTIMATES["full_pipeline"]
        checks["gpu_budget_hours"] = self.gpu_budget.remaining()
        checks["gpu_estimate_hours"] = estimate
        checks["gpu_budget_ok"] = self.gpu_budget.can_train(estimate)

        # Models dir
        models_dir = self.project_path / "training_data" / "models"
        checks["models_dir"] = models_dir.exists()
        if models_dir.exists():
            best = models_dir / "projector_best.pt"
            checks["previous_best_exists"] = best.exists()

        checks["ready"] = all([
            checks["manifest_exists"],
            checks["sample_count"] > 0,
            checks["feature_files"] > 0,
            checks["gpu_budget_ok"],
            checks.get("tools", {}).get("train_projector", False),
        ])

        return checks

    async def run_pipeline(
        self,
        stages: list[str] | None = None,
        epochs: int = 50,
        device: str = "mps",
        dry_run: bool = False,
    ) -> list[OwavTrainingResult]:
        """Run the 0wav ML pipeline (or a subset of stages).

        Args:
            stages: List of stages to run. None = all stages.
            epochs: Projector training epochs.
            device: Training device (cuda/mps/cpu).
            dry_run: Log commands but don't execute.
        """
        if stages is None:
            stages = list(self.STAGES)

        # Budget check
        estimate = self.estimate_training(epochs)
        if not dry_run and not estimate["can_run"]:
            return [OwavTrainingResult(
                stage="preflight",
                status="failed",
                details={"reason": "GPU budget exceeded", **estimate},
            )]

        results = []
        manifest = str(self.project_path / "training_data" / "manifest.json")

        for stage in stages:
            if stage == "encoder_features":
                r = await self._run_stage(
                    stage,
                    script="encoder_features.py",
                    args=["--manifest", manifest, "--device", device],
                    gpu_hours=_GPU_ESTIMATES["encoder_features"],
                    dry_run=dry_run,
                )
            elif stage == "llm_teacher":
                r = await self._run_stage(
                    stage,
                    script="llm_teacher.py",
                    args=["--manifest", manifest],
                    gpu_hours=0,
                    dry_run=dry_run,
                )
            elif stage == "label_quality":
                r = await self._run_stage(
                    stage,
                    script="label_quality.py",
                    args=["--manifest", manifest],
                    gpu_hours=0,
                    dry_run=dry_run,
                )
            elif stage == "train_projector":
                r = await self._run_stage(
                    stage,
                    script="train_projector.py",
                    args=[
                        "--manifest", manifest,
                        "--epochs", str(epochs),
                        "--device", device,
                    ],
                    gpu_hours=_GPU_ESTIMATES.get(
                        f"train_projector_{epochs}ep",
                        _GPU_ESTIMATES["train_projector_50ep"],
                    ),
                    dry_run=dry_run,
                )
            elif stage == "eval":
                r = await self._run_stage(
                    stage,
                    script="run_accuracy_pipeline.py",
                    args=["--manifest", manifest, "--eval-only"],
                    gpu_hours=0,
                    dry_run=dry_run,
                )
            else:
                r = OwavTrainingResult(
                    stage=stage, status="skipped",
                    details={"reason": f"Unknown stage: {stage}"},
                )

            results.append(r)

            # Stop on failure
            if r.status == "failed" and not dry_run:
                logger.error("[0wav] Stage %s failed, stopping pipeline", stage)
                break

        # Record GPU usage
        if not dry_run:
            total_gpu = sum(r.gpu_hours_used for r in results)
            if total_gpu > 0:
                self.gpu_budget.record_usage(
                    total_gpu,
                    purpose="0wav_projector_training",
                    tenant_id="0wav",
                )

        return results

    async def _run_stage(
        self,
        stage: str,
        script: str,
        args: list[str],
        gpu_hours: float,
        dry_run: bool,
    ) -> OwavTrainingResult:
        """Run a single pipeline stage."""
        script_path = self.tools_dir / script

        if not script_path.exists():
            return OwavTrainingResult(
                stage=stage, status="skipped",
                details={"reason": f"Script not found: {script_path}"},
            )

        cmd = ["python", str(script_path)] + args
        logger.info("[0wav] %s: %s", "DRY-RUN" if dry_run else "Running", " ".join(cmd))

        if dry_run:
            return OwavTrainingResult(
                stage=stage, status="dry_run",
                gpu_hours_used=0,
                details={"command": " ".join(cmd)},
            )

        import time
        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.project_path),
                capture_output=True,
                text=True,
                timeout=3600,  # 1hr max per stage
            )
            elapsed = time.monotonic() - start

            if result.returncode == 0:
                return OwavTrainingResult(
                    stage=stage,
                    status="success",
                    duration_s=elapsed,
                    gpu_hours_used=gpu_hours,
                    details={
                        "stdout_tail": result.stdout[-500:] if result.stdout else "",
                    },
                )
            else:
                return OwavTrainingResult(
                    stage=stage,
                    status="failed",
                    duration_s=elapsed,
                    details={
                        "returncode": result.returncode,
                        "stderr_tail": result.stderr[-500:] if result.stderr else "",
                    },
                )
        except Exception as e:
            elapsed = time.monotonic() - start
            return OwavTrainingResult(
                stage=stage,
                status="failed",
                duration_s=elapsed,
                details={"error": str(e)},
            )
