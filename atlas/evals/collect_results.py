#!/usr/bin/env python3
"""
Promptfoo Results Collector → AGI Feedback Loop

Reads the latest promptfoo evaluation results and:
1. Feeds pass/fail data to SelfImprovementEngine (record_win/record_failure)
2. Captures T4 (Opus/Sonnet) outputs as distillation targets
3. Exports quality comparison data for the evolution daemon
4. Identifies routing mismatches (T1 beats T4 = over-routing, T4 >> T1 = under-routing)

Usage:
    # After running promptfoo eval:
    cd atlas/evals && python collect_results.py

    # With custom results path:
    python collect_results.py --results-dir .promptfoo/output

    # Dry run (don't feed to AGI, just print summary):
    python collect_results.py --dry-run
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure atlas is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


def find_latest_results(results_dir: str = None) -> Optional[Path]:
    """Find the most recent promptfoo results file."""
    search_dirs = [
        Path(results_dir) if results_dir else None,
        Path(".promptfoo/output"),
        Path("../.promptfoo/output"),
        Path.home() / ".promptfoo" / "output",
    ]

    for d in search_dirs:
        if d and d.exists():
            json_files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
            if json_files:
                return json_files[0]

    return None


def parse_results(results_path: Path) -> Dict[str, Any]:
    """Parse promptfoo results JSON into structured data."""
    with open(results_path) as f:
        raw = json.load(f)

    results = raw.get("results", [])
    if not results:
        # Try alternate structure
        results = raw.get("evalResults", [])

    parsed = {
        "timestamp": raw.get("createdAt", datetime.now().isoformat()),
        "total_tests": 0,
        "total_pass": 0,
        "total_fail": 0,
        "by_provider": {},
        "by_prompt": {},
        "distillation_pairs": [],  # T4 output + T1/T2 output for same prompt
        "routing_mismatches": [],  # Cases where wrong tier would have been better
    }

    for result in results:
        provider = result.get("provider", {})
        provider_label = provider.get("label", provider.get("id", "unknown"))
        prompt_label = result.get("prompt", {}).get("label", "unknown")

        # Initialize provider tracking
        if provider_label not in parsed["by_provider"]:
            parsed["by_provider"][provider_label] = {
                "total": 0, "pass": 0, "fail": 0,
                "avg_latency_ms": 0, "total_latency": 0,
                "total_tokens": 0, "outputs": [],
            }

        prov = parsed["by_provider"][provider_label]
        prov["total"] += 1
        parsed["total_tests"] += 1

        # Check assertions
        assertions = result.get("gradingResult", {}).get("componentResults", [])
        all_pass = all(a.get("pass", False) for a in assertions) if assertions else False

        if all_pass:
            prov["pass"] += 1
            parsed["total_pass"] += 1
        else:
            prov["fail"] += 1
            parsed["total_fail"] += 1

        # Capture output for distillation
        output = result.get("response", {}).get("output", "")
        latency = result.get("latency", 0)
        tokens = result.get("response", {}).get("tokenUsage", {})

        prov["total_latency"] += latency
        prov["total_tokens"] += tokens.get("total", 0)
        prov["outputs"].append({
            "prompt": result.get("vars", {}),
            "output": output[:2000],  # Cap for storage
            "pass": all_pass,
            "latency_ms": latency,
            "tokens": tokens,
        })

    # Calculate averages
    for prov_data in parsed["by_provider"].values():
        if prov_data["total"] > 0:
            prov_data["avg_latency_ms"] = round(prov_data["total_latency"] / prov_data["total"])
            prov_data["pass_rate"] = round(prov_data["pass"] / prov_data["total"], 3)

    # Build distillation pairs: match T4 output with T1/T2 output for same vars
    t4_outputs = {}
    t1_outputs = {}
    for label, prov_data in parsed["by_provider"].items():
        for out in prov_data["outputs"]:
            key = json.dumps(out["prompt"], sort_keys=True)
            if "T4" in label or "Opus" in label or "Sonnet" in label:
                t4_outputs[key] = {"provider": label, **out}
            elif "T1" in label or "Nemotron" in label:
                t1_outputs[key] = {"provider": label, **out}

    for key, t4_out in t4_outputs.items():
        if key in t1_outputs:
            t1_out = t1_outputs[key]
            parsed["distillation_pairs"].append({
                "prompt_vars": t4_out["prompt"],
                "t4_output": t4_out["output"],
                "t4_pass": t4_out["pass"],
                "t1_output": t1_out["output"],
                "t1_pass": t1_out["pass"],
                "quality_gap": t4_out["pass"] and not t1_out["pass"],
            })

    # Identify routing mismatches
    for pair in parsed["distillation_pairs"]:
        if pair["quality_gap"]:
            parsed["routing_mismatches"].append({
                "prompt": pair["prompt_vars"],
                "issue": "T1 failed where T4 succeeded — under-routed",
            })
        elif not pair["t4_pass"] and pair["t1_pass"]:
            parsed["routing_mismatches"].append({
                "prompt": pair["prompt_vars"],
                "issue": "T4 failed where T1 succeeded — wasted spend",
            })

    return parsed


async def feed_to_agi(parsed: Dict[str, Any], dry_run: bool = False):
    """Feed parsed results to SelfImprovementEngine."""
    if dry_run:
        print("[DRY RUN] Would feed results to AGI — skipping")
        return

    try:
        from core.agi.self_improvement import SelfImprovementEngine
    except ImportError:
        logger.warning("SelfImprovementEngine not importable")
        return

    engine = SelfImprovementEngine()
    total = parsed["total_tests"]
    pass_rate = parsed["total_pass"] / total if total else 0

    if pass_rate >= 0.85:
        await engine.record_win(
            description=f"Promptfoo eval: {parsed['total_pass']}/{total} pass ({pass_rate:.0%})",
            what_worked="Model output quality meets threshold across providers",
            metrics={
                "pass_rate": round(pass_rate, 3),
                "by_provider": {
                    k: {"pass_rate": v["pass_rate"], "avg_latency_ms": v["avg_latency_ms"]}
                    for k, v in parsed["by_provider"].items()
                },
                "distillation_pairs": len(parsed["distillation_pairs"]),
                "routing_mismatches": len(parsed["routing_mismatches"]),
            },
        )
        print(f"  [AGI] Recorded WIN: {pass_rate:.0%} pass rate")
    else:
        # Identify worst provider
        worst = min(parsed["by_provider"].items(), key=lambda x: x[1].get("pass_rate", 0))
        await engine.record_failure(
            description=f"Promptfoo eval: {pass_rate:.0%} pass rate (target: 85%+)",
            what_failed=f"Worst provider: {worst[0]} at {worst[1].get('pass_rate', 0):.0%}",
            root_cause=f"{len(parsed['routing_mismatches'])} routing mismatches detected",
            prevention="Review failed assertions, tune scorer weights, or improve skill protocols",
        )
        print(f"  [AGI] Recorded FAILURE: {pass_rate:.0%} pass rate")

    # Log routing mismatches specifically
    for mismatch in parsed["routing_mismatches"][:5]:
        await engine.record_failure(
            description=f"Routing mismatch: {mismatch['issue']}",
            what_failed=f"Prompt: {json.dumps(mismatch['prompt'])[:200]}",
            root_cause=mismatch["issue"],
            prevention="Evolution daemon should adjust tier thresholds",
        )


def export_distillation_data(parsed: Dict[str, Any], output_dir: str = "data"):
    """Export T4 outputs as distillation training targets."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pairs = parsed.get("distillation_pairs", [])
    if not pairs:
        print("  No distillation pairs found (need T4 + T1 results for same prompts)")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"distillation_{timestamp}.jsonl"

    with open(path, "w") as f:
        for pair in pairs:
            f.write(json.dumps({
                "prompt": pair["prompt_vars"],
                "gold_output": pair["t4_output"],  # T4 = training target
                "t1_output": pair["t1_output"],     # T1 = what we want to improve
                "quality_gap": pair["quality_gap"],
            }) + "\n")

    print(f"  Distillation data: {len(pairs)} pairs → {path}")
    return path


