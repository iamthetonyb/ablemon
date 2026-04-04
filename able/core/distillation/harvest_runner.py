"""
Orchestrates all harvesters with priority ordering and feeds results
into the corpus builder.

Priority: Claude Code > ABLE interactions > GPT/Codex > Antigravity > Inbox > Others

Designed to run nightly via cron, but can be invoked directly:
    python -m able.core.distillation.harvest_runner [--since 24] [--tenant tony] [--dry-run]
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Source priority (lower = higher priority, harvested first) ────────
SOURCE_PRIORITY: dict[str, int] = {
    "claude_code": 1,      # Claude Max subscription — richest reasoning traces
    "cowork": 1,            # Claude Cowork sessions — same quality as Claude Code
    "able_interaction": 2,  # ABLE's own high-quality responses
    "able_cli": 2,          # ABLE CLI sessions (same quality as able_interaction)
    "0wav_ml": 3,           # 0wav labeled behavioral profiles (domain-specific)
    "0wav_claude_code": 3,  # 0wav Claude Code sessions (domain-specific)
    "gstack": 3,            # gstack sprint learnings (code review, QA, security insights)
    "codex": 4,             # OpenAI Codex CLI (bundled w/ GPT sub, clean transcripts)
    "chatgpt": 5,           # ChatGPT web (GPT sub, good reasoning)
    "antigravity": 6,       # Antigravity Pro sessions
    "external_tool": 7,     # Third-party AI tools (user-configured drop dir)
    "grok": 7,              # Grok free tier (thinner reasoning)
    "inbox": 8,             # Manually saved conversations
}


class _CoworkHarvester:
    """Thin wrapper that points ClaudeCodeHarvester at the Cowork sessions dir.

    Cowork uses the same JSONL format as Claude Code but stores sessions in
    ``~/Library/Application Support/Claude/local-agent-mode-sessions/``.
    """

    source_name = "cowork"

    def __init__(self, cowork_dir: Path):
        self._dir = cowork_dir

    def harvest(self, since: datetime | None = None, **kwargs) -> list:
        from able.core.distillation.harvesters.claude_code_harvester import ClaudeCodeHarvester
        h = ClaudeCodeHarvester()
        convos = h.harvest(source_path=str(self._dir), since=since)
        # Re-tag source as cowork
        for c in convos:
            c.source = "cowork"
            c.metadata["original_source"] = "claude_cowork"
        return convos


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


def _get_harvesters(project_root: Path, tenant_id: str = "default") -> list:
    """Build harvester list in priority order.

    When ``tenant_id`` is a known tenant with a dedicated harvester
    (e.g. ``0wav``), that harvester is included automatically.
    """
    from able.core.distillation.harvesters.claude_code_harvester import ClaudeCodeHarvester
    from able.core.distillation.harvesters.able_interaction_harvester import ABLEInteractionHarvester
    from able.core.distillation.harvesters.cli_session_harvester import CLISessionHarvester
    from able.core.distillation.harvesters.inbox_harvester import InboxHarvester

    harvesters = []

    # Priority 1: Claude Code sessions (CLI)
    harvesters.append(("claude_code", ClaudeCodeHarvester()))

    # Priority 1b: Claude Cowork sessions (same JSONL format, different dir)
    cowork_dir = Path.home() / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"
    if cowork_dir.exists():
        harvesters.append(("cowork", _CoworkHarvester(cowork_dir)))

    # Priority 2: ABLE's own interaction log
    db_path = project_root / "data" / "interaction_log.db"
    if db_path.exists():
        harvesters.append(("able_interaction", ABLEInteractionHarvester(db_path=str(db_path))))

    # Priority 2b: ABLE CLI sessions (~/.able/sessions/*.jsonl)
    harvesters.append(("able_cli", CLISessionHarvester()))

    # Priority 3: 0wav ML harvester (labeled profiles + 0wav Claude sessions)
    if tenant_id == "0wav":
        try:
            from able.core.distillation.harvesters.owav_ml_harvester import OwavMLHarvester
            harvesters.append(("0wav_ml", OwavMLHarvester()))
        except Exception as e:
            logger.warning("0wav ML harvester unavailable: %s", e)

    # Priority 3: gstack sprint learnings (~/.gstack/projects/*/learnings.jsonl)
    # Opt-in only — gstack data may contain learnings from private/client repos
    # that should not cross-contaminate the default corpus or leak via federation.
    # Set ABLE_GSTACK_HARVEST=1 to enable.
    import os as _os
    if _os.environ.get("ABLE_GSTACK_HARVEST", "").lower() in ("1", "true", "yes"):
        try:
            from able.core.distillation.harvesters.gstack_harvester import GstackHarvester
            gstack_home = Path.home() / ".gstack"
            if gstack_home.exists():
                harvesters.append(("gstack", GstackHarvester(gstack_home=gstack_home)))
        except Exception as e:
            logger.warning("gstack harvester unavailable: %s", e)
    else:
        logger.debug("gstack harvester disabled (set ABLE_GSTACK_HARVEST=1 to enable)")

    # Priority 4-5: OpenCLI adapters (Codex, ChatGPT, Cowork, Grok)
    try:
        from able.core.distillation.harvesters.opencli_harvester import OpenCLIHarvester
        adapters_dir = project_root / "able" / "core" / "distillation" / "harvesters" / "opencli_adapters"
        if adapters_dir.exists():
            opencli = OpenCLIHarvester(adapters_dir=str(adapters_dir))
            harvesters.append(("opencli", opencli))
    except Exception as e:
        logger.warning("OpenCLI harvester unavailable: %s", e)

    # Priority 6: Antigravity brain artifacts (readable markdown plans/walkthroughs)
    try:
        from able.core.distillation.harvesters.antigravity_harvester import AntigravityHarvester
        harvesters.append(("antigravity", AntigravityHarvester()))
    except Exception as e:
        logger.warning("Antigravity harvester unavailable: %s", e)

    # Priority 7: External tool sessions (~/.able/external_sessions/*.jsonl)
    # Users drop JSONL from any AI tool here — Cursor, Windsurf, Copilot, etc.
    try:
        from able.core.distillation.harvesters.external_tool_harvester import ExternalToolHarvester
        harvesters.append(("external_tool", ExternalToolHarvester()))
    except Exception as e:
        logger.warning("External tool harvester unavailable: %s", e)

    # Priority 9: Inbox (manually saved conversations)
    inbox_dir = Path.home() / "able-corpus-inbox"
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
    harvesters = _get_harvesters(project_root, tenant_id=tenant_id)

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

    # ── 3. Normalize into canonical training pairs ───────────────
    from able.core.distillation.formatter import TrainingFormatter
    from able.core.distillation.models import ConversationRecord, ThinkingTrace
    from able.core.distillation.store import DistillationStore

    formatter = TrainingFormatter()
    raw_training_pairs = formatter.normalize_batch(unique)
    training_pairs = formatter.deduplicate_pairs(raw_training_pairs)
    result.total_formatted = len(training_pairs)
    logger.info("[harvest] Normalized training pairs: %d", len(training_pairs))

    # ── 3b. Retroactively scrub existing corpus (idempotent) ──────
    from able.core.distillation.store import DistillationStore
    store = DistillationStore()
    try:
        scrub = store.scrub_corpus()
        if scrub["scrubbed"] or scrub["deleted"]:
            logger.info(
                "[harvest] Corpus scrub: %d pairs cleaned, %d empty pairs removed",
                scrub["scrubbed"], scrub["deleted"],
            )
    except Exception as e:
        logger.warning("[harvest] Corpus scrub failed (non-fatal): %s", e)

    # ── 4. Persist harvested conversations + canonical pairs ─────
    saved_records = 0
    saved_pairs = 0

    unique_by_hash = {convo.content_hash: convo for convo in unique}
    for pair in training_pairs:
        convo = unique_by_hash.get(pair.content_hash)
        if convo is None:
            continue
        trace = None
        if pair.thinking:
            trace = ThinkingTrace(
                model=pair.teacher_model,
                raw_thinking=pair.thinking,
                stripped_output=pair.response,
                extraction_method="harvested",
            )

        record = ConversationRecord(
            id=convo.id or str(uuid.uuid4()),
            source=convo.source,
            messages=pair.messages or convo.messages,
            model=convo.model,
            tier=int(convo.metadata.get("tier", SOURCE_PRIORITY.get(convo.source, 9))),
            domain=pair.domain,
            quality_score=pair.quality_score,
            thinking_trace=trace,
            tenant_id=pair.tenant_id,
            timestamp=convo.timestamp,
            metadata=dict(convo.metadata),
            content_hash=convo.content_hash,
        )
        if store.save_record(record):
            saved_records += 1
        if store.save_pair(pair.to_distillation_pair()):
            saved_pairs += 1

    logger.info(
        "[harvest] Stored %d conversation records and %d canonical pairs",
        saved_records,
        saved_pairs,
    )

    if dry_run:
        logger.info("[harvest] Dry run — skipping corpus build")
        result.duration_ms = (time.monotonic() - start) * 1000
        result.completed_at = datetime.now(timezone.utc).isoformat()
        return result

    # ── 5. Build corpus incrementally ────────────────────────────
    if training_pairs:
        try:
            from able.core.distillation.corpus_builder import CorpusBuilder
            builder = CorpusBuilder()
            build = await asyncio.to_thread(builder.build_nightly, training_pairs, tenant_id)
            result.corpus_version = build.version
            result.corpus_tier = build.tier
            result.corpus_total = build.total
            logger.info("[harvest] Corpus %s: %d pairs, tier=%s",
                        build.version, build.total, build.tier)
        except Exception as e:
            logger.error("[harvest] Corpus build failed: %s", e)
            result.errors.append(f"corpus_build: {e}")

    # ── 6. Reverse flow: promote best tenant pairs to ABLE core ─
    if tenant_id != "default" and not dry_run:
        try:
            from able.core.distillation.corpus_builder import CorpusBuilder
            builder = CorpusBuilder()
            promo = builder.promote_to_core(
                tenant_id=tenant_id,
                min_quality=0.90,
            )
            promoted_count = promo.get("promoted_from_tenant", 0)
            if promoted_count > 0:
                logger.info(
                    "[harvest] Promoted %d high-quality %s pairs → ABLE core "
                    "(domains: %s)",
                    promoted_count, tenant_id,
                    promo.get("promoted_domains", {}),
                )
        except Exception as e:
            logger.warning("[harvest] Reverse promotion failed (non-fatal): %s", e)

    result.duration_ms = (time.monotonic() - start) * 1000
    result.completed_at = datetime.now(timezone.utc).isoformat()
    return result


# ── CLI ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="ABLE Harvest Runner")
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
