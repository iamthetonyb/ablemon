"""Full training lifecycle orchestrator.

Pipeline: corpus check -> train 27B -> train 9B -> merge -> quantize -> validate -> deploy.

Training order: 27B first (quality reference), then 9B (same corpus).

GPU time estimates (H100 80 GB):
- 27B QLoRA: ~0.9 h per 1 K examples
- 9B QLoRA:  ~0.3 h per 1 K examples
- Both + validation: ~3.5-4 h per cycle
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from atlas.core.distillation.training.axolotl_generator import AxolotlConfigGenerator
from atlas.core.distillation.training.gpu_budget import GPUBudget
from atlas.core.distillation.training.model_configs import resolve_models

logger = logging.getLogger(__name__)

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
        build_corpus: bool = True,
    ) -> dict[str, Any]:
        """Execute a training run.

        Args:
            mode: "all" | "27b" | "9b"
            tenant_id: Tenant identifier.
            epochs: Training epochs.
            build_corpus: If True, rebuild corpus before training.

        Returns:
            Summary dict with generated configs, estimated time, and status.
        """
        # Resolve corpus path for this tenant
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
        total_estimate = self.estimate_time(corpus_size)
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

        results: dict[str, Any] = {
            "configs": {},
            "status": "planned",
            "corpus_path": str(corpus_path),
            "corpus_size": corpus_size,
            "tenant_id": tenant_id,
        }

        for model in models:
            self.status = f"training_{model.name.split('-')[-1]}"
            config_path = self._generator.generate(
                model_config=model,
                corpus_path=str(corpus_path / "train.jsonl"),
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

    async def run_tenant_full(
        self,
        tenant_id: str,
        mode: str = "all",
        epochs: int = 3,
        include_projector: bool = True,
    ) -> dict[str, Any]:
        """Run full training pipeline for a tenant.

        For 0wav: runs ATLAS QLoRA (with 0wav corpus enrichment) + projector.
        For other tenants: runs ATLAS QLoRA with tenant corpus enrichment.
        """
        results: dict[str, Any] = {"tenant_id": tenant_id, "pipelines": {}}

        # 1. ATLAS QLoRA training (base + tenant LoRA adapter)
        qlora_result = await self.run(
            mode=mode, tenant_id=tenant_id, epochs=epochs, build_corpus=True
        )
        results["pipelines"]["qlora"] = qlora_result

        # 2. Tenant-specific pipeline (e.g., 0wav projector)
        if tenant_id == "0wav" and include_projector:
            try:
                from atlas.core.distillation.training.owav_bridge import (
                    OwavPipelineBridge,
                )

                bridge = OwavPipelineBridge(gpu_budget=self.gpu_budget)
                projector_results = await bridge.run_pipeline(
                    stages=["train_projector", "eval"],
                    epochs=50,
                    device="mps",
                )
                results["pipelines"]["projector"] = [
                    {
                        "stage": r.stage,
                        "status": r.status,
                        "duration_s": r.duration_s,
                        "gpu_hours": r.gpu_hours_used,
                    }
                    for r in projector_results
                ]
            except Exception as e:
                logger.warning("0wav projector pipeline failed: %s", e)
                results["pipelines"]["projector"] = {"status": "failed", "error": str(e)}

        results["status"] = qlora_result.get("status", "unknown")
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

    def _resolve_corpus(
        self, tenant_id: str, build: bool = True
    ) -> Path | None:
        """Find or build the latest versioned corpus for a tenant.

        For non-default tenants, uses build_tenant_with_atlas_base() to
        enrich the tenant corpus with high-quality ATLAS core pairs (80/20).
        """
        tenant_dir = Path(self.corpus_dir) / tenant_id

        if build:
            try:
                from atlas.core.distillation.corpus_builder import CorpusBuilder

                builder = CorpusBuilder(corpus_dir=self.corpus_dir)
                if tenant_id == "default":
                    # ATLAS core: build from all default-tenant pairs
                    from atlas.core.distillation.store import DistillationStore

                    store = DistillationStore()
                    pairs = store.get_pairs(
                        tenant_id="default",
                        min_quality=builder.quality_threshold,
                        limit=100_000,
                    )
                    pair_dicts = [
                        {
                            "prompt": p.prompt,
                            "response": p.gold_response,
                            "domain": p.domain,
                            "quality_score": p.quality_score,
                            "model": p.gold_model,
                            "tenant_id": "default",
                            "source": "atlas_core",
                        }
                        for p in pairs
                    ]
                    result = builder.build_full(pair_dicts, tenant_id="default")
                else:
                    # Tenant: 80% tenant data + 20% ATLAS core reasoning
                    result = builder.build_tenant_with_atlas_base(tenant_id)
                logger.info(
                    "Corpus built for %s: %s (%d pairs, tier=%s)",
                    tenant_id, result.version, result.total, result.tier,
                )
            except Exception as e:
                logger.warning("Corpus build failed for %s: %s", tenant_id, e)

        # Find latest version directory
        if not tenant_dir.exists():
            return None

        versions = sorted(
            [d for d in tenant_dir.iterdir() if d.is_dir() and d.name.startswith("v")],
            key=lambda d: d.name,
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
        with open(path) as f:
            return sum(1 for _ in f)
