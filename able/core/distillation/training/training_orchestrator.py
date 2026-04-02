"""Full training lifecycle orchestrator.

Pipeline: corpus check -> train -> merge -> quantize -> validate -> deploy.

Runtime policy:
- 27B prefers H100, falls back to A100 (40GB+) or L4 (24GB, tight).
- 9B defaults to the free T4 Colab lane (12-24h/day) with checkpoint/resume.
- GPU fallback: if the preferred class is budget-exhausted, try the next in
  the chain defined in model_configs.GPU_FALLBACK_CHAINS.

All harvesting, scrubbing, corpus building, and federation sync run on CPU.
GPU is only consumed during this training step.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from able.core.distillation.training.axolotl_generator import AxolotlConfigGenerator
from able.core.distillation.training.gpu_budget import GPUBudget
from able.core.distillation.training.model_configs import (
    GPU_FALLBACK_CHAINS,
    resolve_models,
    resolve_runtime_profile,
)

logger = logging.getLogger(__name__)

# Hours per 1000 examples (with Unsloth 2x speed).  Rates reflect actual
# GPU throughput; the free T4 lane runs at half the speed of H100 but costs $0.
_TRAINING_RATES = {
    "h100_session": {
        "able-student-27b": 0.9,
        "able-nano-9b": 0.3,
    },
    "a100_session": {
        "able-student-27b": 1.2,
        "able-nano-9b": 0.4,
    },
    "l4_session": {
        "able-student-27b": 2.0,
        "able-nano-9b": 0.8,
    },
    "t4_colab": {
        "able-nano-9b": 1.4,
    },
    "local": {
        "able-nano-9b": 2.2,
    },
}

_OVERHEAD_HOURS = {
    "h100_session": 0.5,
    "a100_session": 0.4,
    "l4_session": 0.35,
    "t4_colab": 0.35,
    "local": 0.2,
}


class TrainingOrchestrator:
    """Full lifecycle: corpus -> train -> merge -> quantize -> deploy."""

    VALID_STATES = (
        "idle",
        "preflight",
        "training_27b",
        "training_9b",
        "merging",
        "quantizing",
        "validating",
        "done",
        "failed",
    )

    def __init__(
        self,
        gpu_budget: GPUBudget | None = None,
        corpus_dir: str | None = None,
        output_dir: str | None = None,
        gpu_class: str | None = None,
        runtime: str | None = None,
        checkpoint_dir: str | None = None,
        resume: bool = False,
    ) -> None:
        self.gpu_budget = gpu_budget or GPUBudget()
        self.corpus_dir = corpus_dir or os.path.expanduser("~/.able/distillation/corpus")
        self.output_dir = output_dir or os.path.expanduser("~/.able/distillation/output")
        self.checkpoint_dir = checkpoint_dir or os.path.expanduser(
            "~/.able/distillation/checkpoints"
        )
        self.gpu_class = gpu_class
        self.runtime = runtime
        self.resume = resume
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
        build_corpus: bool = True,
        gpu_class: str | None = None,
        runtime: str | None = None,
        checkpoint_dir: str | None = None,
        resume: bool | None = None,
    ) -> dict[str, Any]:
        """Execute a training run."""
        requested_gpu = gpu_class or self.gpu_class
        selected_runtime = runtime or self.runtime
        selected_checkpoint_dir = checkpoint_dir or self.checkpoint_dir
        selected_resume = self.resume if resume is None else resume

        corpus_path = self._resolve_corpus(tenant_id, build_corpus)
        if not corpus_path:
            self.status = "failed"
            return {
                "status": "no_corpus",
                "tenant_id": tenant_id,
                "message": "No training data available. Run harvesters first.",
            }

        models = resolve_models(mode)
        corpus_size = self._count_lines(corpus_path / "train.jsonl")

        profiles: dict[str, dict[str, Any]] = {}
        budget_plan: dict[str, float] = {}
        for model in models:
            # Resolve GPU class with fallback chain when no explicit class given
            model_gpu = requested_gpu or self._resolve_gpu_with_fallback(
                model, corpus_size,
            )
            try:
                profiles[model.name] = resolve_runtime_profile(
                    model,
                    gpu_class=model_gpu,
                    runtime=selected_runtime,
                )
            except ValueError as exc:
                self.status = "failed"
                return {
                    "status": "unsupported_gpu_class",
                    "tenant_id": tenant_id,
                    "gpu_class": model_gpu,
                    "message": str(exc),
                }

        missing_rates = []
        estimates: dict[str, dict[str, float]] = {}
        pools_used = {profile["gpu_class"] for profile in profiles.values()}
        for model in models:
            model_gpu = profiles[model.name]["gpu_class"]
            pool_estimate = self.estimate_time(corpus_size, pool=model_gpu)
            if model.name not in pool_estimate:
                missing_rates.append(model.name)
                continue
            estimates[model.name] = pool_estimate[model.name]
            budget_plan[model_gpu] = budget_plan.get(model_gpu, 0.0) + pool_estimate[model.name]["hours"]

        if missing_rates:
            self.status = "failed"
            return {
                "status": "unsupported_gpu_class",
                "tenant_id": tenant_id,
                "message": f"No training rate configured for {', '.join(missing_rates)} on the selected GPU pools.",
            }

        for pool in pools_used:
            budget_plan[pool] = budget_plan.get(pool, 0.0) + _OVERHEAD_HOURS.get(pool, 0.5)

        for pool, hours_needed in budget_plan.items():
            if not self.gpu_budget.can_train(hours_needed, pool=pool):
                self.status = "failed"
                return {
                    "status": "budget_exceeded",
                    "tenant_id": tenant_id,
                    "gpu_class": pool,
                    "needed_hours": round(hours_needed, 2),
                    "available_hours": self.gpu_budget.remaining(pool=pool),
                }

        results: dict[str, Any] = {
            "configs": {},
            "profiles": profiles,
            "status": "planned",
            "corpus_path": str(corpus_path),
            "corpus_size": corpus_size,
            "tenant_id": tenant_id,
            "gpu_class": requested_gpu or "auto",
            "runtime": selected_runtime or profiles[next(iter(profiles))]["runtime"],
            "checkpoint_dir": selected_checkpoint_dir,
            "resume": selected_resume,
            "budget_plan": {pool: round(hours, 2) for pool, hours in budget_plan.items()},
        }

        for model in models:
            self.status = f"training_{model.name.split('-')[-1]}"
            model_checkpoint_dir = os.path.join(
                selected_checkpoint_dir,
                tenant_id,
                model.name,
            )
            config_path = self._generator.generate(
                model_config=model,
                corpus_path=str(corpus_path / "train.jsonl"),
                output_path=self.output_dir,
                epochs=epochs,
                tenant_id=tenant_id,
                gpu_class=profiles[model.name]["gpu_class"],
                runtime=profiles[model.name]["runtime"],
                checkpoint_dir=model_checkpoint_dir,
                resume=selected_resume,
            )
            results["configs"][model.name] = config_path

        for pool, hours_used in budget_plan.items():
            self.gpu_budget.record_usage(
                hours_used,
                purpose="training",
                tenant_id=tenant_id,
                pool=pool,
            )
        self.status = "done"
        results["status"] = "done"
        results["hours_used"] = round(sum(budget_plan.values()), 2)
        return results

    async def run_tenant_full(
        self,
        tenant_id: str,
        mode: str = "all",
        epochs: int = 3,
        include_projector: bool = True,
        gpu_class: str | None = None,
        runtime: str | None = None,
        checkpoint_dir: str | None = None,
        resume: bool | None = None,
    ) -> dict[str, Any]:
        """Run full training pipeline for a tenant."""
        results: dict[str, Any] = {"tenant_id": tenant_id, "pipelines": {}}

        qlora_result = await self.run(
            mode=mode,
            tenant_id=tenant_id,
            epochs=epochs,
            build_corpus=True,
            gpu_class=gpu_class,
            runtime=runtime,
            checkpoint_dir=checkpoint_dir,
            resume=resume,
        )
        results["pipelines"]["qlora"] = qlora_result

        if tenant_id == "0wav" and include_projector:
            try:
                from able.core.distillation.training.owav_bridge import OwavPipelineBridge

                bridge = OwavPipelineBridge(gpu_budget=self.gpu_budget)
                projector_results = await bridge.run_pipeline(
                    stages=["train_projector", "eval"],
                    epochs=50,
                    device="mps",
                )
                results["pipelines"]["projector"] = [
                    {
                        "stage": item.stage,
                        "status": item.status,
                        "duration_s": item.duration_s,
                        "gpu_hours": item.gpu_hours_used,
                    }
                    for item in projector_results
                ]
            except Exception as exc:
                logger.warning("0wav projector pipeline failed: %s", exc)
                results["pipelines"]["projector"] = {
                    "status": "failed",
                    "error": str(exc),
                }

        results["status"] = qlora_result.get("status", "unknown")
        return results

    def estimate_time(
        self,
        corpus_size: int,
        pool: str | None = None,
    ) -> dict[str, dict[str, float]]:
        """Estimate training time in hours for each model on a GPU pool."""
        selected_pool = pool or self.gpu_class or "h100_session"
        rates = _TRAINING_RATES.get(selected_pool, {})
        estimates: dict[str, dict[str, float]] = {}
        for name, rate in rates.items():
            hours = (corpus_size / 1000.0) * rate
            estimates[name] = {"hours": round(hours, 2), "examples": corpus_size}
        return estimates

    def get_status(self) -> dict[str, Any]:
        """Current pipeline status."""
        return {
            "status": self.status,
            "corpus_dir": self.corpus_dir,
            "output_dir": self.output_dir,
            "checkpoint_dir": self.checkpoint_dir,
            "gpu_class": self.gpu_class or "auto",
            "runtime": self.runtime,
            "resume": self.resume,
            "budget": self.gpu_budget.get_summary(),
            "budget_remaining_hours": {
                pool: summary["remaining_hours"]
                for pool, summary in self.gpu_budget.get_summary()["pools"].items()
            },
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_gpu_with_fallback(
        self,
        model: Any,
        corpus_size: int,
    ) -> str:
        """Walk the GPU fallback chain and return the first class with budget.

        Fallback order is defined in GPU_FALLBACK_CHAINS (e.g. H100 → A100 → L4
        for 27B).  If nothing has budget, return the model's default so the
        caller gets a clean budget-exceeded error.
        """
        chain = GPU_FALLBACK_CHAINS.get(model.name, [model.default_gpu_class])
        for gpu_class in chain:
            rates = _TRAINING_RATES.get(gpu_class, {})
            rate = rates.get(model.name)
            if rate is None:
                continue  # model has no rate on this GPU class
            estimated = (corpus_size / 1000.0) * rate + _OVERHEAD_HOURS.get(gpu_class, 0.5)
            if self.gpu_budget.can_train(estimated, pool=gpu_class):
                if gpu_class != chain[0]:
                    logger.info(
                        "GPU fallback for %s: %s → %s (%.1fh needed, %.1fh available)",
                        model.name, chain[0], gpu_class, estimated,
                        self.gpu_budget.remaining(pool=gpu_class),
                    )
                return gpu_class
        return model.default_gpu_class

    def _resolve_corpus(self, tenant_id: str, build: bool = True) -> Path | None:
        """Find or build the latest versioned corpus for a tenant."""
        tenant_dir = Path(self.corpus_dir) / tenant_id

        if build:
            try:
                from able.core.distillation.corpus_builder import CorpusBuilder

                builder = CorpusBuilder(corpus_dir=self.corpus_dir)
                if tenant_id == "default":
                    from able.core.distillation.store import DistillationStore

                    store = DistillationStore()
                    pairs = store.get_pairs(
                        tenant_id="default",
                        min_quality=builder.quality_threshold,
                        limit=100_000,
                    )
                    pair_dicts = [
                        {
                            "prompt": pair.prompt,
                            "response": pair.gold_response,
                            "domain": pair.domain,
                            "quality_score": pair.quality_score,
                            "model": pair.gold_model,
                            "tenant_id": "default",
                            "source": "able_core",
                        }
                        for pair in pairs
                    ]
                    builder.build_full(pair_dicts, tenant_id="default")
                else:
                    builder.build_tenant_with_able_base(tenant_id)
            except Exception as exc:
                logger.warning("Corpus build failed for %s: %s", tenant_id, exc)

        if not tenant_dir.exists():
            return None

        versions = sorted(
            [directory for directory in tenant_dir.iterdir() if directory.is_dir() and directory.name.startswith("v")],
            key=lambda directory: directory.name,
        )
        if not versions:
            return None

        latest = versions[-1]
        if not (latest / "train.jsonl").exists():
            return None
        return latest

    @staticmethod
    def _count_lines(path: Path) -> int:
        if not path.is_file():
            return 0
        with open(path) as handle:
            return sum(1 for _ in handle)
