#!/usr/bin/env python3
"""
Promptfoo Results Collector → AGI Feedback Loop

Reads promptfoo evaluation results from SQLite DB and:
1. Feeds pass/fail data to SelfImprovementEngine (record_win/record_failure)
2. Captures T4 (Sonnet 4.6) outputs as distillation targets
3. Exports quality comparison data for the evolution daemon
4. Identifies routing mismatches (T1 fails where T4 succeeds = under-routing)

Usage:
    cd atlas/evals && python collect_results.py
    python collect_results.py --dry-run
    python collect_results.py --last 3          # Process last 3 ATLAS evals
    python collect_results.py --eval-id eval-xxx  # Process specific eval
"""

import asyncio
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".promptfoo" / "promptfoo.db"


def get_atlas_evals(db_path: Path = DB_PATH, last_n: int = 3, eval_id: str = None) -> List[Dict]:
    """Get the most recent ATLAS evaluation results from SQLite."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if eval_id:
        evals = conn.execute(
            "SELECT id, description, created_at FROM evals WHERE id = ?", (eval_id,)
        ).fetchall()
    else:
        evals = conn.execute(
            "SELECT id, description, created_at FROM evals "
            "WHERE description LIKE 'ATLAS%' "
            "ORDER BY created_at DESC LIMIT ?", (last_n,)
        ).fetchall()

    results = []
    for ev in evals:
        rows = conn.execute("""
            SELECT
                test_idx,
                json_extract(test_case, '$.description') as test_desc,
                json_extract(provider, '$.label') as provider_label,
                success,
                score,
                latency_ms,
                cost,
                response,
                json_extract(grading_result, '$.reason') as grading_reason,
                json_extract(test_case, '$.vars') as vars_json
            FROM eval_results
            WHERE eval_id = ?
            ORDER BY test_idx, provider_label
        """, (ev["id"],)).fetchall()

        results.append({
            "eval_id": ev["id"],
            "description": ev["description"],
            "created_at": ev["created_at"],
            "results": [dict(r) for r in rows],
        })

    conn.close()
    return results


def parse_eval(eval_data: Dict) -> Dict[str, Any]:
    """Parse a single eval into structured data."""
    results = eval_data["results"]

    parsed = {
        "eval_id": eval_data["eval_id"],
        "description": eval_data["description"],
        "timestamp": eval_data["created_at"],
        "total_tests": len(results),
        "total_pass": sum(1 for r in results if r["success"]),
        "total_fail": sum(1 for r in results if not r["success"]),
        "by_provider": {},
        "by_test": {},
        "distillation_pairs": [],
        "routing_mismatches": [],
    }

    # Group by provider
    for r in results:
        label = r["provider_label"] or "unknown"
        if label not in parsed["by_provider"]:
            parsed["by_provider"][label] = {
                "total": 0, "pass": 0, "fail": 0,
                "total_latency": 0, "total_cost": 0.0, "outputs": [],
            }
        prov = parsed["by_provider"][label]
        prov["total"] += 1
        if r["success"]:
            prov["pass"] += 1
        else:
            prov["fail"] += 1
        prov["total_latency"] += r["latency_ms"] or 0
        prov["total_cost"] += r["cost"] or 0.0

        # Parse response to get output text
        output = ""
        try:
            resp = json.loads(r["response"]) if r["response"] else {}
            output = resp.get("output", "")[:2000]
        except (json.JSONDecodeError, TypeError):
            output = str(r["response"])[:2000] if r["response"] else ""

        prov["outputs"].append({
            "test": r["test_desc"],
            "vars": r["vars_json"],
            "output": output,
            "pass": bool(r["success"]),
            "latency_ms": r["latency_ms"],
            "reason": r["grading_reason"] if not r["success"] else None,
        })

    # Calculate averages
    for prov_data in parsed["by_provider"].values():
        if prov_data["total"] > 0:
            prov_data["avg_latency_ms"] = round(prov_data["total_latency"] / prov_data["total"])
            prov_data["pass_rate"] = round(prov_data["pass"] / prov_data["total"], 3)
            prov_data["avg_cost"] = round(prov_data["total_cost"] / prov_data["total"], 6)

    # Group by test for cross-tier comparison
    for r in results:
        test = r["test_desc"]
        if test not in parsed["by_test"]:
            parsed["by_test"][test] = {}
        parsed["by_test"][test][r["provider_label"]] = {
            "pass": bool(r["success"]),
            "reason": r["grading_reason"] if not r["success"] else None,
        }

    # Build distillation pairs
    t4_key = next((k for k in parsed["by_provider"] if "T4" in k or "Sonnet" in k), None)
    t1_key = next((k for k in parsed["by_provider"] if "T1" in k or "Nemotron" in k), None)

    if t4_key and t1_key:
        t4_outputs = {o["test"]: o for o in parsed["by_provider"][t4_key]["outputs"]}
        t1_outputs = {o["test"]: o for o in parsed["by_provider"][t1_key]["outputs"]}

        for test, t4_out in t4_outputs.items():
            if test in t1_outputs:
                t1_out = t1_outputs[test]
                quality_gap = t4_out["pass"] and not t1_out["pass"]
                parsed["distillation_pairs"].append({
                    "test": test,
                    "vars": t4_out["vars"],
                    "t4_output": t4_out["output"],
                    "t4_pass": t4_out["pass"],
                    "t1_output": t1_out["output"],
                    "t1_pass": t1_out["pass"],
                    "quality_gap": quality_gap,
                })
                if quality_gap:
                    parsed["routing_mismatches"].append({
                        "test": test,
                        "issue": "T1 failed where T4 succeeded — under-routed",
                    })
                elif not t4_out["pass"] and t1_out["pass"]:
                    parsed["routing_mismatches"].append({
                        "test": test,
                        "issue": "T4 failed where T1 succeeded — wasted spend",
                    })

    return parsed


async def feed_to_agi(all_parsed: List[Dict], dry_run: bool = False):
    """Feed parsed results to SelfImprovementEngine."""
    if dry_run:
        print("[DRY RUN] Would feed results to AGI — skipping")
        return

    try:
        from core.agi.self_improvement import SelfImprovementEngine
    except ImportError:
        logger.warning("SelfImprovementEngine not importable — writing to data/ instead")
        out_dir = Path("../data")
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "eval_feedback.json", "w") as f:
            json.dump(all_parsed, f, indent=2, default=str)
        print(f"  Wrote feedback to {out_dir / 'eval_feedback.json'}")
        return

    engine = SelfImprovementEngine()

    for parsed in all_parsed:
        total = parsed["total_tests"]
        if total == 0:
            continue
        pass_rate = parsed["total_pass"] / total

        if pass_rate >= 0.85:
            await engine.record_win(
                description=f"Promptfoo [{parsed['description']}]: {parsed['total_pass']}/{total} ({pass_rate:.0%})",
                what_worked="Skill quality meets threshold",
                metrics={
                    "pass_rate": round(pass_rate, 3),
                    "by_provider": {
                        k: {"pass_rate": v["pass_rate"], "avg_latency_ms": v["avg_latency_ms"]}
                        for k, v in parsed["by_provider"].items()
                    },
                },
            )
        else:
            worst = min(parsed["by_provider"].items(), key=lambda x: x[1].get("pass_rate", 0))
            await engine.record_failure(
                description=f"Promptfoo [{parsed['description']}]: {pass_rate:.0%} (target: 85%+)",
                what_failed=f"Worst: {worst[0]} at {worst[1].get('pass_rate', 0):.0%}",
                root_cause=f"{len(parsed['routing_mismatches'])} routing mismatches",
                prevention="Tune skill prompts or scorer weights",
            )

        print(f"  [AGI] {'WIN' if pass_rate >= 0.85 else 'FAILURE'}: "
              f"{parsed['description']} — {pass_rate:.0%}")


def export_distillation_data(all_parsed: List[Dict], output_dir: str = "../data"):
    """Export T4 outputs as distillation training targets."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_pairs = []
    for parsed in all_parsed:
        all_pairs.extend(parsed.get("distillation_pairs", []))

    if not all_pairs:
        print("  No distillation pairs found")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"distillation_{timestamp}.jsonl"

    quality_gaps = 0
    with open(path, "w") as f:
        for pair in all_pairs:
            f.write(json.dumps({
                "test": pair["test"],
                "prompt_vars": pair["vars"],
                "gold_output": pair["t4_output"],
                "t1_output": pair["t1_output"],
                "quality_gap": pair["quality_gap"],
            }, default=str) + "\n")
            if pair["quality_gap"]:
                quality_gaps += 1

    print(f"  Distillation: {len(all_pairs)} pairs, {quality_gaps} quality gaps → {path}")
    return path


