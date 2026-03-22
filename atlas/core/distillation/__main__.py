"""Unified CLI for the ATLAS distillation pipeline.

Usage:
    python -m atlas.core.distillation harvest [--since 24] [--tenant default] [--dry-run]
    python -m atlas.core.distillation status
    python -m atlas.core.distillation train --check
    python -m atlas.core.distillation train --train all
    python -m atlas.core.distillation train --budget
    python -m atlas.core.distillation validate --run --candidate atlas-student-27b-v1
    python -m atlas.core.distillation corpus --status
    python -m atlas.core.distillation corpus --generate --domain coding --count 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone


def _print_json(data: dict) -> None:
    print(json.dumps(data, indent=2, default=str))


def cmd_status(args: argparse.Namespace) -> None:
    """Show full pipeline status."""
    from atlas.core.distillation.store import DistillationStore
    from atlas.core.distillation.prompt_bank import PromptBank
    from atlas.core.distillation.training.gpu_budget import GPUBudget

    store = DistillationStore()
    bank = PromptBank()
    budget = GPUBudget()

    stats = store.stats()
    bank_domains = {d: bank.count(domain=d) for d in bank.all_domains()}

    status = {
        "distillation_db": {
            "total_pairs": stats["total_pairs"],
            "by_domain": stats.get("by_domain", {}),
            "by_model": stats.get("by_model", {}),
            "corpus_tier": store.get_corpus_tier(),
        },
        "prompt_bank": {
            "total_prompts": bank.count(),
            "by_domain": bank_domains,
        },
        "gpu_budget": budget.get_summary(),
    }
    _print_json(status)


def cmd_harvest(args: argparse.Namespace) -> None:
    """Run the harvest pipeline."""
    from atlas.core.distillation.harvest_runner import run_harvest

    result = asyncio.run(run_harvest(
        since_hours=args.since,
        tenant_id=args.tenant,
        dry_run=args.dry_run,
    ))

    output = {
        "conversations": result.total_conversations,
        "deduplicated": result.total_deduplicated,
        "formatted": result.total_formatted,
        "corpus_version": result.corpus_version,
        "corpus_tier": result.corpus_tier,
        "duration_ms": result.duration_ms,
        "sources": [
            {
                "source": s.source,
                "conversations": s.conversations,
                "error": s.error,
                "duration_ms": s.duration_ms,
            }
            for s in result.sources
        ],
        "errors": result.errors,
    }
    _print_json(output)
    if result.errors:
        sys.exit(1)


def cmd_train(args: argparse.Namespace, remaining: list[str]) -> None:
    """Proxy to training CLI."""
    sys.argv = ["atlas.core.distillation.training"] + remaining
    from atlas.core.distillation.training.__main__ import main
    main()


def cmd_validate(args: argparse.Namespace, remaining: list[str]) -> None:
    """Proxy to validation CLI."""
    sys.argv = ["atlas.core.distillation.validation"] + remaining
    from atlas.core.distillation.validation.__main__ import main
    main()


def cmd_corpus(args: argparse.Namespace, remaining: list[str]) -> None:
    """Proxy to corpus generator."""
    sys.argv = ["corpus-generator"] + remaining
    # Import from skill scripts directory
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "generate",
        Path(__file__).resolve().parents[2] / "skills" / "library" / "corpus-generator" / "scripts" / "generate.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="atlas.core.distillation",
        description="ATLAS distillation pipeline — harvest, train, validate, deploy.",
    )
    sub = parser.add_subparsers(dest="command")

    # status
    sub.add_parser("status", help="Show full pipeline status")

    # harvest
    harvest_p = sub.add_parser("harvest", help="Run conversation harvesters")
    harvest_p.add_argument("--since", type=int, default=24, help="Hours to look back (default: 24)")
    harvest_p.add_argument("--tenant", default="default", help="Tenant ID")
    harvest_p.add_argument("--dry-run", action="store_true", help="Harvest only, skip corpus build")

    # train (proxy)
    sub.add_parser("train", help="Training pipeline (--check, --train, --budget, --estimate, --status)")

    # validate (proxy)
    sub.add_parser("validate", help="Validation gate (--run, --status)")

    # corpus (proxy)
    sub.add_parser("corpus", help="Corpus generator (--generate, --prompts, --status)")

    # Parse only the known args so subcommands can handle their own
    args, remaining = parser.parse_known_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "status":
        cmd_status(args)
    elif args.command == "harvest":
        cmd_harvest(args)
    elif args.command == "train":
        cmd_train(args, remaining)
    elif args.command == "validate":
        cmd_validate(args, remaining)
    elif args.command == "corpus":
        cmd_corpus(args, remaining)


if __name__ == "__main__":
    main()
