"""Pre-flight checks before an H100 training session.

Goal: zero GPU minutes wasted on configuration mistakes.
Run all checks locally (CPU) before claiming the GPU.
"""

from __future__ import annotations

import os
from typing import Any

from atlas.core.distillation.training.gpu_budget import GPUBudget
from atlas.core.distillation.training.model_configs import StudentModelConfig, resolve_models
from atlas.core.distillation.training.training_orchestrator import TrainingOrchestrator


class GPUPreflight:
    """Pre-flight checks before H100 session. Zero GPU minutes wasted."""

    def __init__(
        self,
        corpus_dir: str | None = None,
        output_dir: str | None = None,
        gpu_budget: GPUBudget | None = None,
    ) -> None:
        self.corpus_dir = corpus_dir or os.path.expanduser(
            "~/.atlas/distillation/corpus"
        )
        self.output_dir = output_dir or os.path.expanduser(
            "~/.atlas/distillation/output"
        )
        self.gpu_budget = gpu_budget or GPUBudget()
        self._orchestrator = TrainingOrchestrator(gpu_budget=self.gpu_budget)

    def run(
        self,
        model_name: str = "all",
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Run all pre-flight checks.

        Args:
            model_name: "all", "atlas-student-27b", or "atlas-nano-9b".
            tenant_id: Tenant for budget scoping.

        Returns:
            Dict with pass/fail for each check plus an overall ``ready`` bool.
        """
        models = resolve_models(model_name)

        results: dict[str, Any] = {"models": {}}
        all_pass = True

        for cfg in models:
            checks = self._check_model(cfg, tenant_id)
            model_pass = all(v["pass"] for v in checks.values())
            results["models"][cfg.name] = {
                "checks": checks,
                "pass": model_pass,
            }
            if not model_pass:
                all_pass = False

        results["ready"] = all_pass
        return results

    # ------------------------------------------------------------------
    # Per-model checks
    # ------------------------------------------------------------------

    def _check_model(
        self, cfg: StudentModelConfig, tenant_id: str
    ) -> dict[str, dict[str, Any]]:
        checks: dict[str, dict[str, Any]] = {}

        # 1. Corpus exists
        corpus_exists = os.path.isdir(self.corpus_dir) and bool(
            os.listdir(self.corpus_dir)
        )
        checks["corpus_exists"] = {
            "pass": corpus_exists,
            "detail": self.corpus_dir if corpus_exists else "corpus directory missing or empty",
        }

        # 2. Train split present
        train_path = os.path.join(self.corpus_dir, "train.jsonl")
        train_exists = os.path.isfile(train_path)
        checks["train_split_ready"] = {
            "pass": train_exists,
            "detail": train_path if train_exists else "train.jsonl not found",
        }

        # 3. Validation split present
        val_path = os.path.join(self.corpus_dir, "val.jsonl")
        val_exists = os.path.isfile(val_path)
        checks["val_split_ready"] = {
            "pass": val_exists,
            "detail": val_path if val_exists else "val.jsonl not found",
        }

        # 4. GPU budget sufficient
        estimate = self._orchestrator.estimate_time(
            corpus_size=self._count_lines(train_path)
        )
        needed = estimate.get(cfg.name, {}).get("hours", 0.0)
        can_train = self.gpu_budget.can_train(needed)
        checks["gpu_budget_sufficient"] = {
            "pass": can_train,
            "detail": (
                f"{needed:.1f}h needed, {self.gpu_budget.remaining():.1f}h available"
            ),
        }

        # 5. Output directory writable
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            writable = os.access(self.output_dir, os.W_OK)
        except OSError:
            writable = False
        checks["output_writable"] = {
            "pass": writable,
            "detail": self.output_dir if writable else "output directory not writable",
        }

        return checks

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _count_lines(path: str) -> int:
        if not os.path.isfile(path):
            return 0
        with open(path) as f:
            return sum(1 for _ in f)
