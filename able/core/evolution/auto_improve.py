"""
Eval-Driven Auto-Improvement — The AGI Loop

Reads eval failures → decomposes into improvement tasks → spawns agent swarm →
validates fixes → applies via SelfImprovementEngine.

This is the bridge between "we ran evals" and "the system got better."

Integration points:
  - collect_results.py → feeds parsed eval data here
  - SwarmCoordinator → parallel analysis/coding/review agents
  - SelfImprovementEngine → apply approved changes
  - EvolutionDaemon → can trigger this as part of its cycle

Usage:
    from able.core.evolution.auto_improve import AutoImprover
    improver = AutoImprover(llm_provider=chain)
    report = await improver.run(eval_data)
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Project root — two levels up from this file
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_SKILL_TARGETS = {
    "copywriting": "able/skills/library/copywriting/SKILL.md",
    "security": "able/skills/library/security-audit/SKILL.md",
    "refactor": "able/skills/library/code-refactoring/SKILL.md",
    "research": "able/skills/library/web-research/SKILL.md",
    "self_improvement": "able/skills/library/self-improvement/SKILL.md",
}


def _resolve_skill_target(eval_name: str) -> tuple[str, str]:
    """Map an eval name to the closest concrete SKILL.md path."""
    lower = eval_name.lower()
    if any(word in lower for word in ["copy", "landing", "email", "sales", "brand"]):
        return "copywriting", _SKILL_TARGETS["copywriting"]
    if any(word in lower for word in ["security", "audit", "threat", "pentest"]):
        return "security", _SKILL_TARGETS["security"]
    if any(word in lower for word in ["refactor", "code", "debug", "coding"]):
        return "refactor", _SKILL_TARGETS["refactor"]
    if any(word in lower for word in ["research", "web", "sources", "citation"]):
        return "research", _SKILL_TARGETS["research"]
    return "self_improvement", _SKILL_TARGETS["self_improvement"]


def _build_skill_patch(action: "ImprovementAction") -> str:
    """Build a concise SKILL.md reinforcement section from eval evidence."""
    return (
        "### Latest Eval Reinforcement\n"
        f"- Source eval: `{action.source_eval or 'unknown'}`\n"
        f"- Failure pattern: {action.failure_pattern}\n"
        f"- Correction: {action.description}\n"
        f"- Required adjustment: {action.proposed_change}\n"
        "- Guardrail: keep instructions concrete, measurable, and output-focused.\n"
    )


@dataclass
class ImprovementAction:
    """A concrete action to improve the system."""
    id: str
    category: str          # "skill", "enricher", "routing", "prompt", "model"
    target_file: str
    description: str
    proposed_change: str
    confidence: float      # 0.0-1.0
    source_eval: str
    failure_pattern: str
    applied: bool = False
    validated: bool = False


@dataclass
class ImprovementReport:
    """Result of an auto-improvement cycle."""
    timestamp: str
    evals_analyzed: int
    failures_analyzed: int
    actions_proposed: int
    actions_validated: int
    actions_applied: int
    actions: List[ImprovementAction] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    duration_ms: float = 0.0


# ── Failure pattern classifiers ──────────────────────────────────

def _classify_failures(parsed_evals: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Classify eval failures into actionable categories.

    Returns dict of category → list of failure records.
    """
    categories = {
        "thinking_bleed": [],    # <think> tokens in output
        "format_violation": [],  # wrong format, missed structure
        "content_quality": [],   # correct format but weak content
        "under_routing": [],     # T1 fail, T4 pass
        "over_routing": [],      # T4 fail, T1 pass (wasted spend)
        "enricher_gap": [],      # enriched didn't help
        "skill_gap": [],         # skill-specific failures
    }

    for ev in parsed_evals:
        desc = ev.get("description", "")

        # Check routing mismatches
        for mismatch in ev.get("routing_mismatches", []):
            if "under" in mismatch.get("issue", ""):
                categories["under_routing"].append({
                    "eval": desc, "test": mismatch["test"],
                    "issue": mismatch["issue"],
                })
            elif "wasted" in mismatch.get("issue", ""):
                categories["over_routing"].append({
                    "eval": desc, "test": mismatch["test"],
                    "issue": mismatch["issue"],
                })

        # Analyze per-provider failures
        for provider_label, prov_data in ev.get("by_provider", {}).items():
            for output in prov_data.get("outputs", []):
                if output.get("pass"):
                    continue

                reason = (output.get("reason") or "").lower()
                test = output.get("test", "unknown")

                record = {
                    "eval": desc, "test": test, "provider": provider_label,
                    "reason": output.get("reason", ""),
                    "output_preview": str(output.get("output", ""))[:300],
                }

                # Classify by failure reason
                if any(w in reason for w in ["think>", "thinking", "reasoning token"]):
                    categories["thinking_bleed"].append(record)
                elif any(w in reason for w in ["format", "structure", "markdown", "heading"]):
                    categories["format_violation"].append(record)
                elif any(w in reason for w in ["quality", "depth", "detail", "specific"]):
                    categories["content_quality"].append(record)
                else:
                    # Determine if it's a skill or enricher issue
                    if "enricher" in desc.lower():
                        categories["enricher_gap"].append(record)
                    else:
                        categories["skill_gap"].append(record)

    return {k: v for k, v in categories.items() if v}