def print_summary(parsed: Dict[str, Any]):
    """Print human-readable summary."""
    total = parsed["total_tests"]
    if total == 0:
        print("No results found.")
        return

    print("=" * 60)
    print("PROMPTFOO RESULTS SUMMARY")
    print(f"Timestamp: {parsed['timestamp']}")
    print("=" * 60)
    print(f"\nOverall: {parsed['total_pass']}/{total} pass "
          f"({parsed['total_pass']/total:.0%})")

    print("\nBy Provider:")
    for label, data in parsed["by_provider"].items():
        print(f"  {label}:")
        print(f"    Pass rate: {data['pass']}/{data['total']} ({data.get('pass_rate', 0):.0%})")
        print(f"    Avg latency: {data.get('avg_latency_ms', 0)}ms")
        print(f"    Total tokens: {data.get('total_tokens', 0)}")

    pairs = parsed.get("distillation_pairs", [])
    if pairs:
        gaps = sum(1 for p in pairs if p["quality_gap"])
        print(f"\nDistillation: {len(pairs)} pairs, {gaps} quality gaps (T4 > T1)")

    mismatches = parsed.get("routing_mismatches", [])
    if mismatches:
        print(f"\nRouting Mismatches: {len(mismatches)}")
        for m in mismatches[:5]:
            print(f"  - {m['issue']}")

    print("=" * 60)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Collect promptfoo results → AGI feedback")
    parser.add_argument("--results-dir", help="Path to promptfoo results directory")
    parser.add_argument("--results-file", help="Path to specific results JSON")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without feeding to AGI")
    parser.add_argument("--export-distillation", action="store_true", default=True,
                        help="Export T4 outputs as distillation training data")
    args = parser.parse_args()

    # Find results
    if args.results_file:
        results_path = Path(args.results_file)
    else:
        results_path = find_latest_results(args.results_dir)

    if not results_path or not results_path.exists():
        print("No promptfoo results found. Run: cd atlas/evals && npx promptfoo@latest eval")
        sys.exit(1)

    print(f"Reading results from: {results_path}")

    # Parse
    parsed = parse_results(results_path)

    # Summary
    print_summary(parsed)

    # Feed to AGI
    asyncio.run(feed_to_agi(parsed, dry_run=args.dry_run))

    # Export distillation data
    if args.export_distillation:
        export_distillation_data(parsed)

    print("\nDone.")


if __name__ == "__main__":
    main()