def print_summary(all_parsed: List[Dict]):
    """Print human-readable summary across all evals."""
    print("=" * 65)
    print("PROMPTFOO RESULTS SUMMARY")
    print("=" * 65)

    grand_total = sum(p["total_tests"] for p in all_parsed)
    grand_pass = sum(p["total_pass"] for p in all_parsed)

    for parsed in all_parsed:
        total = parsed["total_tests"]
        if total == 0:
            continue
        rate = parsed["total_pass"] / total
        print(f"\n  {parsed['description']}")
        print(f"  {'─' * 55}")
        for label, data in parsed["by_provider"].items():
            pct = data.get('pass_rate', 0) * 100
            bar = '█' * int(pct / 5) + '░' * (20 - int(pct / 5))
            print(f"    {label:25s} {data['pass']}/{data['total']} "
                  f"({pct:5.1f}%) {bar}  {data.get('avg_latency_ms', 0)}ms")

    print(f"\n  {'═' * 55}")
    print(f"  GRAND TOTAL: {grand_pass}/{grand_total} "
          f"({grand_pass/grand_total*100:.1f}%)" if grand_total else "  No results")

    # Distillation summary
    all_pairs = []
    all_mismatches = []
    for p in all_parsed:
        all_pairs.extend(p.get("distillation_pairs", []))
        all_mismatches.extend(p.get("routing_mismatches", []))

    if all_pairs:
        gaps = sum(1 for p in all_pairs if p["quality_gap"])
        print(f"\n  Distillation pairs: {len(all_pairs)} total, {gaps} quality gaps")

    if all_mismatches:
        under = sum(1 for m in all_mismatches if "under" in m["issue"])
        over = sum(1 for m in all_mismatches if "wasted" in m["issue"])
        print(f"  Routing: {under} under-routed, {over} over-routed")

    print("=" * 65)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Collect promptfoo results → AGI feedback")
    parser.add_argument("--last", type=int, default=3, help="Process last N ATLAS evals")
    parser.add_argument("--eval-id", help="Process specific eval ID")
    parser.add_argument("--dry-run", action="store_true", help="Print summary only")
    parser.add_argument("--no-distillation", action="store_true", help="Skip distillation export")
    parser.add_argument("--auto-improve", action="store_true", help="Run auto-improvement from results")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"promptfoo DB not found at {DB_PATH}")
        print("Run evals first: cd atlas/evals && ./run-evals.sh")
        sys.exit(1)

    # Get evals
    evals = get_atlas_evals(last_n=args.last, eval_id=args.eval_id)
    if not evals:
        print("No ATLAS evals found in DB")
        sys.exit(1)

    print(f"Processing {len(evals)} eval(s) from {DB_PATH}")

    # Parse all
    all_parsed = [parse_eval(e) for e in evals]

    # Summary
    print_summary(all_parsed)

    # Feed to AGI
    asyncio.run(feed_to_agi(all_parsed, dry_run=args.dry_run))

    # Export distillation data
    if not args.no_distillation:
        export_distillation_data(all_parsed)

    # Auto-improvement from eval failures
    if args.auto_improve:
        try:
            from core.evolution.auto_improve import AutoImprover
            improver = AutoImprover(auto_apply=not args.dry_run)
            report = asyncio.run(improver.run(all_parsed))
            print(f"\n  [AUTO-IMPROVE] {report.actions_proposed} proposed, "
                  f"{report.actions_applied} applied")
            for insight in report.insights[:5]:
                print(f"    {insight}")
        except Exception as e:
            print(f"\n  [AUTO-IMPROVE] Skipped: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