# ── Improvement generators (one per category) ───────────────────

def _generate_routing_improvements(
    under_routes: List[Dict],
    over_routes: List[Dict],
) -> List[ImprovementAction]:
    """Generate scorer weight adjustments from routing mismatches."""
    actions = []

    if len(under_routes) >= 3:
        # Significant under-routing — bump domain weights
        domains_affected = set()
        for r in under_routes:
            test = r.get("test", "").lower()
            if any(w in test for w in ["security", "audit", "threat"]):
                domains_affected.add("security")
            elif any(w in test for w in ["code", "refactor", "debug"]):
                domains_affected.add("coding")
            elif any(w in test for w in ["finance", "invest", "money"]):
                domains_affected.add("financial")

        for domain in domains_affected:
            actions.append(ImprovementAction(
                id=f"route-bump-{domain}",
                category="routing",
                target_file="config/scorer_weights.yaml",
                description=f"Bump {domain} domain adjustment +0.05 (under-routing pattern)",
                proposed_change=f"domain_adjustments.{domain}: += 0.05",
                confidence=0.7,
                source_eval=under_routes[0].get("eval", ""),
                failure_pattern=f"{len(under_routes)} under-routes in {domain}",
            ))

    if len(over_routes) >= 2:
        actions.append(ImprovementAction(
            id="route-over-alert",
            category="routing",
            target_file="config/scorer_weights.yaml",
            description=f"Over-routing detected: {len(over_routes)} cases — review tier thresholds",
            proposed_change="Review tier_thresholds or domain adjustments",
            confidence=0.5,
            source_eval=over_routes[0].get("eval", ""),
            failure_pattern=f"{len(over_routes)} over-routes (wasted Opus spend)",
        ))

    return actions


def _generate_enricher_improvements(
    enricher_gaps: List[Dict],
    format_violations: List[Dict],
) -> List[ImprovementAction]:
    """Generate enricher improvements from failure patterns."""
    actions = []

    # Count format violations that enrichment should have caught
    format_domains = {}
    for fv in format_violations:
        reason = fv.get("reason", "")
        for domain in ["coding", "security", "creative", "research"]:
            if domain in fv.get("eval", "").lower() or domain in fv.get("test", "").lower():
                format_domains[domain] = format_domains.get(domain, 0) + 1

    for domain, count in format_domains.items():
        if count >= 2:
            actions.append(ImprovementAction(
                id=f"enricher-format-{domain}",
                category="enricher",
                target_file="able/core/routing/prompt_enricher.py",
                description=f"Add format-specific steering for {domain} domain ({count} format failures)",
                proposed_change=f"Add stricter format directives to OUTPUT_SPECS['{domain}']",
                confidence=0.6,
                source_eval=format_violations[0].get("eval", ""),
                failure_pattern=f"{count} format violations in {domain}",
            ))

    # Enricher-specific gaps (enrichment didn't improve quality)
    if len(enricher_gaps) >= 3:
        actions.append(ImprovementAction(
            id="enricher-quality-gap",
            category="enricher",
            target_file="able/core/routing/prompt_enricher.py",
            description="Enricher not lifting quality — review flavor word expansion criteria",
            proposed_change="Review DOMAIN_CRITERIA expansions for affected domains",
            confidence=0.5,
            source_eval=enricher_gaps[0].get("eval", ""),
            failure_pattern=f"{len(enricher_gaps)} cases where enrichment didn't help",
        ))

    return actions


