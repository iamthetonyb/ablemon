"""
Weekly Research Scout — Monitors AI ecosystem for improvements.

Searches Twitter/X, Reddit, GitHub releases, HuggingFace, and AI news
for relevant developments that could improve ATLAS. Generates a
Telegram-deliverable report with actionable recommendations.

Runs as a cron job (Sunday 10am) and can be triggered manually.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class ResearchFinding:
    """A single research finding."""
    topic: str
    source: str  # "twitter", "reddit", "github", "huggingface", "news"
    title: str
    summary: str
    url: str = ""
    relevance: str = "medium"  # "high", "medium", "low"
    action: str = ""  # What ATLAS should do about it
    tags: List[str] = field(default_factory=list)


@dataclass
class WeeklyResearchReport:
    """Full weekly research report."""
    timestamp: str = ""
    total_findings: int = 0
    high_priority: List[ResearchFinding] = field(default_factory=list)
    findings: List[ResearchFinding] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    search_queries_run: int = 0


# Base TOPICS (not queries) — the research interests ATLAS cares about.
# Queries are generated dynamically each run with date context and rotation.
RESEARCH_TOPICS = {
    "claude_ecosystem": {
        "keywords": ["Claude Code SDK", "Anthropic Claude API", "Claude MCP", "Anthropic agent SDK", "Claude Max", "Claude computer use"],
        "why": "Our T4 provider and CLI tooling — breaking changes or new features directly affect us",
    },
    "agentic_systems": {
        "keywords": ["autonomous AI agent", "self-improving AI", "agentic AGI", "AI agent orchestration", "multi-agent framework"],
        "why": "Competitive landscape and techniques we can adopt for ATLAS evolution daemon",
    },
    "models_training": {
        "keywords": ["Qwen model", "Unsloth fine-tuning", "GGUF quantization", "LoRA QLoRA", "distillation LLM", "open source LLM release"],
        "why": "Our distillation pipeline uses Qwen 3.5 + Unsloth + GGUF — updates change our training approach",
    },
    "tools_infra": {
        "keywords": ["Ollama", "vLLM inference", "Axolotl training", "promptfoo eval", "Arize Phoenix", "LMCache", "MCP server"],
        "why": "Our runtime and eval stack — upgrades can improve performance or unlock features",
    },
    "security": {
        "keywords": ["prompt injection defense", "LLM security", "AI red team", "agentic security"],
        "why": "Trust gate and client data protection — must stay ahead of attack vectors",
    },
    "business_revenue": {
        "keywords": ["AI SaaS pricing", "AI agent monetization", "white-label AI", "AI consulting business model"],
        "why": "Revenue goal is $100k MRR from zero — need market intelligence on pricing and GTM",
    },
    "ecosystem": {
        "keywords": ["OpenAI GPT update", "AI coding assistant", "edge AI deployment", "H100 optimization"],
        "why": "General ecosystem awareness — competitive models, deployment patterns, cost reduction",
    },
}

# How many topics to scan per nightly run (rotates through all over the week)
NIGHTLY_TOPIC_COUNT = 3


class WeeklyResearchScout:
    """
    Automated research scout that monitors the AI ecosystem.

    Uses ATLAS WebSearch (Brave/Perplexity/DuckDuckGo) to find recent
    developments relevant to ATLAS's tech stack and capabilities.
    """

    def __init__(self, report_dir: str = None):
        if report_dir is None:
            report_dir = str(_PROJECT_ROOT / "data" / "research_reports")
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    async def run_research(
        self, categories: List[str] = None, mode: str = "weekly"
    ) -> WeeklyResearchReport:
        """
        Run research scan with dynamic query generation.

        Each run generates fresh queries based on:
        - Current date (for recency in search results)
        - Topic rotation (nightly scans different topics each night)
        - Previous findings (dedup against last report)
        - Current ATLAS goals/objectives (goal-aware queries)
        """
        report = WeeklyResearchReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Initialize web search
        try:
            from atlas.tools.search.web_search import WebSearch
            search = WebSearch()
        except Exception as e:
            report.errors.append(f"WebSearch unavailable: {e}")
            logger.error(f"WebSearch init failed: {e}")
            return report

        # Generate dynamic queries
        queries = self._generate_queries(mode, categories)
        previous_urls = self._load_previous_urls()

        for query, category in queries:
            try:
                findings = await self._search_topic(search, query, category)
                for f in findings:
                    # Dedup against previous report
                    if f.url and f.url in previous_urls:
                        continue
                    report.findings.append(f)
                    if f.relevance == "high":
                        report.high_priority.append(f)
                report.search_queries_run += 1
            except Exception as e:
                report.errors.append(f"Query '{query}': {e}")
                logger.warning(f"Research query failed: {query} — {e}")

            await asyncio.sleep(1.0)

        # Phase 2 (weekly only): Deep analysis via Claude Code SDK
        if mode == "weekly":
            await self._deep_research_phase(report)

        report.total_findings = len(report.findings)

        # Phase 3: LLM analysis — turn raw findings into actionable intelligence
        if report.findings:
            await self._analyze_findings(report)

        # Save report
        self._save_report(report)

        logger.info(
            f"{mode.title()} research complete: {report.total_findings} findings "
            f"({len(report.high_priority)} high priority), "
            f"{len(report.errors)} errors"
        )

        return report

    def _generate_queries(
        self, mode: str, categories: List[str] = None
    ) -> List[tuple]:
        """
        Generate fresh, date-aware search queries. Returns list of (query, category).

        Nightly: rotates through NIGHTLY_TOPIC_COUNT topics per night.
        Weekly: scans all topics with deeper queries.
        """
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%B %Y")  # e.g. "March 2026"
        recency = "this week" if mode == "nightly" else "this month"

        all_topics = list(RESEARCH_TOPICS.keys())
        if categories:
            selected_topics = [t for t in categories if t in RESEARCH_TOPICS]
        elif mode == "nightly":
            # Rotate: use day-of-year to pick different topics each night
            day_offset = now.timetuple().tm_yday
            start = (day_offset * NIGHTLY_TOPIC_COUNT) % len(all_topics)
            indices = [(start + i) % len(all_topics) for i in range(NIGHTLY_TOPIC_COUNT)]
            selected_topics = [all_topics[i] for i in indices]
        else:
            selected_topics = all_topics

        queries = []
        for topic_name in selected_topics:
            topic = RESEARCH_TOPICS[topic_name]
            keywords = topic["keywords"]

            if mode == "nightly":
                # 2 queries per topic, focused on breaking news
                for kw in keywords[:2]:
                    queries.append((f"{kw} new release update {recency}", topic_name))
            else:
                # 3 queries per topic, deeper
                for kw in keywords[:3]:
                    queries.append((f"{kw} {date_str} latest", topic_name))
                # Add one trend query
                queries.append(
                    (f"best new {topic_name.replace('_', ' ')} tools techniques {date_str}", topic_name)
                )

        # Add goal-aware queries from current objectives
        goal_queries = self._generate_goal_queries(recency)
        queries.extend(goal_queries)

        return queries

    def _generate_goal_queries(self, recency: str) -> List[tuple]:
        """Generate queries based on current ATLAS objectives."""
        queries = []
        try:
            goals_path = Path.home() / ".atlas" / "memory" / "current_objectives.yaml"
            if not goals_path.exists():
                return queries

            import yaml
            with open(goals_path) as f:
                objectives = yaml.safe_load(f) or {}

            # Extract urgent/in-progress goals and generate research queries
            for priority in ("urgent", "in_progress"):
                items = objectives.get(priority, [])
                if isinstance(items, list):
                    for item in items[:3]:
                        if isinstance(item, dict):
                            goal_text = item.get("goal", item.get("name", ""))
                        else:
                            goal_text = str(item)
                        if goal_text and len(goal_text) > 5:
                            # Turn the goal into a research query
                            queries.append(
                                (f"{goal_text[:60]} best approach tools {recency}", "goals")
                            )
        except Exception as e:
            logger.debug(f"Goal-aware query generation failed: {e}")

        return queries[:4]  # Cap at 4 goal queries

    def _load_previous_urls(self) -> set:
        """Load URLs from the most recent research report for dedup."""
        urls = set()
        try:
            reports = sorted(self.report_dir.glob("research_*.json"), reverse=True)
            if reports:
                with open(reports[0]) as f:
                    data = json.load(f)
                for finding in data.get("findings", []):
                    url = finding.get("url", "")
                    if url:
                        urls.add(url)
        except Exception:
            pass
        return urls

    async def _deep_research_phase(self, report: WeeklyResearchReport):
        """
        Use Claude Code SDK (Max subscription) for deep research.

        Generates questions dynamically from high-priority web findings
        rather than asking the same static questions every week.
        """
        try:
            from atlas.tools.claude_code_sdk import ClaudeCodeSDK
            if not ClaudeCodeSDK.is_available():
                report.errors.append("Claude Code CLI not available for deep research")
                return
        except ImportError:
            report.errors.append("Claude Code SDK module not found")
            return

        sdk = ClaudeCodeSDK(model="claude-sonnet-4-6", timeout=120.0, max_turns=5)

        # Build deep research questions from ACTUAL findings, not static topics
        deep_topics = []

        # 1. Follow up on top 3 high-priority findings with deeper research
        for f in report.high_priority[:3]:
            deep_topics.append((
                f"Deep dive: {f.title}. What specifically changed, what are the technical details, "
                f"and how could an autonomous AI agent system using {f.tags[0] if f.tags else 'this technology'} "
                f"benefit? Include code examples, migration steps, or integration patterns if applicable.",
                f.tags[0] if f.tags else "general",
            ))

        # 2. Always check for breaking changes in our core stack
        now = datetime.now(timezone.utc)
        date_range = now.strftime("last 7 days of %B %Y")
        deep_topics.append((
            f"What breaking changes, deprecations, or critical updates were released in the "
            f"{date_range} for: Claude API/SDK, OpenAI API, Qwen models, Unsloth, Ollama, "
            f"or promptfoo? Only include things that would require code changes.",
            "breaking_changes",
        ))

        for topic_query, category in deep_topics[:4]:  # Cap at 4
            try:
                result = await sdk.research(topic_query, deep=False)
                if result.success and result.content:
                    finding = ResearchFinding(
                        topic=topic_query[:80],
                        source="claude_code",
                        title=f"[Deep] {category.replace('_', ' ').title()}",
                        summary=result.content[:500],
                        relevance="high",
                        action="Analyzed by Claude — see summary for specific integration steps",
                        tags=[category, "deep_research"],
                    )
                    report.findings.append(finding)
                    report.high_priority.append(finding)
                    report.search_queries_run += 1
                elif not result.success:
                    report.errors.append(f"Claude Code research failed: {result.error[:100]}")
            except Exception as e:
                report.errors.append(f"Deep research '{category}': {e}")
                logger.warning(f"Deep research failed for {category}: {e}")

            await asyncio.sleep(2.0)

    async def _search_topic(
        self, search, query: str, category: str
    ) -> List[ResearchFinding]:
        """Search a single topic and extract relevant findings."""
        findings = []

        try:
            response = await search.search(query, max_results=5)
            if not response.results:
                return findings

            for result in response.results:
                relevance = self._assess_relevance(result.title, result.snippet, category)
                if relevance == "low":
                    continue

                action = self._suggest_action(result.title, result.snippet, category)

                findings.append(ResearchFinding(
                    topic=query,
                    source=self._detect_source(result.url),
                    title=result.title,
                    summary=result.snippet[:300],
                    url=result.url,
                    relevance=relevance,
                    action=action,
                    tags=[category],
                ))
        except Exception as e:
            logger.debug(f"Search failed for '{query}': {e}")

        return findings

    def _assess_relevance(self, title: str, snippet: str, category: str) -> str:
        """Rule-based relevance scoring."""
        text = f"{title} {snippet}".lower()

        # High relevance: directly mentions our stack
        high_keywords = [
            "qwen", "unsloth", "gguf", "qlora", "lora", "axolotl",
            "claude", "anthropic", "ollama", "vllm", "phoenix", "arize",
            "promptfoo", "mcp server", "h100", "distillation",
            "claude code", "agent sdk",
        ]
        if any(kw in text for kw in high_keywords):
            return "high"

        # Medium relevance: general AI/ML advancement
        medium_keywords = [
            "fine-tune", "fine-tuning", "quantization", "reasoning",
            "open source", "release", "update", "benchmark",
            "llm", "model", "training", "inference", "deployment",
            "agent", "orchestrat", "security", "injection",
        ]
        if any(kw in text for kw in medium_keywords):
            return "medium"

        return "low"

    def _suggest_action(self, title: str, snippet: str, category: str) -> str:
        """Quick rule-based action hint (used as fallback if LLM analysis fails)."""
        text = f"{title} {snippet}".lower()

        if "release" in text or "update" in text or "new version" in text:
            return "Evaluate for upgrade"
        if "vulnerability" in text or "security" in text or "exploit" in text:
            return "Review security implications"
        if "benchmark" in text or "comparison" in text:
            return "Compare against current ATLAS performance"
        if "technique" in text or "method" in text or "approach" in text:
            return "Evaluate for integration"
        if "deprecat" in text:
            return "Check if ATLAS uses deprecated feature"

        return "Review for potential improvement"

    async def _analyze_findings(self, report: WeeklyResearchReport):
        """
        Use M2.7 (Tier 3 — background only) to analyze raw findings and generate
        specific, actionable intelligence tied to ATLAS goals.

        Replaces generic "Review for potential improvement" with concrete next steps.
        """
        if not report.findings:
            return

        # Build findings digest for the LLM
        digest_lines = []
        for i, f in enumerate(report.findings[:40]):  # cap at 40 to fit context
            digest_lines.append(
                f"{i+1}. [{f.relevance.upper()}] ({f.tags[0] if f.tags else 'general'}) "
                f"{f.title}\n   {f.summary[:200]}\n   URL: {f.url}"
            )
        findings_digest = "\n".join(digest_lines)

        # Load current goals if available
        goals_context = ""
        try:
            goals_path = Path.home() / ".atlas" / "memory" / "current_objectives.yaml"
            if goals_path.exists():
                import yaml
                with open(goals_path) as gf:
                    goals_context = f"\nCurrent objectives:\n{gf.read()[:500]}"
        except Exception:
            pass

        prompt = f"""You are ATLAS's research analyst. Analyze these findings and generate SPECIFIC action items.

