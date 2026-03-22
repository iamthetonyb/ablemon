"""Full training lifecycle orchestrator.

Pipeline: corpus check -> train 27B -> train 9B -> merge -> quantize -> validate -> deploy.

Training order: 27B first (quality reference), then 9B (same corpus).

GPU time estimates (H100 80 GB):
- 27B QLoRA: ~0.9 h per 1 K examples
- 9B QLoRA:  ~0.3 h per 1 K examples
- Both + validation: ~3.5-4 h per cycle
"""

from __future__ import annotations

import os
from typing import Any

from atlas.core.distillation.training.axolotl_generator import AxolotlConfigGenerator
from atlas.core.distillation.training.gpu_budget import GPUBudget
from atlas.core.distillation.training.model_configs import resolve_models

# Hours per 1 K training examples on an H100 80 GB.
_H100_HOURS_PER_1K = {
    "atlas-student-27b": 0.9,
    "atlas-nano-9b": 0.3,
}

# Fixed overhead (merge + quantize + validation) in hours.
_OVERHEAD_HOURS = 0.5


class TrainingOrchestrator:
    """Full lifecycle: corpus -> train -> merge -> quantize -> deploy."""

    VALID_STATES = ("idle", "preflight", "training_27b", "training_9b",
                    "merging", "quantizing", "validating", "done", "failed")

    def __init__(
        self,
        gpu_budget: GPUBudget | None = None,
        corpus_dir: str | None = None,
        output_dir: str | None = None,
    ) -> None:
        self.gpu_budget = gpu_budget or GPUBudget()
        self.corpus_dir = corpus_dir or os.path.expanduser(
            "~/.atlas/distillation/corpus"
        )
        self.output_dir = output_dir or os.path.expanduser(
            "~/.atlas/distillation/output"
        )
        self.status: str = "idle"
        self._generator = AxolotlConfigGenerator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        mode: str = "all",
        tenant_id: str = "default",
        epochs: int = 3,
    ) -> dict[str, Any]:
        """Execute a training run.

        Args:
            mode: "all" | "27b" | "9b"
            tenant_id: Tenant identifier.
            epochs: Training epochs.

        Returns:
            Summary dict with generated configs, estimated time, and status.
        """
        models = resolve_models(mode)
        total_estimate = self.estimate_time(self._corpus_size())
        total_hours = sum(
            total_estimate.get(m.name, {}).get("hours", 0.0) for m in models
        ) + _OVERHEAD_HOURS

        if not self.gpu_budget.can_train(total_hours):
            self.status = "failed"
            return {
                "status": "budget_exceeded",
                "needed_hours": total_hours,
                "available_hours": self.gpu_budget.remaining(),
            }

        results: dict[str, Any] = {"configs": {}, "status": "planned"}

        for model in models:
            self.status = f"training_{model.name.split('-')[-1]}"
            config_path = self._generator.generate(
                model_config=model,
                corpus_path=os.path.join(self.corpus_dir, "train.jsonl"),
                output_path=self.output_dir,
                epochs=epochs,
                tenant_id=tenant_id,
            )
            results["configs"][model.name] = config_path

        self.gpu_budget.record_usage(
            total_hours, purpose="training", tenant_id=tenant_id
        )
        self.status = "done"
        results["status"] = "done"
        results["hours_used"] = total_hours
        return results

    def estimate_time(self, corpus_size: int) -> dict[str, dict[str, float]]:
        """Estimate training time in hours for each model.

        Args:
            corpus_size: Number of training examples.

        Returns:
            ``{model_name: {"hours": float, "examples": int}}``
        """
        estimates: dict[str, dict[str, float]] = {}
        for name, rate in _H100_HOURS_PER_1K.items():
            hours = (corpus_size / 1000.0) * rate
            estimates[name] = {"hours": round(hours, 2), "examples": corpus_size}
        return estimates

    def get_status(self) -> dict[str, Any]:
        """Current pipeline status."""
        return {
            "status": self.status,
            "corpus_dir": self.corpus_dir,
            "output_dir": self.output_dir,
            "budget_remaining_hours": self.gpu_budget.remaining(),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _corpus_size(self) -> int:
        train_path = os.path.join(self.corpus_dir, "train.jsonl")
        if not os.path.isfile(train_path):
            return 0
        with open(train_path) as f:
            return sum(1 for _ in f)