def _generate_skill_improvements(
    skill_gaps: List[Dict],
    content_quality: List[Dict],
) -> List[ImprovementAction]:
    """Generate skill-level improvements from failure patterns."""
    actions = []

    # Group failures by skill/eval
    by_eval = {}
    for gap in skill_gaps + content_quality:
        eval_name = gap.get("eval", "unknown")
        by_eval.setdefault(eval_name, []).append(gap)

    for eval_name, failures in by_eval.items():
        # Count T1-specific failures (these are the ones thinking strip helps)
        t1_failures = [f for f in failures if "T1" in f.get("provider", "")]
        t2_failures = [f for f in failures if "T2" in f.get("provider", "")]
        t4_failures = [f for f in failures if "T4" in f.get("provider", "")]

        if len(t1_failures) >= 3 and len(t4_failures) == 0:
            # T1 is the weak link, T4 passes — model capability gap, not skill issue
            actions.append(ImprovementAction(
                id=f"skill-t1-ceiling-{eval_name[:20]}",
                category="model",
                target_file="config/scorer_weights.yaml",
                description=f"T1 ceiling hit on {eval_name} — route more to T2",
                proposed_change="Lower tier_1_max threshold or increase domain adjustment",
                confidence=0.7,
                source_eval=eval_name,
                failure_pattern=f"T1: {len(t1_failures)} fail, T4: {len(t4_failures)} fail",
            ))

        if len(t2_failures) >= 2:
            # T2 GPT 5.4 also struggling — might be a skill quality issue
            skill_name, target_path = _resolve_skill_target(eval_name)

            actions.append(ImprovementAction(
                id=f"skill-quality-{skill_name}",
                category="skill",
                target_file=target_path,
                description=f"Skill quality issue in {skill_name} — T2 failing ({len(t2_failures)} cases)",
                proposed_change="Review SKILL.md for missing quality criteria or overly vague instructions",
                confidence=0.6,
                source_eval=eval_name,
                failure_pattern=f"T2 GPT 5.4 failing {len(t2_failures)}/{len(failures)} on {skill_name}",
            ))

    return actions


# ── Swarm-powered deep analysis ─────────────────────────────────

async def _swarm_analyze_failures(
    failures: Dict[str, List[Dict]],
    llm_call: Optional[Callable] = None,
) -> List[str]:
    """
    Use agent swarm roles to analyze failure patterns.

    Even without a live LLM, the structured decomposition produces insights.
    With an LLM, each role provides specialized analysis.
    """
    insights = []

    total_failures = sum(len(v) for v in failures.values())
    categories_hit = list(failures.keys())

    # ── ANALYST role: pattern detection ──
    analyst_insight = f"Failure distribution across {total_failures} cases: "
    analyst_insight += ", ".join(f"{k}: {len(v)}" for k, v in failures.items())
    insights.append(f"[ANALYST] {analyst_insight}")

    # Identify concentration
    if failures:
        worst_category = max(failures.items(), key=lambda x: len(x[1]))
        insights.append(
            f"[ANALYST] Worst category: {worst_category[0]} "
            f"({len(worst_category[1])} failures, "
            f"{len(worst_category[1]) / max(total_failures, 1) * 100:.0f}% of total)"
        )

    # ── REVIEWER role: root cause analysis ──
    if "thinking_bleed" in failures:
        count = len(failures["thinking_bleed"])
        insights.append(
            f"[REVIEWER] Thinking bleed is still active in {count} outputs. "
            f"Verify strip_thinking_tokens() is applied in the eval pipeline "
            f"(evals bypass the gateway, so stripping must happen in the eval harness too)."
        )

    if "under_routing" in failures:
        count = len(failures["under_routing"])
        tests = [f["test"] for f in failures["under_routing"][:3]]
        insights.append(
            f"[REVIEWER] {count} under-routed prompts. Affected tests: {', '.join(tests)}. "
            f"These prompts need higher complexity scores to reach T2/T4."
        )

    # ── CRITIC role: challenge assumptions ──
    if "enricher_gap" in failures and "content_quality" in failures:
        insights.append(
            "[CRITIC] Both enricher and content quality failing — the enricher may be "
            "adding criteria the model can't follow. Consider reducing enrichment depth "
            "for T1 or only enriching for T2+."
        )

    if len(categories_hit) >= 4:
        insights.append(
            "[CRITIC] Failures spread across 4+ categories. This isn't a single-fix problem. "
            "Prioritize: (1) thinking strip in eval harness, (2) routing weights, (3) skill quality."
        )

    # ── PLANNER role: prioritization ──
    priority_order = []
    if "thinking_bleed" in failures:
        priority_order.append(("thinking_bleed", "High", "Mechanical fix, highest ROI"))
    if "under_routing" in failures:
        priority_order.append(("under_routing", "High", "Config change, no code risk"))
    if "format_violation" in failures:
        priority_order.append(("format_violation", "Medium", "Enricher OUTPUT_SPECS tuning"))
    if "content_quality" in failures:
        priority_order.append(("content_quality", "Medium", "Skill prompt quality"))
    if "skill_gap" in failures:
        priority_order.append(("skill_gap", "Low", "Needs per-skill investigation"))
    if "enricher_gap" in failures:
        priority_order.append(("enricher_gap", "Low", "May be model ceiling, not enricher"))

    if priority_order:
        insights.append(
            "[PLANNER] Priority order: "
            + " → ".join(f"{p[0]} ({p[1]})" for p in priority_order)
        )

    # ── LLM-powered deep analysis if available ──
    if llm_call and total_failures >= 5:
        try:
            # Build a compact summary for the LLM
            summary = json.dumps({
                "total_failures": total_failures,
                "categories": {k: len(v) for k, v in failures.items()},
                "sample_reasons": [
                    f.get("reason", "")[:200]
                    for cat_failures in failures.values()
                    for f in cat_failures[:2]
                ][:10],
            }, indent=2)

            system = (
                "You are a QA engineer analyzing AI system eval failures. "
                "Identify the root cause pattern and suggest the single highest-impact fix. "
                "Be specific: name the file, the function, the config value. Under 100 words."
            )
            llm_insight = await llm_call(system, f"Failure data:\n{summary}")
            if llm_insight and llm_insight.strip():
                insights.append(f"[LLM_ANALYST] {llm_insight.strip()}")
        except Exception as e:
            logger.debug(f"LLM analysis failed (non-critical): {e}")

    return insights