ATLAS CONTEXT:
- Autonomous AI agent with 5-tier model routing (GPT 5.4 Mini/Full via OAuth, MiMo-V2-Pro, Opus 4.6, Ollama Qwen 3.5)
- Building distillation pipeline: Qwen 3.5 27B + 9B fine-tuned students via QLoRA on H100
- Using: Unsloth for training, GGUF for quantization, promptfoo for evals, Arize Phoenix for observability
- Multi-tenant system for client AI instances
- Revenue goal: $100k MRR, currently $0 — need first paying clients
- Self-evolution daemon improves routing weights, prompts, and skills overnight
- Claude Max subscription + ChatGPT subscription as zero-cost teacher models{goals_context}

RAW FINDINGS:
{findings_digest}

For each finding that matters, output a JSON array of action items:
```json
[
  {{
    "finding_index": 1,
    "action": "Specific action in 1-2 sentences — what to do, where in the codebase, expected impact",
    "category": "upgrade|security|cost_savings|new_capability|client_value|training|infrastructure",
    "effort": "quick_win|medium|major",
    "impact": "high|medium|low",
    "ties_to": "Which ATLAS goal or system this improves"
  }}
]
```

RULES:
- Skip findings that are just noise or don't apply to ATLAS's stack
- "Review for potential improvement" is BANNED — be specific or skip it
- Every action must answer: WHAT to do, WHERE in the code/system, and WHY it matters
- Prioritize: security fixes > cost savings > revenue enablers > performance > nice-to-have
- If a finding enables landing clients faster, flag it prominently
- Max 15 action items, sorted by impact"""

        try:
            # Use M2.7 via OpenRouter (Tier 3 — background analysis only)
            try:
                from atlas.core.providers.openrouter import OpenRouterProvider
            except ImportError:
                from core.providers.openrouter import OpenRouterProvider

            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                logger.warning("No OPENROUTER_API_KEY — skipping LLM analysis")
                return

            provider = OpenRouterProvider(
                api_key=api_key,
                model="minimax/minimax-m2.7",
            )
            result = await provider.complete(
                prompt=prompt,
                system="You are a research analyst for an autonomous AI agent system. Output valid JSON only.",
                temperature=0.3,
                max_tokens=3000,
            )

            if not result or not result.content:
                logger.warning("M2.7 analysis returned empty response")
                return

            # Parse the action items
            import re
            json_match = re.search(r'\[[\s\S]*\]', result.content)
            if not json_match:
                logger.warning("M2.7 response didn't contain JSON array")
                return

            actions = json.loads(json_match.group())

            # Apply the analyzed actions back to findings
            action_map = {}
            for item in actions:
                idx = item.get("finding_index")
                if idx is not None and isinstance(idx, int) and 1 <= idx <= len(report.findings):
                    action_map[idx - 1] = item

            for idx, item in action_map.items():
                f = report.findings[idx]
                f.action = item.get("action", f.action)
                effort = item.get("effort", "")
                impact = item.get("impact", "")
                ties_to = item.get("ties_to", "")
                category = item.get("category", "")

                # Enrich tags
                if category and category not in f.tags:
                    f.tags.append(category)
                if effort:
                    f.tags.append(f"effort:{effort}")
                if ties_to:
                    f.tags.append(f"goal:{ties_to}")

                # Promote to high if high-impact quick win
                if impact == "high" and f.relevance != "high":
                    f.relevance = "high"
                    if f not in report.high_priority:
                        report.high_priority.append(f)

            # Store the raw analysis for the full report
            report._action_items = actions  # type: ignore[attr-defined]

            logger.info(f"M2.7 analysis complete: {len(actions)} action items generated")

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse M2.7 action items: {e}")
        except Exception as e:
            logger.warning(f"M2.7 research analysis failed: {e}")

    def _detect_source(self, url: str) -> str:
        """Detect source platform from URL."""
        url_lower = url.lower()
        if "twitter.com" in url_lower or "x.com" in url_lower:
            return "twitter"
        if "reddit.com" in url_lower:
            return "reddit"
        if "github.com" in url_lower:
            return "github"
        if "huggingface.co" in url_lower:
            return "huggingface"
        if "arxiv.org" in url_lower:
            return "arxiv"
        return "web"

    def _save_report(self, report: WeeklyResearchReport):
        """Save report as JSON."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        report_path = self.report_dir / f"research_{date_str}.json"

        action_items = getattr(report, "_action_items", None) or []

        data = {
            "timestamp": report.timestamp,
            "total_findings": report.total_findings,
            "high_priority_count": len(report.high_priority),
            "search_queries_run": report.search_queries_run,
            "errors": report.errors,
            "action_items": action_items,
            "findings": [
                {
                    "topic": f.topic,
                    "source": f.source,
                    "title": f.title,
                    "summary": f.summary,
                    "url": f.url,
                    "relevance": f.relevance,
                    "action": f.action,
                    "tags": f.tags,
                }
                for f in report.findings
            ],
        }

        with open(report_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Research report saved: {report_path}")

    def format_telegram(self, report: WeeklyResearchReport, mode: str = "weekly") -> str:
        """Format report for Telegram — actionable intelligence, not link dumps."""
        label = "Weekly" if mode == "weekly" else "Nightly"
        lines = [f"🔬 *ATLAS {label} Research Scout*\n"]
        lines.append(f"📊 {report.total_findings} findings | {len(report.high_priority)} high priority")
        lines.append(f"🔍 {report.search_queries_run} queries searched")

        # === ACTION ITEMS (the main output) ===
        action_items = getattr(report, "_action_items", None) or []
        quick_wins = [a for a in action_items if a.get("effort") == "quick_win" and a.get("impact") in ("high", "medium")]
        strategic = [a for a in action_items if a.get("effort") != "quick_win" and a.get("impact") == "high"]

        if quick_wins:
            lines.append("\n⚡ *Quick Wins* (do today)")
            for a in quick_wins[:5]:
                cat_emoji = {
                    "security": "🛡️", "cost_savings": "💰", "client_value": "🤝",
                    "new_capability": "🚀", "upgrade": "⬆️", "training": "🧠",
                    "infrastructure": "🔧",
                }.get(a.get("category", ""), "▸")
                lines.append(f"{cat_emoji} {a['action'][:200]}")
                if a.get("ties_to"):
                    lines.append(f"   → _{a['ties_to']}_")

        if strategic:
            lines.append("\n🎯 *Strategic Actions* (this week)")
            for a in strategic[:5]:
                cat_emoji = {
                    "security": "🛡️", "cost_savings": "💰", "client_value": "🤝",
                    "new_capability": "🚀", "upgrade": "⬆️", "training": "🧠",
                    "infrastructure": "🔧",
                }.get(a.get("category", ""), "▸")
                lines.append(f"{cat_emoji} {a['action'][:200]}")
                if a.get("ties_to"):
                    lines.append(f"   → _{a['ties_to']}_")

        # === KEY FINDINGS (condensed, with real actions) ===
        if report.high_priority:
            lines.append(f"\n🔴 *Key Findings* ({len(report.high_priority)})")
            for f in report.high_priority[:8]:
                source_emoji = {
                    "twitter": "🐦", "reddit": "📱", "github": "🐙",
                    "huggingface": "🤗", "arxiv": "📄", "web": "🌐",
                    "claude_code": "🤖",
                }.get(f.source, "🌐")
                lines.append(f"{source_emoji} {f.title[:70]}")
                # Show the analyzed action, not the generic one
                if f.action and f.action != "Review for potential improvement":
                    lines.append(f"   → {f.action[:150]}")
                if f.url:
                    lines.append(f"   🔗 {f.url}")

        # === SUMMARY COUNTS ===
        medium_count = sum(1 for f in report.findings if f.relevance == "medium")
        if medium_count:
            lines.append(f"\n🟡 +{medium_count} medium-priority findings in full report")

        if report.errors:
            lines.append(f"\n⚠️ {len(report.errors)} search errors")

        lines.append(f"\n📁 Full report: data/research_reports/")
        return "\n".join(lines)


async def run_weekly_research(
    categories: List[str] = None,
    send_telegram=None,
    mode: str = "weekly",
) -> Dict[str, Any]:
    """Entry point for cron job. mode='weekly' or 'nightly'."""
    scout = WeeklyResearchScout()
    report = await scout.run_research(categories=categories, mode=mode)
    text = scout.format_telegram(report, mode=mode)

    logger.info(f"{mode.title()} research report ({len(text)} chars)")

    if send_telegram:
        try:
            await send_telegram(text)
            logger.info(f"{mode.title()} research report sent via Telegram")
        except Exception as e:
            logger.warning(f"Telegram delivery failed: {e}")

    return {
        "total_findings": report.total_findings,
        "high_priority": len(report.high_priority),
        "errors": len(report.errors),
        "report_text": text,
    }


async def run_nightly_research(
    send_telegram=None,
) -> Dict[str, Any]:
    """Entry point for nightly research cron — lighter scan."""
    return await run_weekly_research(
        send_telegram=send_telegram, mode="nightly"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ATLAS Research Scout")
    parser.add_argument("--once", action="store_true", help="Run single nightly research cycle")
    parser.add_argument("--weekly", action="store_true", help="Run full weekly research")
    args = parser.parse_args()

    if args.once or args.weekly:
        scout = WeeklyResearchScout()
        mode = "weekly" if args.weekly else "nightly"
        report = asyncio.run(scout.run_research(mode=mode))
        report_path = scout.report_dir / f"research_{datetime.now().strftime('%Y-%m-%d')}.json"
        print(f"Report saved: {report_path}")
    else:
        parser.print_help()
