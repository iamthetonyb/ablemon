"""CLI for the ABLE self-scheduler and evolution system.

Usage:
    python -m able.core.evolution --pending           # List pending proposals
    python -m able.core.evolution --promote ACTION_ID # Approve a dry-run proposal
    python -m able.core.evolution --reject ACTION_ID  # Reject a proposal
    python -m able.core.evolution --status            # Evolution system status
    python -m able.core.evolution --daemon --once     # Run one evolution cycle
    python -m able.core.evolution --daemon --dry-run  # Analyze only, no deploy
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from able.core.evolution.self_scheduler import SelfScheduler
from able.core.evolution.daemon import EvolutionDaemon, EvolutionConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="able.core.evolution",
        description="ABLE evolution daemon and self-scheduler CLI.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--pending",
        action="store_true",
        help="List pending self-scheduled proposals awaiting review.",
    )
    group.add_argument(
        "--promote",
        metavar="ACTION_ID",
        help="Promote a dry-run proposal to active.",
    )
    group.add_argument(
        "--reject",
        metavar="ACTION_ID",
        help="Reject a pending proposal.",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Show evolution system status.",
    )
    group.add_argument(
        "--daemon",
        action="store_true",
        help="Run the evolution daemon.",
    )
    # Daemon options
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle then exit (with --daemon).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze only — don't deploy changes (with --daemon).",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    sched = SelfScheduler()

    if args.pending:
        pending = sched.get_pending_actions()
        if not pending:
            print("No pending proposals.")
            return
        for action in pending:
            print(f"  {action['id']:12s}  {action['type']:15s}  {action['name']}")
            if action.get("description"):
                print(f"{'':14s}  {action['description']}")
        print(f"\n{len(pending)} proposal(s) pending review.")
        print("Use --promote <ID> to approve or --reject <ID> to decline.")

    elif args.promote:
        if sched.promote_action(args.promote):
            print(f"Promoted: {args.promote}")
        else:
            print(f"Action not found: {args.promote}", file=sys.stderr)
            sys.exit(1)

    elif args.reject:
        if sched.reject_action(args.reject):
            print(f"Rejected: {args.reject}")
        else:
            print(f"Action not found: {args.reject}", file=sys.stderr)
            sys.exit(1)

    elif args.status:
        status = sched.status
        print(json.dumps(status, indent=2, default=str))

    elif args.daemon:
        config = EvolutionConfig()
        if args.dry_run:
            config.auto_deploy = False
        daemon = EvolutionDaemon(config=config)

        if args.once:
            result = asyncio.run(daemon.run_cycle())
            output = {
                "cycle_id": result.cycle_id,
                "success": result.success,
                "problems_found": result.problems_found,
                "improvements_proposed": result.improvements_proposed,
                "improvements_deployed": result.improvements_deployed,
                "duration_ms": result.duration_ms,
            }
            print(json.dumps(output, indent=2, default=str))
            sys.exit(0 if result.success else 1)
        else:
            print("Starting evolution daemon (continuous mode, Ctrl+C to stop)...")
            asyncio.run(daemon.run_continuous())


if __name__ == "__main__":
    main()
