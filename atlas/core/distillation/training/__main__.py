"""CLI for the ATLAS training pipeline.

Usage:
    python -m atlas.core.distillation.training --check
    python -m atlas.core.distillation.training --train all
    python -m atlas.core.distillation.training --budget
    python -m atlas.core.distillation.training --estimate
    python -m atlas.core.distillation.training --status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from atlas.core.distillation.training.gpu_budget import GPUBudget
from atlas.core.distillation.training.gpu_preflight import GPUPreflight
from atlas.core.distillation.training.training_orchestrator import TrainingOrchestrator


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas.core.distillation.training",
        description="ATLAS training pipeline for dual-model QLoRA fine-tuning.",
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
        help="Model to check (for --check). 'all', 'atlas-student-27b', or 'atlas-nano-9b'.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    budget = GPUBudget()
    orchestrator = TrainingOrchestrator(gpu_budget=budget)

    if args.check:
        preflight = GPUPreflight(gpu_budget=budget)
        result = preflight.run(model_name=args.model, tenant_id=args.tenant)
        _print_json(result)
        sys.exit(0 if result["ready"] else 1)

    if args.train:
        result = asyncio.run(orchestrator.run(mode=args.train, tenant_id=args.tenant))
        _print_json(result)
        sys.exit(0 if result.get("status") == "done" else 1)

    if args.budget:
        _print_json(budget.get_summary())
        return

    if args.estimate:
        estimate = orchestrator.estimate_time(orchestrator._corpus_size())
        _print_json(estimate)
        return

    if args.status:
        _print_json(orchestrator.get_status())
        return


def _print_json(data: dict) -> None:
    print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    main()
