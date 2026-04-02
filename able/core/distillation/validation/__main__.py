"""CLI for the ABLE validation gate.

Usage:
    python -m able.core.distillation.validation --run --candidate able-student-27b-v1
    python -m able.core.distillation.validation --run --candidate able-student-27b-v1 --previous able-student-27b-v0
    python -m able.core.distillation.validation --status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from able.core.distillation.validation.validation_gate import (
    ValidationGate,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="able.core.distillation.validation",
        description="ABLE validation gate — 4-stage pipeline for student model deployment.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run",
        action="store_true",
        help="Run the full validation pipeline on a candidate model.",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Show validation gate configuration and last results.",
    )
    parser.add_argument(
        "--candidate",
        help="Candidate model identifier (e.g. 'able-student-27b-v1').",
    )
    parser.add_argument(
        "--previous",
        default=None,
        help="Previous model version for regression check (stage 4).",
    )
    parser.add_argument(
        "--teacher",
        default="opus-4.6",
        help="Teacher model for comparison (default: opus-4.6).",
    )
    parser.add_argument(
        "--test-data",
        default=None,
        help="Path to held-out test JSONL for stage 2 comparison.",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.8,
        help="Minimum pass rate for stages 1/2/4 (default: 0.8).",
    )
    parser.add_argument(
        "--security-min-rate",
        type=float,
        default=0.95,
        help="Minimum pass rate for stage 3 security (default: 0.95).",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    gate = ValidationGate(
        min_pass_rate=args.min_pass_rate,
        security_min_rate=args.security_min_rate,
    )

    if args.status:
        print(json.dumps({
            "eval_dir": str(gate.eval_dir),
            "min_pass_rate": gate.min_pass_rate,
            "security_min_rate": gate.security_min_rate,
            "stages": [
                "1: Promptfoo eval suite (tool use, skill adherence, reasoning)",
                "2: Teacher-student comparison on held-out data",
                "3: Security red-team (67+ attack plugins)",
                "4: Regression check vs previous student version",
            ],
        }, indent=2))
        return

    if args.run:
        if not args.candidate:
            parser.error("--run requires --candidate")

        result = asyncio.run(gate.run(
            candidate_model=args.candidate,
            test_data_path=args.test_data,
            previous_model=args.previous,
            teacher_model=args.teacher,
        ))

        output = {
            "decision": result.decision.value,
            "overall_pass_rate": result.overall_pass_rate,
            "model": result.model_name,
            "version": result.model_version,
            "timestamp": result.timestamp.isoformat(),
            "domain_breakdown": result.domain_breakdown,
            "recommendations": result.recommendations,
            "stages": [
                {
                    "stage": s.stage,
                    "name": s.name,
                    "passed": s.passed,
                    "pass_rate": s.pass_rate,
                    "errors": s.errors,
                }
                for s in result.stages
            ],
        }
        print(json.dumps(output, indent=2, default=str))
        sys.exit(0 if result.decision.value == "deploy" else 1)


if __name__ == "__main__":
    main()
