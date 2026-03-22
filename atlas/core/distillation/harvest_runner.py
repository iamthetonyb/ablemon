"""
Orchestrates all harvesters with priority ordering and feeds results
into the corpus builder.

Priority: Claude Code > ABLE interactions > GPT/Codex > Antigravity > Inbox > Others

Designed to run nightly via cron, but can be invoked directly:
    python -m atlas.core.distillation.harvest_runner [--since 24] [--tenant tony] [--dry-run]
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Source priority (lower = higher priority, harvested first) ────────
SOURCE_PRIORITY: dict[str, int] = {
    "claude_code": 1,      # Claude Max subscription — richest reasoning traces
    "able_interaction": 2,  # ABLE's own high-quality responses
    "codex": 3,             # OpenAI Codex CLI (bundled w/ GPT sub, clean transcripts)
    "chatgpt": 4,           # ChatGPT web (GPT sub, good reasoning)
    "antigravity": 5,       # Antigravity Pro sessions
    "cowork": 6,            # Claude Cowork mobile sessions
    "grok": 7,              # Grok free tier (thinner reasoning)
    "inbox": 8,             # Manually saved conversations
}


@dataclass
class HarvestResult:
    """Summary of a single harvest run."""
    source: str
    conversations: int
    deduplicated: int
    formatted: int
    duration_ms: float
    error: Optional[str] = None


@dataclass
class FullHarvestResult:
    """Summary of a complete harvest cycle."""
    started_at: str = ""
    completed_at: str = ""
    duration_ms: float = 0
    total_conversations: int = 0
    total_formatted: int = 0
    total_deduplicated: int = 0
    corpus_version: Optional[str] = None
    corpus_tier: Optional[str] = None
    corpus_total: int = 0
    sources: list[HarvestResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _get_harvesters(project_root: Path) -> list:
    """Build harvester list in priority order."""
    from atlas.core.distillation.harvesters.claude_code_harvester import ClaudeCodeHarvester
    from atlas.core.distillation.harvesters.able_interaction_harvester import ABLEInteractionHarvester
    from atlas.core.distillation.harvesters.inbox_harvester import InboxHarvester

    harvesters = []

    # Priority 1: Claude Code sessions
    harvesters.append(("claude_code", ClaudeCodeHarvester()))

    # Priority 2: ABLE's own interaction log
    db_path = project_root / "data" / "interaction_log.db"
    if db_path.exists():
        harvesters.append(("able_interaction", ABLEInteractionHarvester(db_path=str(db_path))))

    # Priority 3-7: OpenCLI adapters (Codex, ChatGPT, Antigravity, Cowork, Grok)
    try:
        from atlas.core.distillation.harvesters.opencli_harvester import OpenCLIHarvester
        adapters_dir = project_root / "atlas" / "core" / "distillation" / "harvesters" / "opencli_adapters"
        if adapters_dir.exists():
            opencli = OpenCLIHarvester(adapters_dir=str(adapters_dir))
            harvesters.append(("opencli", opencli))
    except Exception as e:
        logger.warning("OpenCLI harvester unavailable: %s", e)

    # Priority 8: Inbox (manually saved conversations)
    inbox_dir = Path.home() / "atlas-corpus-inbox"
    inbox_dir.mkdir(exist_ok=True)
    harvesters.append(("inbox", InboxHarvester(inbox_dir=inbox_dir)))

    return harvesters


def _deduplicate_conversations(conversations: list) -> list:
    """Cross-source deduplication by content hash."""
    seen: set[str] = set()
    unique = []
    for convo in conversations:
        h = getattr(convo, "content_hash", None)
        if not h:
            # Fallback: hash the messages
            msg_str = str(getattr(convo, "messages", []))
            h = hashlib.sha256(msg_str.encode()).hexdigest()[:16]
        if h not in seen:
            seen.add(h)
            unique.append(convo)
    return unique


def _sort_by_priority(conversations: list) -> list:
    """Sort harvested conversations by source priority."""
    def priority_key(convo):
        source = getattr(convo, "source", "unknown")
        return SOURCE_PRIORITY.get(source, 99)
    return sorted(conversations, key=priority_key)


async def run_harvest(
    since_hours: int = 24,
    tenant_id: str = "default",
    dry_run: bool = False,
    project_root: Optional[Path] = None,
) -> FullHarvestResult:
    """
    Run all harvesters, deduplicate, format, and optionally build corpus.

    Args:
        since_hours: How far back to look (default 24h)
        tenant_id: Tenant to build corpus for
        dry_run: If True, harvest and report but don't build corpus
        project_root: Project root path (auto-detected if None)
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent.parent

    result = FullHarvestResult(
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    start = time.monotonic()
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    # ── 1. Harvest from all sources ──────────────────────────────
    all_conversations = []
    harvesters = _get_harvesters(project_root)

    for source_name, harvester in harvesters:
        h_start = time.monotonic()
        try:
            convos = harvester.harvest(since=since)
            h_ms = (time.monotonic() - h_start) * 1000
            hr = HarvestResult(
                source=source_name,
                conversations=len(convos),
                deduplicated=0,  # filled after global dedup
                formatted=0,     # filled after formatting
                duration_ms=h_ms,
            )
            result.sources.append(hr)
            all_conversations.extend(convos)
            if convos:
                logger.info("[harvest] %s: %d conversations (%.0fms)",
                            source_name, len(convos), h_ms)
        except Exception as e:
            h_ms = (time.monotonic() - h_start) * 1000
            logger.warning("[harvest] %s failed: %s", source_name, e)
            result.sources.append(HarvestResult(
                source=source_name, conversations=0, deduplicated=0,
                formatted=0, duration_ms=h_ms, error=str(e),
            ))
            result.errors.append(f"{source_name}: {e}")

    result.total_conversations = len(all_conversations)
    logger.info("[harvest] Total raw conversations: %d", len(all_conversations))

    if not all_conversations:
        result.duration_ms = (time.monotonic() - start) * 1000
        result.completed_at = datetime.now(timezone.utc).isoformat()
        return result

    # ── 2. Deduplicate across sources ────────────────────────────
    unique = _deduplicate_conversations(all_conversations)
    unique = _sort_by_priority(unique)
    result.total_deduplicated = len(all_conversations) - len(unique)
    logger.info("[harvest] After dedup: %d (removed %d duplicates)",
                len(unique), result.total_deduplicated)

    # ── 3. Format into training pairs ────────────────────────────
    from atlas.core.distillation.formatter import TrainingFormatter
    formatter = TrainingFormatter()
    formatted = formatter.format_batch(unique)
    formatted = formatter.deduplicate(formatted)
    result.total_formatted = len(formatted)
    logger.info("[harvest] Formatted training pairs: %d", len(formatted))

    if dry_run:
        logger.info("[harvest] Dry run — skipping corpus build")
        result.duration_ms = (time.monotonic() - start) * 1000
        result.completed_at = datetime.now(timezone.utc).isoformat()
        return result

    # ── 4. Build corpus incrementally ────────────────────────────
    if formatted:
        try:
            from atlas.core.distillation.corpus_builder import CorpusBuilder
            builder = CorpusBuilder()
            build = await asyncio.to_thread(builder.build_nightly, formatted, tenant_id)
            result.corpus_version = build.version
            result.corpus_tier = build.tier
            result.corpus_total = build.total
            logger.info("[harvest] Corpus %s: %d pairs, tier=%s",
                        build.version, build.total, build.tier)
        except Exception as e:
            logger.error("[harvest] Corpus build failed: %s", e)
            result.errors.append(f"corpus_build: {e}")

    # ── 5. Store to distillation DB ──────────────────────────────
    try:
        import uuid as _uuid
        from atlas.core.distillation.store import DistillationStore
        from atlas.core.distillation.models import DistillationPair
        store = DistillationStore()
        saved = 0
        for pair in formatted:
            convos = pair.get("conversations", [])
            prompt = convos[-2].get("content", "") if len(convos) >= 2 else ""
            response = convos[-1].get("content", "") if convos else ""
            meta = pair.get("metadata", {})
            dp = DistillationPair(
                id=str(_uuid.uuid4()),
                prompt=prompt,
                gold_response=response,
                gold_model=meta.get("teacher_model", "unknown"),
                gold_thinking=meta.get("thinking", None),
                domain=meta.get("domain", "general"),
                quality_score=meta.get("quality_score", 0.8),
                tenant_id=tenant_id,
                tags=[meta.get("source", "unknown")],
            )
            if store.save_pair(dp):
                saved += 1
        logger.info("[harvest] Stored %d/%d pairs to distillation DB", saved, len(formatted))
    except Exception as e:
        logger.warning("[harvest] DB store failed (non-fatal): %s", e)

    result.duration_ms = (time.monotonic() - start) * 1000
    result.completed_at = datetime.now(timezone.utc).isoformat()
    return result


# ── CLI ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="ATLAS Harvest Runner")
    parser.add_argument("--since", type=int, default=24, help="Hours to look back (default: 24)")
    parser.add_argument("--tenant", type=str, default="default", help="Tenant ID")
    parser.add_argument("--dry-run", action="store_true", help="Harvest only, don't build corpus")
    args = parser.parse_args()

    r = asyncio.run(run_harvest(
        since_hours=args.since,
        tenant_id=args.tenant,
        dry_run=args.dry_run,
    ))

    print(f"\n{'='*50}")
    print(f"HARVEST COMPLETE")
    print(f"{'='*50}")
    print(f"Duration: {r.duration_ms:.0f}ms")
    print(f"Conversations: {r.total_conversations}")
    print(f"After dedup: {r.total_conversations - r.total_deduplicated}")
    print(f"Training pairs: {r.total_formatted}")
    if r.corpus_version:
        print(f"Corpus: {r.corpus_version} ({r.corpus_total} total, tier={r.corpus_tier})")
    print(f"\nSources:")
    for s in r.sources:
        status = f"{s.conversations} convos" if not s.error else f"ERROR: {s.error}"
        print(f"  {s.source}: {status} ({s.duration_ms:.0f}ms)")
    if r.errors:
        print(f"\nErrors: {len(r.errors)}")
        for e in r.errors:
            print(f"  - {e}")
    sys.exit(0 if not r.errors else 1)
