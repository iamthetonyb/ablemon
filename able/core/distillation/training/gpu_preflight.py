"""Pre-flight checks before a training session.

Goal: spend CPU time proving the run is valid before claiming T4/H100 runtime.
"""

from __future__ import annotations

import os
from typing import Any

from able.core.distillation.training.gpu_budget import GPUBudget
from able.core.distillation.training.model_configs import (
    StudentModelConfig,
    resolve_models,
    resolve_runtime_profile,
)
from able.core.distillation.training.training_orchestrator import TrainingOrchestrator


class GPUPreflight:
    """Pre-flight checks for T4/H100/local training lanes."""

    def __init__(
        self,
        corpus_dir: str | None = None,
        output_dir: str | None = None,
        checkpoint_dir: str | None = None,
        gpu_budget: GPUBudget | None = None,
        gpu_class: str | None = None,
        runtime: str | None = None,
        resume: bool = False,
    ) -> None:
        self.corpus_dir = corpus_dir or os.path.expanduser("~/.able/distillation/corpus")
        self.output_dir = output_dir or os.path.expanduser("~/.able/distillation/output")
        self.checkpoint_dir = checkpoint_dir or os.path.expanduser(
            "~/.able/distillation/checkpoints"
        )
        self.gpu_budget = gpu_budget or GPUBudget()
        self.gpu_class = gpu_class
        self.runtime = runtime
        self.resume = resume
        self._orchestrator = TrainingOrchestrator(
            gpu_budget=self.gpu_budget,
            corpus_dir=self.corpus_dir,
            output_dir=self.output_dir,
            gpu_class=self.gpu_class,
            runtime=self.runtime,
            checkpoint_dir=self.checkpoint_dir,
            resume=self.resume,
        )

    def run(
        self,
        model_name: str = "all",
        tenant_id: str = "default",
        gpu_class: str | None = None,
        runtime: str | None = None,
        checkpoint_dir: str | None = None,
        resume: bool | None = None,
    ) -> dict[str, Any]:
        """Run all pre-flight checks."""
        models = resolve_models(model_name)
        selected_gpu = gpu_class or self.gpu_class
        selected_runtime = runtime or self.runtime
        selected_checkpoint_dir = checkpoint_dir or self.checkpoint_dir
        selected_resume = self.resume if resume is None else resume

        results: dict[str, Any] = {
            "gpu_class": selected_gpu or "auto",
            "runtime": selected_runtime,
            "checkpoint_dir": selected_checkpoint_dir,
            "resume": selected_resume,
            "models": {},
        }
        all_pass = True

        for config in models:
            checks = self._check_model(
                config,
                tenant_id=tenant_id,
                gpu_class=selected_gpu or config.default_gpu_class,
                runtime=selected_runtime,
                checkpoint_dir=selected_checkpoint_dir,
                resume=selected_resume,
            )
            model_pass = all(item["pass"] for item in checks.values())
            results["models"][config.name] = {
                "checks": checks,
                "pass": model_pass,
            }
            if not model_pass:
                all_pass = False

        results["ready"] = all_pass
        return results

    def _check_model(
        self,
        config: StudentModelConfig,
        tenant_id: str,
        gpu_class: str,
        runtime: str | None,
        checkpoint_dir: str,
        resume: bool,
    ) -> dict[str, dict[str, Any]]:
        checks: dict[str, dict[str, Any]] = {}

        corpus_exists = os.path.isdir(self.corpus_dir) and bool(os.listdir(self.corpus_dir))
        checks["corpus_exists"] = {
            "pass": corpus_exists,
            "detail": self.corpus_dir if corpus_exists else "corpus directory missing or empty",
        }

        train_path = os.path.join(self.corpus_dir, "train.jsonl")
        train_exists = os.path.isfile(train_path)
        checks["train_split_ready"] = {
            "pass": train_exists,
            "detail": train_path if train_exists else "train.jsonl not found",
        }

        val_path = os.path.join(self.corpus_dir, "val.jsonl")
        val_exists = os.path.isfile(val_path)
        checks["val_split_ready"] = {
            "pass": val_exists,
            "detail": val_path if val_exists else "val.jsonl not found",
        }

        try:
            profile = resolve_runtime_profile(config, gpu_class=gpu_class, runtime=runtime)
            checks["gpu_class_supported"] = {
                "pass": True,
                "detail": {
                    "gpu_class": profile["gpu_class"],
                    "runtime": profile["runtime"],
                    "sequence_len": profile["sequence_len"],
                    "micro_batch_size": profile["micro_batch_size"],
                    "gradient_accumulation": profile["gradient_accumulation"],
                },
            }
        except ValueError as exc:
            profile = None
            checks["gpu_class_supported"] = {
                "pass": False,
                "detail": str(exc),
            }

        estimate = self._orchestrator.estimate_time(
            corpus_size=self._count_lines(train_path),
            pool=gpu_class,
        )
        needed = estimate.get(config.name, {}).get("hours")
        can_train = needed is not None and self.gpu_budget.can_train(needed, pool=gpu_class)
        checks["gpu_budget_sufficient"] = {
            "pass": bool(can_train),
            "detail": (
                f"{needed:.1f}h needed, {self.gpu_budget.remaining(pool=gpu_class):.1f}h available"
                if needed is not None
                else f"No estimate for {config.name} on {gpu_class}"
            ),
        }

        try:
            os.makedirs(self.output_dir, exist_ok=True)
            writable = os.access(self.output_dir, os.W_OK)
        except OSError:
            writable = False
        checks["output_writable"] = {
            "pass": writable,
            "detail": self.output_dir if writable else "output directory not writable",
        }

        checkpoint_model_dir = os.path.join(checkpoint_dir, tenant_id, config.name)
        try:
            os.makedirs(checkpoint_model_dir, exist_ok=True)
            checkpoint_writable = os.access(checkpoint_model_dir, os.W_OK)
        except OSError:
            checkpoint_writable = False
        checks["checkpoint_writable"] = {
            "pass": checkpoint_writable,
            "detail": checkpoint_model_dir if checkpoint_writable else "checkpoint directory not writable",
        }

        if resume:
            resume_ready = any(os.scandir(checkpoint_model_dir)) if os.path.isdir(checkpoint_model_dir) else False
            checks["resume_ready"] = {
                "pass": resume_ready,
                "detail": checkpoint_model_dir if resume_ready else "resume requested but no checkpoints found",
            }
        else:
            checks["resume_ready"] = {
                "pass": True,
                "detail": "resume not requested",
            }

        return checks

    @staticmethod
    def _count_lines(path: str) -> int:
        if not os.path.isfile(path):
            return 0
        with open(path) as handle:
            return sum(1 for _ in handle)
