"""CLI for the ABLE training pipeline.

Usage:
    python -m able.core.distillation.training --check
    python -m able.core.distillation.training --train all
    python -m able.core.distillation.training --budget
    python -m able.core.distillation.training --estimate
    python -m able.core.distillation.training --status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from able.core.distillation.training.gpu_budget import GPUBudget
from able.core.distillation.training.gpu_preflight import GPUPreflight
from able.core.distillation.training.training_orchestrator import TrainingOrchestrator


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="able.core.distillation.training",
        description="ABLE training pipeline for dual-model QLoRA fine-tuning.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check",
        action="store_true",
        help="Run pre-flight checks before training.",
    )
    group.add_argument(
        "--train",
        choices=["all", "27b", "9b"],
        help="Run a training cycle (all | 27b | 9b).",
    )
    group.add_argument(
        "--budget",
        action="store_true",
        help="Show GPU budget summary.",
    )
    group.add_argument(
        "--estimate",
        action="store_true",
        help="Estimate training time for current corpus.",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Show pipeline status.",
    )
    parser.add_argument(
        "--tenant",
        default="default",
        help="Tenant ID (default: 'default').",
    )
    parser.add_argument(
        "--model",
        default="all",
        help="Model to check (for --check). 'all', 'able-student-27b', or 'able-nano-9b'.",
    )
    parser.add_argument(
        "--gpu-class",
        default=None,
        choices=["t4_colab", "h100_session", "local"],
        help="Training budget/runtime lane. Leave unset to use each model's default lane.",
    )
    parser.add_argument(
        "--runtime",
        default=None,
        help="Runtime label override (for example: colab, cloud, local).",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default=None,
        help="Checkpoint directory for save/resume flows.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoints when available.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    budget = GPUBudget()
    orchestrator = TrainingOrchestrator(
        gpu_budget=budget,
        gpu_class=args.gpu_class,
        runtime=args.runtime,
        checkpoint_dir=args.checkpoint_dir,
        resume=args.resume,
    )

    if args.check:
        preflight = GPUPreflight(
            gpu_budget=budget,
            gpu_class=args.gpu_class,
            runtime=args.runtime,
            checkpoint_dir=args.checkpoint_dir,
            resume=args.resume,
        )
        result = preflight.run(
            model_name=args.model,
            tenant_id=args.tenant,
            gpu_class=args.gpu_class,
            runtime=args.runtime,
            checkpoint_dir=args.checkpoint_dir,
            resume=args.resume,
        )
        _print_json(result)
        sys.exit(0 if result["ready"] else 1)

    if args.train:
        result = asyncio.run(
            orchestrator.run(
                mode=args.train,
                tenant_id=args.tenant,
                gpu_class=args.gpu_class,
                runtime=args.runtime,
                checkpoint_dir=args.checkpoint_dir,
                resume=args.resume,
            )
        )
        _print_json(result)
        sys.exit(0 if result.get("status") == "done" else 1)

    if args.budget:
        _print_json(budget.get_summary())
        return

    if args.estimate:
        corpus_root = orchestrator._resolve_corpus(args.tenant, build=False)
        corpus_size = 0
        if corpus_root:
            corpus_size = orchestrator._count_lines(corpus_root / "train.jsonl")
        estimate = orchestrator.estimate_time(corpus_size, pool=args.gpu_class)
        _print_json(estimate)
        return

    if args.status:
        _print_json(orchestrator.get_status())
        return


def _print_json(data: dict) -> None:
    print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    main()
