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


# Research topics organized by category
# WEEKLY queries — deep scan across all categories
RESEARCH_QUERIES = {
    "claude_ecosystem": [
        "Claude Code SDK new release update changelog",
        "Anthropic Claude API new features capabilities 2026",
        "Claude Code hooks MCP integration new",
        "Anthropic agent SDK update features",
        "Claude Max subscription new features tools",
        "Claude computer use browser control update",
    ],
    "openclaw_agentic": [
        "OpenClaw AI agent framework update",
        "MetaClaw MadMax pattern agentic system",
        "autonomous AI agent framework comparison 2026",
        "agentic AGI system architecture new",
        "self-improving AI agent techniques 2026",
        "AI agent self-evolution self-healing systems",
    ],
    "models_training": [
        "Qwen model update release 2026",
        "new open source LLM release reasoning 2026",
        "Unsloth fine-tuning new features update",
        "GGUF quantization Unsloth Dynamic improvements",
        "LoRA QLoRA GRPO training techniques new",
        "distillation knowledge transfer LLM research 2026",
    ],
    "tools_infra": [
        "Ollama new release features update",
        "vLLM update inference optimization",
        "Axolotl training framework update",
        "promptfoo eval testing new release",
        "Arize Phoenix observability update",
        "LMCache prefix caching KV cache optimization",
        "MCP server new tools popular trending",
    ],
    "security_defense": [
        "prompt injection defense new techniques 2026",
        "LLM guardrails trust gate improvements",
        "AI red teaming new attack vectors defense",
        "agentic system security best practices",
    ],
    "ecosystem_trends": [
        "AI coding assistant comparison features 2026",
        "OpenAI GPT update API changes",
        "multi-agent orchestration patterns new",
        "edge AI deployment mobile GGUF optimization",
        "H100 fine-tuning optimization cost reduction",
        "x402 payment protocol blockchain AI integration",
    ],
}

# NIGHTLY queries — lighter scan, focused on breaking news and patches
NIGHTLY_QUERIES = {
    "breaking": [
        "Claude Anthropic announcement today",
        "OpenAI GPT release patch today",
        "Qwen Unsloth Ollama release today",
        "AI agent framework major update this week",
    ],
    "patches": [
        "Claude Code SDK changelog recent",
        "Ollama release notes recent",
        "promptfoo update recent",
    ],
}


class WeeklyResearchScout:
    """
    Automated research scout that monitors the AI ecosystem.

    Uses ATLAS WebSearch (Brave/Perplexity/DuckDuckGo) to find recent
    developments relevant to ATLAS's tech stack and capabilities.
    """

    def __init__(self, report_dir: str = "data/research_reports"):
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    async def run_research(
        self, categories: List[str] = None, mode: str = "weekly"
    ) -> WeeklyResearchReport:
        """
        Run research scan.

        Args:
            categories: Specific categories to scan (None = all)
            mode: "weekly" for deep scan, "nightly" for breaking news only
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

        # Select query set based on mode
        query_set = NIGHTLY_QUERIES if mode == "nightly" else RESEARCH_QUERIES
        cats = categories or list(query_set.keys())

        for category in cats:
            queries = query_set.get(category, [])
            for query in queries:
                try:
                    findings = await self._search_topic(search, query, category)
                    for f in findings:
                        report.findings.append(f)
                        if f.relevance == "high":
                            report.high_priority.append(f)
                    report.search_queries_run += 1
                except Exception as e:
                    report.errors.append(f"Query '{query}': {e}")
                    logger.warning(f"Research query failed: {query} — {e}")

                # Rate limit between queries
                await asyncio.sleep(1.0)

        # Phase 2 (weekly only): Deep analysis via Claude Code SDK
        # Uses Max subscription — zero marginal cost, gets real web browsing
        if mode == "weekly":
            await self._deep_research_phase(report)

        report.total_findings = len(report.findings)

        # Save report
        self._save_report(report)

        logger.info(
            f"{mode.title()} research complete: {report.total_findings} findings "
            f"({len(report.high_priority)} high priority), "
            f"{len(report.errors)} errors"
        )

        return report

    async def _deep_research_phase(self, report: WeeklyResearchReport):
        """
        Use Claude Code SDK (Max subscription) for deep research on high-priority findings.

        This phase:
        1. Takes the top high-priority findings from web search
        2. Asks Claude Code to do deep web research on each
        3. Specifically looks for actionable improvements to integrate into ATLAS
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

        # Deep research on curated topics that map to ATLAS improvements
        deep_topics = [
            (
                "What are the latest Claude Code SDK features, hooks, and MCP integrations "
                "released in the last 2 weeks? Include version numbers and changelog links.",
                "claude_ecosystem",
            ),
            (
                "What new agentic AI frameworks, self-improving agent architectures, or "
                "autonomous AGI systems have been released or updated recently? "
                "Focus on OpenClaw, MetaClaw, and similar projects.",
                "openclaw_agentic",
            ),
            (
                "What are the latest updates to Qwen models, Unsloth training framework, "
                "and GGUF quantization? Any new techniques for LoRA/QLoRA fine-tuning?",
                "models_training",
            ),
            (
                "What new MCP servers, AI developer tools, or LLM observability tools "
                "have been released recently that could improve an autonomous agent system?",
                "tools_infra",
            ),
        ]

        for topic_query, category in deep_topics:
            try:
                result = await sdk.research(topic_query, deep=False)
                if result.success and result.content:
                    finding = ResearchFinding(
                        topic=topic_query[:80],
                        source="claude_code",
                        title=f"[Deep] {category.replace('_', ' ').title()}",
                        summary=result.content[:500],
                        relevance="high",
                        action="Review for integration into ATLAS",
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

            await asyncio.sleep(2.0)  # Rate limit between Claude Code calls

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
        """Suggest what ATLAS should do about this finding."""
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

        data = {
            "timestamp": report.timestamp,
            "total_findings": report.total_findings,
            "high_priority_count": len(report.high_priority),
            "search_queries_run": report.search_queries_run,
            "errors": report.errors,
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
        """Format report for Telegram delivery."""
        label = "Weekly" if mode == "weekly" else "Nightly"
        lines = [f"🔬 *ATLAS {label} Research Scout*\n"]
        lines.append(f"📊 {report.total_findings} findings | {len(report.high_priority)} high priority")
        lines.append(f"🔍 {report.search_queries_run} queries searched")

        if report.high_priority:
            lines.append("\n🔴 *High Priority*")
            for f in report.high_priority[:10]:
                source_emoji = {
                    "twitter": "🐦", "reddit": "📱", "github": "🐙",
                    "huggingface": "🤗", "arxiv": "📄", "web": "🌐",
                }.get(f.source, "🌐")
                lines.append(f"{source_emoji} *{f.title[:80]}*")
                lines.append(f"   ↳ {f.action}")
                if f.url:
                    lines.append(f"   🔗 {f.url}")

        if report.findings:
            medium = [f for f in report.findings if f.relevance == "medium"]
            if medium:
                lines.append(f"\n🟡 *Medium Priority* ({len(medium)} findings)")
                for f in medium[:5]:
                    lines.append(f"• {f.title[:80]}")
                    lines.append(f"  ↳ {f.action}")

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
