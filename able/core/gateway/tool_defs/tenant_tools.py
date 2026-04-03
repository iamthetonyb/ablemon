"""
Tenant management + distillation tool definitions and handlers.

Allows ABLE to onboard clients, check corpus status, trigger harvests,
and manage distillation pipelines via Telegram natural language or tool calls.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from able.core.gateway.tool_registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)


# ── Tool Definitions ──────────────────────────────────────────────────────────

TENANT_ONBOARD = {
    "type": "function",
    "function": {
        "name": "tenant_onboard",
        "description": (
            "Onboard a new client tenant with full distillation pipeline. "
            "Creates config, data directories, system prompt, and initial "
            "corpus. Use when Tony says 'set up a client', 'onboard tenant', "
            "'new client', or 'set up distillation for X'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tenant_id": {
                    "type": "string",
                    "description": "Short kebab-case ID (e.g. 'acme-legal', 'buildright-saas')",
                },
                "name": {
                    "type": "string",
                    "description": "Display name of the client/company",
                },
                "domain": {
                    "type": "string",
                    "description": "Primary domain (e.g. legal, medical, saas, ecommerce, audio-ml, real-estate)",
                },
                "personality": {
                    "type": "string",
                    "description": "Brief personality/tone description for the tenant's AI assistant",
                },
            },
            "required": ["tenant_id", "name", "domain"],
        },
    },
}

TENANT_LIST = {
    "type": "function",
    "function": {
        "name": "tenant_list",
        "description": "List all tenants and their status. Use when asked about clients, tenants, or 'who do we have'.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

TENANT_STATUS = {
    "type": "function",
    "function": {
        "name": "tenant_status",
        "description": (
            "Get detailed status for a TENANT (client distillation pipeline) including "
            "corpus stats, training readiness, and quality metrics. Use when asked "
            "about a tenant/client's distillation status (e.g. 'how is the 0wav tenant', "
            "'tenant status for acme'). "
            "Do NOT use this for buddy/companion queries — use buddy_status instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tenant_id": {
                    "type": "string",
                    "description": "Tenant ID to check",
                },
            },
            "required": ["tenant_id"],
        },
    },
}

DISTILLATION_STATUS = {
    "type": "function",
    "function": {
        "name": "distillation_status",
        "description": (
            "Get distillation pipeline status: corpus size, quality, "
            "training readiness, GPU budget. Use when asked about "
            "'corpus', 'training data', 'distillation', 'how much data'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tenant_id": {
                    "type": "string",
                    "description": "Tenant ID (default: 'default' for ABLE core)",
                },
            },
            "required": [],
        },
    },
}

DISTILLATION_HARVEST = {
    "type": "function",
    "function": {
        "name": "distillation_harvest",
        "description": (
            "Run the harvest pipeline to collect training data from all "
            "sources (Claude Code, Cowork, 0wav, Codex, etc). Use when "
            "asked to 'harvest', 'collect training data', or 'run distillation'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tenant_id": {
                    "type": "string",
                    "description": "Tenant ID (default: 'default')",
                },
                "since_hours": {
                    "type": "integer",
                    "description": "Hours to look back (default: 168 = 1 week)",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, harvest and report but don't build corpus",
                },
            },
            "required": [],
        },
    },
}

DISTILLATION_BUILD_CORPUS = {
    "type": "function",
    "function": {
        "name": "distillation_build_corpus",
        "description": (
            "Build or rebuild the training corpus for a tenant. For non-default "
            "tenants, enriches with 20% ABLE core reasoning data. Use when "
            "asked to 'build corpus', 'prepare training data', or 'rebuild dataset'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tenant_id": {
                    "type": "string",
                    "description": "Tenant ID (default: 'default' for ABLE core)",
                },
            },
            "required": [],
        },
    },
}


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_tenant_onboard(tenant_id: str, name: str, domain: str,
                                 personality: str = "", **kwargs) -> str:
    """Onboard a new tenant with full pipeline setup."""
    try:
        from able.core.tenants.tenant_manager import TenantManager

        manager = TenantManager()
        config = manager.onboard(
            tenant_id=tenant_id,
            name=name,
            domain=domain,
            personality=personality or f"Professional {domain} assistant for {name}.",
        )

        # Run initial harvest to seed the corpus
        harvest_summary = ""
        try:
            from able.core.distillation.harvest_runner import run_harvest

            result = await run_harvest(
                since_hours=720,  # 30 days backfill
                tenant_id=tenant_id,
                dry_run=False,
            )
            harvest_summary = (
                f"\n\nInitial harvest: {result.total_conversations} conversations, "
                f"{result.total_formatted} training pairs"
            )
            if result.corpus_version:
                harvest_summary += f", corpus {result.corpus_version} ({result.corpus_total} pairs, tier={result.corpus_tier})"
        except Exception as e:
            harvest_summary = f"\n\nInitial harvest skipped: {e}"

        return (
            f"Tenant **{name}** (`{tenant_id}`) onboarded.\n"
            f"- Domain: {domain}\n"
            f"- Config: `config/tenants/{tenant_id}.yaml`\n"
            f"- Data: `~/.able/tenants/{tenant_id}/`\n"
            f"- Distillation: auto-retrain at {config.distillation.get('training_threshold', 500)} pairs\n"
            f"- Data isolation: enforced (cross_tenant_training=False)"
            f"{harvest_summary}"
        )
    except ValueError as e:
        return f"Onboarding failed: {e}"
    except Exception as e:
        logger.error("Tenant onboard failed: %s", e, exc_info=True)
        return f"Onboarding error: {e}"


async def handle_tenant_list(**kwargs) -> str:
    """List all tenants."""
    from able.core.tenants.tenant_manager import TenantManager

    manager = TenantManager()
    tenants = manager.list_tenants()

    if not tenants:
        return "No tenants configured."

    lines = ["**Active Tenants:**"]
    for t in tenants:
        status_icon = {"active": "🟢", "paused": "🟡", "archived": "⚪"}.get(t.status, "❓")
        lines.append(f"{status_icon} `{t.tenant_id}` — {t.name} ({t.domain}) [{t.status}]")
    return "\n".join(lines)


async def handle_tenant_status(tenant_id: str, **kwargs) -> str:
    """Get detailed tenant status."""
    from able.core.tenants.tenant_manager import TenantManager

    manager = TenantManager()
    config = manager.get_tenant(tenant_id)
    if not config:
        return f"Tenant not found: `{tenant_id}`"

    # Corpus stats
    corpus_info = "No corpus data"
    try:
        from able.core.distillation.corpus_builder import CorpusBuilder

        builder = CorpusBuilder()
        stats = builder.get_stats(tenant_id)
        if stats.get("versions", 0) > 0:
            corpus_info = (
                f"v{stats['latest']} — {stats['total_pairs']} pairs, "
                f"tier={stats['tier']}, avg_quality={stats['avg_quality']:.2f}"
            )
    except Exception:
        pass

    # Store stats
    store_info = ""
    try:
        from able.core.distillation.store import DistillationStore

        store = DistillationStore()
        pairs = store.get_pairs(tenant_id=tenant_id, limit=100000)
        hq = [p for p in pairs if p.quality_score >= 0.8]
        store_info = f"\n- Store: {len(pairs)} total, {len(hq)} high-quality (≥0.8)"
    except Exception:
        pass

    # Training readiness
    threshold = config.distillation.get("training_threshold", 500)
    ready = "ready" if len(hq) >= threshold else f"need {threshold - len(hq)} more HQ pairs"

    return (
        f"**{config.name}** (`{tenant_id}`)\n"
        f"- Domain: {config.domain}\n"
        f"- Status: {config.status}\n"
        f"- Corpus: {corpus_info}"
        f"{store_info}\n"
        f"- Training: {ready} (threshold={threshold})\n"
        f"- Auto-retrain: {config.distillation.get('auto_retrain', False)}\n"
        f"- Tier 0 enabled: {config.routing.get('tier_0_enabled', False)}"
    )


async def handle_distillation_status(tenant_id: str = "default", **kwargs) -> str:
    """Get distillation pipeline status."""
    lines = [f"**Distillation Status** (`{tenant_id}`)"]

    # Store stats
    try:
        from able.core.distillation.store import DistillationStore

        store = DistillationStore()
        pairs = store.get_pairs(tenant_id=tenant_id, limit=100000)
        hq = [p for p in pairs if p.quality_score >= 0.8]
        domains = {}
        for p in hq:
            domains[p.domain] = domains.get(p.domain, 0) + 1
        lines.append(f"- Pairs: {len(pairs)} total, {len(hq)} high-quality")
        lines.append(f"- Domains: {domains}")
    except Exception as e:
        lines.append(f"- Store: error ({e})")

    # Corpus stats
    try:
        from able.core.distillation.corpus_builder import CorpusBuilder

        builder = CorpusBuilder()
        stats = builder.get_stats(tenant_id)
        if stats.get("versions", 0) > 0:
            lines.append(
                f"- Corpus: {stats['latest']} — {stats['total_pairs']} pairs, "
                f"tier={stats['tier']}"
            )
        else:
            lines.append("- Corpus: not built yet")
    except Exception:
        pass

    # GPU budget
    try:
        from able.core.distillation.training.gpu_budget import GPUBudget

        budget = GPUBudget()
        lines.append(f"- GPU budget: {budget.remaining():.1f}h remaining")
    except Exception:
        pass

    return "\n".join(lines)


async def handle_distillation_harvest(
    tenant_id: str = "default",
    since_hours: int = 168,
    dry_run: bool = False,
    **kwargs,
) -> str:
    """Run harvest pipeline."""
    from able.core.distillation.harvest_runner import run_harvest

    result = await run_harvest(
        since_hours=since_hours,
        tenant_id=tenant_id,
        dry_run=dry_run,
    )

    lines = [
        f"**Harvest {'(dry run) ' if dry_run else ''}Complete** (`{tenant_id}`)",
        f"- Conversations: {result.total_conversations}",
        f"- Deduplicated: {result.total_deduplicated}",
        f"- Training pairs: {result.total_formatted}",
        f"- Duration: {result.duration_ms:.0f}ms",
    ]

    if result.corpus_version:
        lines.append(
            f"- Corpus: {result.corpus_version} "
            f"({result.corpus_total} pairs, tier={result.corpus_tier})"
        )

    for s in result.sources:
        if s.conversations > 0:
            lines.append(f"  - {s.source}: {s.conversations}")
        elif s.error:
            lines.append(f"  - {s.source}: ❌ {s.error}")

    if result.errors:
        lines.append(f"\n⚠️ {len(result.errors)} errors")

    return "\n".join(lines)


async def handle_distillation_build_corpus(
    tenant_id: str = "default", **kwargs
) -> str:
    """Build or rebuild corpus."""
    from able.core.distillation.corpus_builder import CorpusBuilder

    builder = CorpusBuilder()

    if tenant_id == "default":
        from able.core.distillation.store import DistillationStore

        store = DistillationStore()
        pairs = store.get_pairs(
            tenant_id="default",
            min_quality=builder.quality_threshold,
            limit=100_000,
        )
        pair_dicts = [
            {
                "prompt": p.prompt,
                "response": p.gold_response,
                "domain": p.domain,
                "quality_score": p.quality_score,
                "model": p.gold_model,
                "tenant_id": "default",
                "source": "able_core",
            }
            for p in pairs
        ]
        result = builder.build_full(pair_dicts, tenant_id="default")
    else:
        result = builder.build_tenant_with_able_base(tenant_id)

    return (
        f"**Corpus Built** (`{tenant_id}`)\n"
        f"- Version: {result.version}\n"
        f"- Total: {result.total} ({result.train_count} train / "
        f"{result.val_count} val / {result.test_count} test)\n"
        f"- Tier: {result.tier}\n"
        f"- Avg quality: {result.avg_quality:.3f}\n"
        f"- Domains: {result.domains}"
    )


# ── Registration ──────────────────────────────────────────────────────────────

def register_tools(registry: "ToolRegistry"):
    """Register all tenant and distillation tools with the registry."""
    registry.register(
        name="tenant_onboard",
        definition=TENANT_ONBOARD,
        handler=handle_tenant_onboard,
        display_name="Tenants / Onboard",
        requires_approval=True,
        risk_level="medium",
        category="agents-tasks",
        read_only=False,
        concurrent_safe=False,
        surface="tenant",
        artifact_kind="markdown",
        tags=["tenant", "distillation"],
    )
    registry.register(
        name="tenant_list",
        definition=TENANT_LIST,
        handler=handle_tenant_list,
        display_name="Tenants / List",
        category="agents-tasks",
        read_only=True,
        concurrent_safe=True,
        surface="tenant",
        artifact_kind="markdown",
        tags=["tenant", "read"],
    )
    registry.register(
        name="tenant_status",
        definition=TENANT_STATUS,
        handler=handle_tenant_status,
        display_name="Tenants / Status",
        category="agents-tasks",
        read_only=True,
        concurrent_safe=True,
        surface="tenant",
        artifact_kind="markdown",
        tags=["tenant", "read"],
    )
    registry.register(
        name="distillation_status",
        definition=DISTILLATION_STATUS,
        handler=handle_distillation_status,
        display_name="Distillation / Status",
        category="planning",
        read_only=True,
        concurrent_safe=True,
        surface="distillation",
        artifact_kind="markdown",
        tags=["distillation", "status"],
    )
    registry.register(
        name="distillation_harvest",
        definition=DISTILLATION_HARVEST,
        handler=handle_distillation_harvest,
        display_name="Distillation / Harvest",
        category="execution",
        requires_approval=True,
        risk_level="medium",
        read_only=False,
        concurrent_safe=False,
        surface="distillation",
        artifact_kind="markdown",
        tags=["distillation", "harvest"],
    )
    registry.register(
        name="distillation_build_corpus",
        definition=DISTILLATION_BUILD_CORPUS,
        handler=handle_distillation_build_corpus,
        display_name="Distillation / Build Corpus",
        category="planning",
        requires_approval=True,
        risk_level="medium",
        read_only=False,
        concurrent_safe=False,
        surface="distillation",
        artifact_kind="markdown",
        tags=["distillation", "corpus"],
    )