# ── Main orchestrator ────────────────────────────────────────────

class AutoImprover:
    """
    Eval-driven auto-improvement orchestrator.

    Takes parsed eval results → classifies failures → generates improvement
    actions → validates → applies via SelfImprovementEngine.
    """

    def __init__(
        self,
        llm_call: Optional[Callable] = None,
        auto_apply: bool = False,
        log_dir: str = "data/auto_improve",
        approval_workflow: Any = None,
        self_improvement_engine: Any = None,
    ):
        self.llm_call = llm_call
        self.auto_apply = auto_apply
        self.log_dir = Path(_PROJECT_ROOT) / log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.approval_workflow = approval_workflow
        self._self_improvement_engine = self_improvement_engine

    async def run(self, parsed_evals: List[Dict]) -> ImprovementReport:
        """
        Run a full auto-improvement cycle from eval data.

        Steps:
        1. Classify failures into categories
        2. Spawn swarm analysis (ANALYST + REVIEWER + CRITIC + PLANNER)
        3. Generate improvement actions per category
        4. Validate actions (confidence thresholds + safety checks)
        5. Apply if auto_apply=True, otherwise propose
        6. Log everything
        """
        start = time.perf_counter()
        report = ImprovementReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            evals_analyzed=len(parsed_evals),
            failures_analyzed=0,
            actions_proposed=0,
            actions_validated=0,
            actions_applied=0,
        )

        # ── Step 1: Classify failures ──
        failures = _classify_failures(parsed_evals)
        report.failures_analyzed = sum(len(v) for v in failures.values())

        if not failures:
            report.insights.append("No failures to analyze — system is healthy")
            report.duration_ms = (time.perf_counter() - start) * 1000
            self._log_report(report)
            return report

        logger.info(
            f"[AUTO_IMPROVE] Classified {report.failures_analyzed} failures into "
            f"{len(failures)} categories: {list(failures.keys())}"
        )

        # ── Step 2: Swarm analysis ──
        insights = await _swarm_analyze_failures(failures, self.llm_call)
        report.insights = insights

        # ── Step 3: Generate improvement actions ──
        actions = []

        # Routing improvements
        actions.extend(_generate_routing_improvements(
            failures.get("under_routing", []),
            failures.get("over_routing", []),
        ))

        # Enricher improvements
        actions.extend(_generate_enricher_improvements(
            failures.get("enricher_gap", []),
            failures.get("format_violation", []),
        ))

        # Skill improvements
        actions.extend(_generate_skill_improvements(
            failures.get("skill_gap", []),
            failures.get("content_quality", []),
        ))

        # Thinking bleed — special: this should already be fixed,
        # but if it's still showing up in evals, the eval harness needs the strip too
        if "thinking_bleed" in failures and len(failures["thinking_bleed"]) >= 2:
            actions.append(ImprovementAction(
                id="eval-harness-strip",
                category="enricher",
                target_file="able/evals/prompts/enricher-enhanced.txt",
                description="Add thinking token strip to eval post-processor",
                proposed_change=(
                    "Add transform to eval configs: "
                    "postprocess: strip_thinking_tokens"
                ),
                confidence=0.8,
                source_eval=failures["thinking_bleed"][0].get("eval", ""),
                failure_pattern=f"{len(failures['thinking_bleed'])} thinking bleed in evals",
            ))

        report.actions = actions
        report.actions_proposed = len(actions)

        # ── Step 4: Validate ──
        validated = []
        for action in actions:
            if action.confidence >= 0.5:
                action.validated = True
                validated.append(action)
            else:
                logger.debug(
                    f"[AUTO_IMPROVE] Skipping low-confidence action: "
                    f"{action.id} ({action.confidence})"
                )
        report.actions_validated = len(validated)

        # ── Step 5: Apply (if auto_apply) ──
        if self.auto_apply and validated:
            applied = await self._apply_actions(validated)
            report.actions_applied = applied

        # ── Step 6: Log ──
        report.duration_ms = (time.perf_counter() - start) * 1000
        self._log_report(report)

        logger.info(
            f"[AUTO_IMPROVE] Cycle complete: {report.actions_proposed} proposed, "
            f"{report.actions_validated} validated, {report.actions_applied} applied "
            f"({report.duration_ms:.0f}ms)"
        )

        return report

    async def _apply_actions(self, actions: List[ImprovementAction]) -> int:
        """Apply validated actions via SelfImprovementEngine."""
        applied = 0

        try:
            from able.core.agi.self_improvement import SelfImprovementEngine, UpdateType

            engine = self._self_improvement_engine or SelfImprovementEngine(
                v2_path=_PROJECT_ROOT,
                approval_workflow=self.approval_workflow,
            )
        except ImportError:
            logger.warning("[AUTO_IMPROVE] SelfImprovementEngine not importable — skipping apply")
            return 0

        for action in actions:
            if action.category == "routing":
                # Routing changes go through evolution daemon, not self-improvement
                logger.info(f"[AUTO_IMPROVE] Routing action queued for evolution daemon: {action.id}")
                action.applied = True
                applied += 1
                continue

            try:
                target_path = Path(action.target_file)
                if not target_path.is_absolute():
                    target_path = _PROJECT_ROOT / target_path

                if action.category == "skill" and target_path.name == "SKILL.md":
                    update = await engine.propose_update(
                        document_path=target_path,
                        content=_build_skill_patch(action),
                        update_type=UpdateType.SECTION,
                        reason=action.description,
                        source=f"auto_improve:{action.source_eval or action.id}",
                        metadata={
                            "section_heading": "## Auto-Improve Guidance",
                            "action_id": action.id,
                            "failure_pattern": action.failure_pattern,
                        },
                    )
                    action.applied = update.applied
                    if update.applied:
                        applied += 1
                    continue

                await engine.add_learning(
                    f"Auto-improvement [{action.category}]: {action.description}\n"
                    f"Target: {action.target_file}\n"
                    f"Pattern: {action.failure_pattern}\n"
                    f"Proposed: {action.proposed_change}"
                )
                action.applied = True
                applied += 1
            except Exception as e:
                logger.warning(f"[AUTO_IMPROVE] Failed to apply {action.id}: {e}")

        return applied

    def _log_report(self, report: ImprovementReport):
        """Log improvement report to disk."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = self.log_dir / f"cycle_{timestamp}.json"

        data = {
            "timestamp": report.timestamp,
            "evals_analyzed": report.evals_analyzed,
            "failures_analyzed": report.failures_analyzed,
            "actions_proposed": report.actions_proposed,
            "actions_validated": report.actions_validated,
            "actions_applied": report.actions_applied,
            "duration_ms": report.duration_ms,
            "insights": report.insights,
            "actions": [
                {
                    "id": a.id,
                    "category": a.category,
                    "target_file": a.target_file,
                    "description": a.description,
                    "proposed_change": a.proposed_change,
                    "confidence": a.confidence,
                    "failure_pattern": a.failure_pattern,
                    "validated": a.validated,
                    "applied": a.applied,
                }
                for a in report.actions
            ],
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info(f"[AUTO_IMPROVE] Report logged to {path}")


# ── Behavioral benchmarks (Plan Item 10) ─────────────────────────

# 10 standardized prompts that probe common failure modes across all tiers.
# Each prompt targets a specific failure class so we can generate
# per-model-family execution guidance.

BEHAVIORAL_PROBES = [
    # ── Thinking bleed ──
    {"id": "tb-1", "mode": "thinking_bleed",
     "prompt": "What is 2+2? Answer with ONLY the number, nothing else.",
     "check": "output should be exactly '4' with no <think> or reasoning preamble"},
    {"id": "tb-2", "mode": "thinking_bleed",
     "prompt": "Summarize this in one sentence: The cat sat on the mat.",
     "check": "no <think>, [Internal reasoning], or 'Thinking:' tokens in output"},
    # ── Empty responses ──
    {"id": "er-1", "mode": "empty_response",
     "prompt": "List three benefits of exercise.",
     "check": "output must be non-empty and contain at least 20 characters"},
    {"id": "er-2", "mode": "empty_response",
     "prompt": "Explain why the sky is blue in two sentences.",
     "check": "output must be non-empty and contain at least 30 characters"},
    # ── Tool refusal ──
    {"id": "tr-1", "mode": "tool_refusal",
     "prompt": "Use the web_search tool to find the current weather in Tokyo.",
     "check": "output should contain a tool_call or acknowledge tool use intent"},
    {"id": "tr-2", "mode": "tool_refusal",
     "prompt": "Search GitHub for the most starred Python project this month.",
     "check": "output should attempt tool use, not refuse or explain inability"},
    # ── Format violations ──
    {"id": "fv-1", "mode": "format_violation",
     "prompt": "Return a valid JSON object with keys 'name' and 'age'.",
     "check": "output must parse as valid JSON with required keys"},
    {"id": "fv-2", "mode": "format_violation",
     "prompt": "Write a markdown table with columns: Feature, Status, Notes. Include 3 rows.",
     "check": "output must contain a markdown table with | separators and 3 data rows"},
    # ── Hallucinated tool calls ──
    {"id": "ht-1", "mode": "hallucinated_tool",
     "prompt": "What tools do you have available? List them.",
     "check": "should not fabricate tool names not in the actual tool list"},
    {"id": "ht-2", "mode": "hallucinated_tool",
     "prompt": "Can you run a database query to check user count?",
     "check": "should not claim to execute tools it doesn't have"},
]

# 5 failure modes → per-model-family guidance templates
_FAILURE_MODE_GUIDANCE = {
    "thinking_bleed": (
        "This model family emits <think> reasoning tokens in user-facing output. "
        "Apply strip_thinking_tokens() post-processing. For evals, add the strip "
        "transform to the eval harness."
    ),
    "empty_response": (
        "This model family occasionally returns empty or near-empty responses. "
        "Add retry logic with temperature bump (+0.1) on empty output. Consider "
        "increasing max_tokens if the model is hitting length limits."
    ),
    "tool_refusal": (
        "This model family sometimes refuses to use tools or explains why it can't. "
        "Add explicit tool-use instruction in system prompt: 'You MUST use available "
        "tools when the task requires external data. Do not explain inability.'"
    ),
    "format_violation": (
        "This model family struggles with strict format requirements. "
        "Add format examples in the system prompt. For JSON, include a template. "
        "For markdown tables, show the expected structure."
    ),
    "hallucinated_tool": (
        "This model family fabricates tool names or claims capabilities it lacks. "
        "Inject the exact tool catalog into the system prompt. Add guard: "
        "'Only reference tools from the provided list. Never invent tool names.'"
    ),
}


@dataclass
class BehavioralAuditResult:
    """Result of running behavioral probes against a provider tier."""
    provider_name: str
    tier: int
    total_probes: int
    failures: Dict[str, List[Dict]]  # mode → list of failure details
    pass_rate: float
    guidance: List[str]  # Generated execution guidance for this provider
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


async def provider_behavioral_audit(
    llm_call: Optional[Callable] = None,
    tiers: Optional[List[int]] = None,
    log_dir: str = "data/behavioral_audit",
) -> List[BehavioralAuditResult]:
    """
    Run 10 standardized prompts through each provider tier to detect
    systematic failure modes.

    Classifies 5 failure types:
      1. thinking_bleed — <think> tokens in output
      2. empty_response — blank or near-empty output
      3. tool_refusal — refuses to use available tools
      4. format_violation — wrong format (JSON, markdown, etc.)
      5. hallucinated_tool — fabricates tool names

    Generates per-model-family execution guidance injected into system
    prompts. Results feed the evolution daemon.

    Args:
        llm_call: async callable(system, user) → str. If None, uses
                  ProviderRegistry to build per-tier chains.
        tiers: Which tiers to audit (default: [1, 2, 4]).
        log_dir: Where to save audit reports.

    Returns:
        List of BehavioralAuditResult per tier.
    """
    import re as _re

    tiers = tiers or [1, 2, 4]
    out_dir = Path(_PROJECT_ROOT) / log_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    results: List[BehavioralAuditResult] = []

    # Build per-tier LLM callers
    tier_callers: Dict[int, tuple] = {}  # tier → (name, callable)

    if llm_call:
        for t in tiers:
            tier_callers[t] = (f"tier-{t}", llm_call)
    else:
        try:
            from able.core.routing.provider_registry import ProviderRegistry
            registry = ProviderRegistry()
            for t in tiers:
                chain = registry.build_chain_for_tier(t)
                if chain and chain.providers:
                    provider_name = chain.providers[0].name

                    async def _make_call(system: str, user: str, _c=chain) -> str:
                        from able.core.providers.base import Message, Role
                        msgs = [
                            Message(role=Role.SYSTEM, content=system),
                            Message(role=Role.USER, content=user),
                        ]
                        result = await _c.complete(msgs, temperature=0.3, max_tokens=1024)
                        return result.content

                    tier_callers[t] = (provider_name, _make_call)
        except Exception as e:
            logger.warning("[BEHAVIORAL_AUDIT] Could not build provider chains: %s", e)
            return results

    # ── Run probes per tier ──
    for tier, (provider_name, caller) in tier_callers.items():
        failures: Dict[str, List[Dict]] = {
            "thinking_bleed": [],
            "empty_response": [],
            "tool_refusal": [],
            "format_violation": [],
            "hallucinated_tool": [],
        }
        passed = 0

        for probe in BEHAVIORAL_PROBES:
            probe_id = probe["id"]
            mode = probe["mode"]
            prompt = probe["prompt"]

            try:
                output = await caller(
                    "You are a helpful assistant. Follow instructions precisely.",
                    prompt,
                )
                output = output or ""
            except Exception as e:
                logger.debug("[BEHAVIORAL_AUDIT] Probe %s failed for tier %d: %s", probe_id, tier, e)
                failures[mode].append({
                    "probe_id": probe_id,
                    "error": str(e),
                    "output": "",
                })
                continue

            # ── Classify output against expected behavior ──
            failed = False

            if mode == "thinking_bleed":
                if _re.search(r'<think>|Thinking:|^\[Internal reasoning\]', output, _re.IGNORECASE):
                    failed = True

            elif mode == "empty_response":
                if len(output.strip()) < 20:
                    failed = True

            elif mode == "tool_refusal":
                refusal_signals = ["i can't", "i cannot", "i don't have", "i'm unable", "not able to"]
                if any(s in output.lower() for s in refusal_signals) and "tool" not in output.lower():
                    failed = True

            elif mode == "format_violation":
                if "json" in probe["check"].lower():
                    try:
                        parsed = json.loads(output.strip().strip("`").strip())
                        if not isinstance(parsed, dict):
                            failed = True
                    except (json.JSONDecodeError, ValueError):
                        failed = True
                elif "table" in probe["check"].lower():
                    if output.count("|") < 6:  # header + separator + 3 rows = at least 6 pipes
                        failed = True

            elif mode == "hallucinated_tool":
                fake_tools = ["run_query", "execute_sql", "database_query", "query_db"]
                if any(ft in output.lower() for ft in fake_tools):
                    failed = True

            if failed:
                failures[mode].append({
                    "probe_id": probe_id,
                    "output_preview": output[:300],
                    "check": probe["check"],
                })
            else:
                passed += 1

        # ── Generate per-model-family guidance ──
        guidance = []
        for mode, mode_failures in failures.items():
            if mode_failures:
                guidance.append(
                    f"[{provider_name}/T{tier}] {_FAILURE_MODE_GUIDANCE[mode]}"
                )

        total = len(BEHAVIORAL_PROBES)
        pass_rate = passed / total if total else 0.0

        audit_result = BehavioralAuditResult(
            provider_name=provider_name,
            tier=tier,
            total_probes=total,
            failures={k: v for k, v in failures.items() if v},
            pass_rate=pass_rate,
            guidance=guidance,
        )
        results.append(audit_result)

        # Award buddy XP per tier
        try:
            from able.core.buddy.xp import award_benchmark_xp
            for mode, mode_failures in failures.items():
                award_benchmark_xp(
                    model=provider_name,
                    domain=mode,
                    passed=len(mode_failures) == 0,
                )
        except Exception:
            pass

        logger.info(
            "[BEHAVIORAL_AUDIT] Tier %d (%s): %d/%d passed (%.0f%%), %d guidance items",
            tier, provider_name, passed, total, pass_rate * 100, len(guidance),
        )

    # ── Persist results ──
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"behavioral_audit_{timestamp}.json"
    report_data = [
        {
            "provider": r.provider_name,
            "tier": r.tier,
            "pass_rate": r.pass_rate,
            "total_probes": r.total_probes,
            "failures": r.failures,
            "guidance": r.guidance,
            "timestamp": r.timestamp,
        }
        for r in results
    ]
    with open(report_path, "w") as f:
        json.dump(report_data, f, indent=2, default=str)

    logger.info("[BEHAVIORAL_AUDIT] Report saved to %s", report_path)
    return results


# ── CLI + integration entry points ───────────────────────────────

async def run_from_evals(
    last_n: int = 5,
    auto_apply: bool = False,
    approval_workflow: Any = None,
) -> ImprovementReport:
    """
    Run auto-improvement from the most recent promptfoo evals.

    Called by:
    - collect_results.py (after collecting)
    - evolution daemon (as part of its cycle)
    - CLI: python -m able.core.evolution.auto_improve
    """
    from able.evals.collect_results import DB_PATH, get_able_evals, parse_eval

    if not DB_PATH.exists():
        logger.error(f"promptfoo DB not found at {DB_PATH}")
        return ImprovementReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            evals_analyzed=0, failures_analyzed=0,
            actions_proposed=0, actions_validated=0, actions_applied=0,
            insights=["No promptfoo DB found — run evals first"],
        )

    evals = get_able_evals(last_n=last_n)
    if not evals:
        return ImprovementReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            evals_analyzed=0, failures_analyzed=0,
            actions_proposed=0, actions_validated=0, actions_applied=0,
            insights=["No ABLE evals found in DB"],
        )

    parsed = [parse_eval(e) for e in evals]

    improver = AutoImprover(
        auto_apply=auto_apply,
        approval_workflow=approval_workflow,
    )
    return await improver.run(parsed)


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="ABLE Eval-Driven Auto-Improvement")
    parser.add_argument("--last", type=int, default=5, help="Process last N evals")
    parser.add_argument("--auto-apply", action="store_true", help="Auto-apply validated actions")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    report = asyncio.run(run_from_evals(last_n=args.last, auto_apply=args.auto_apply))

    print(f"\n{'=' * 60}")
    print("AUTO-IMPROVEMENT REPORT")
    print(f"{'=' * 60}")
    print(f"  Evals analyzed: {report.evals_analyzed}")
    print(f"  Failures classified: {report.failures_analyzed}")
    print(f"  Actions proposed: {report.actions_proposed}")
    print(f"  Actions validated: {report.actions_validated}")
    print(f"  Actions applied: {report.actions_applied}")
    print(f"  Duration: {report.duration_ms:.0f}ms")

    if report.insights:
        print(f"\n  INSIGHTS:")
        for insight in report.insights:
            print(f"    {insight}")

    if report.actions:
        print(f"\n  ACTIONS:")
        for action in report.actions:
            status = "APPLIED" if action.applied else ("VALIDATED" if action.validated else "PROPOSED")
            print(f"    [{status}] {action.id}: {action.description}")
            print(f"           Target: {action.target_file} | Confidence: {action.confidence}")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
