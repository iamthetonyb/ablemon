"""Corpus generator — drive training data collection from the prompt bank.

Can operate in two modes:
  - **interactive**: Prints prompts for the operator to answer via Claude Code
    session, auto-saves responses as training pairs.
  - **batch**: Sends prompts through the ATLAS provider chain automatically
    and stores resulting pairs.

Usage:
    python -m atlas.skills.library.corpus-generator.scripts.generate --domain coding --count 5
    python -m atlas.skills.library.corpus-generator.scripts.generate --status
    python -m atlas.skills.library.corpus-generator.scripts.generate --from-failures --count 10
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from atlas.core.distillation.prompt_bank import PromptBank
from atlas.core.distillation.store import DistillationStore
from atlas.core.distillation.models import DistillationPair

_OUTPUT_DIR = Path("data")


def _status() -> dict:
    """Show corpus stats: prompt bank size + distillation DB counts."""
    bank = PromptBank()
    store = DistillationStore()
    stats = store.stats()

    bank_counts = {}
    for domain in bank.all_domains():
        bank_counts[domain] = bank.count(domain=domain)

    return {
        "prompt_bank": {
            "total": bank.count(),
            "by_domain": bank_counts,
            "domains": bank.all_domains(),
        },
        "distillation_db": {
            "total_pairs": stats["total_pairs"],
            "by_domain": stats.get("by_domain", {}),
            "by_model": stats.get("by_model", {}),
            "corpus_tier": store.get_corpus_tier(),
        },
    }


def _generate_interactive(
    domain: str | None,
    difficulty: str | None,
    count: int,
    from_failures: bool,
) -> list[dict]:
    """Generate prompts for interactive corpus building.

    Prints each prompt so the operator can respond. The response is captured
    from stdin (pipe) or the operator pastes it. Each pair is saved to the
    distillation DB and appended to the JSONL output.
    """
    bank = PromptBank()

    if from_failures:
        # Load failure patterns from interaction log
        try:
            from atlas.core.routing.log_queries import LogQueries
            lq = LogQueries()
            failures = lq.get_failures_by_tier(tier=1, since_hours=168)
            failure_patterns = [
                {
                    "domain": f.get("message_category", "coding"),
                    "description": f.get("outcome_notes", f.get("error", "Describe a task that failed")),
                    "difficulty": "medium",
                    "tags": ["from_failure"],
                }
                for f in failures[:count]
            ]
            added = bank.add_from_failures(failure_patterns)
            print(f"Added {added} failure-derived prompts to bank")
        except Exception as e:
            print(f"Could not load failures (using existing bank): {e}", file=sys.stderr)

    prompts = bank.sample(domain=domain, difficulty=difficulty, n=count)
    if not prompts:
        print("No prompts available for the specified filters.")
        print(f"Available domains: {bank.all_domains()}")
        return []

    store = DistillationStore()
    output_path = _OUTPUT_DIR / f"distillation_corpus_gen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    print(f"\n{'='*60}")
    print(f"CORPUS GENERATION — {len(prompts)} prompts")
    print(f"Domain: {domain or 'all'} | Difficulty: {difficulty or 'all'}")
    print(f"Output: {output_path}")
    print(f"{'='*60}\n")

    for i, prompt_entry in enumerate(prompts, 1):
        print(f"--- Prompt {i}/{len(prompts)} [{prompt_entry.domain}/{prompt_entry.difficulty}] ---")
        print(f"\n{prompt_entry.prompt}\n")
        print("(Paste response below, end with a line containing only 'END')")

        response_lines = []
        try:
            while True:
                line = input()
                if line.strip() == "END":
                    break
                response_lines.append(line)
        except EOFError:
            break

        response = "\n".join(response_lines)
        if not response.strip():
            print("  [skipped — empty response]")
            continue

        # Build training pair
        pair = {
            "conversations": [
                {"role": "system", "content": "You are ATLAS, an autonomous AI agent. Be direct, accurate, and helpful."},
                {"role": "user", "content": prompt_entry.prompt},
                {"role": "assistant", "content": response},
            ],
            "metadata": {
                "source": "corpus_generator",
                "teacher_model": "interactive",
                "domain": prompt_entry.domain,
                "difficulty": prompt_entry.difficulty,
                "quality_score": 0.8,
                "tenant_id": "default",
            },
        }
        results.append(pair)

        # Save to distillation DB
        dp = DistillationPair(
            id=str(uuid.uuid4()),
            prompt=prompt_entry.prompt,
            gold_response=response,
            gold_model="interactive",
            gold_thinking=None,
            domain=prompt_entry.domain,
            quality_score=0.8,
            tenant_id="default",
            tags=["corpus_generator", prompt_entry.difficulty],
        )
        store.save_pair(dp)

        # Append to JSONL
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

        print(f"  [saved — {len(response)} chars]")

    print(f"\n{'='*60}")
    print(f"Generated {len(results)} training pairs")
    print(f"Saved to: {output_path}")
    print(f"DB total: {store.stats()['total_pairs']} pairs")
    print(f"{'='*60}")

    return results


def _generate_prompts_only(
    domain: str | None,
    difficulty: str | None,
    count: int,
) -> list[dict]:
    """Print prompts as JSON for piping into other tools."""
    bank = PromptBank()
    prompts = bank.sample(domain=domain, difficulty=difficulty, n=count)
    return [p.to_dict() for p in prompts]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="corpus-generator",
        description="Generate training data for ATLAS model distillation.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true", help="Interactive generation session")
    group.add_argument("--prompts", action="store_true", help="Print prompts as JSON (no interaction)")
    group.add_argument("--status", action="store_true", help="Show corpus statistics")

    parser.add_argument("--domain", default=None, help="Filter by domain")
    parser.add_argument("--difficulty", default=None, help="Filter by difficulty")
    parser.add_argument("--count", type=int, default=10, help="Number of prompts (default: 10)")
    parser.add_argument("--from-failures", action="store_true", help="Generate from failure patterns")

    args = parser.parse_args()

    if args.status:
        print(json.dumps(_status(), indent=2, default=str))

    elif args.prompts:
        prompts = _generate_prompts_only(args.domain, args.difficulty, args.count)
        print(json.dumps(prompts, indent=2, default=str))

    elif args.generate:
        _generate_interactive(args.domain, args.difficulty, args.count, args.from_failures)


if __name__ == "__main__":
    main()
