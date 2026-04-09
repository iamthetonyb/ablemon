"""
Weekly Research Scout — Monitors AI ecosystem for improvements.

Searches Twitter/X, Reddit, GitHub releases, HuggingFace, and AI news
for relevant developments that could improve ABLE. Generates a
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
    action: str = ""  # What ABLE should do about it
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


# Base TOPICS (not queries) — the research interests ABLE cares about.
# Queries are generated dynamically each run with date context and rotation.
RESEARCH_TOPICS = {
    "claude_ecosystem": {
        "keywords": ["Claude Code SDK", "Anthropic Claude API", "Claude MCP", "Anthropic agent SDK", "Claude Max", "Claude computer use"],
        "why": "Our T4 provider and CLI tooling — breaking changes or new features directly affect us",
    },
    "agentic_systems": {
        "keywords": ["autonomous AI agent", "self-improving AI", "agentic AGI", "AI agent orchestration", "multi-agent framework"],
        "why": "Competitive landscape and techniques we can adopt for ABLE evolution daemon",
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

    Uses ABLE WebSearch (Brave/Perplexity/DuckDuckGo) to find recent
    developments relevant to ABLE's tech stack and capabilities.
    """

    def __init__(self, report_dir: str = "data/research_reports"):
        self.report_dir = Path(report_dir)
        self.operator_report_dir = Path.home() / ".able" / "reports" / "research"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.operator_report_dir.mkdir(parents=True, exist_ok=True)

    async def run_research(
        self, categories: List[str] = None, mode: str = "weekly"
    ) -> WeeklyResearchReport:
        """
        Run research scan with dynamic query generation.

        Each run generates fresh queries based on:
        - Current date (for recency in search results)
        - Topic rotation (nightly scans different topics each night)
        - Previous findings (dedup against last report)
        - Current ABLE goals/objectives (goal-aware queries)
        """
        from able.core.observability.tracer import trace_operation

        report = WeeklyResearchReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Initialize web search
        try:
            from able.tools.search.web_search import WebSearch
            search = WebSearch()
        except Exception as e:
            report.errors.append(f"WebSearch unavailable: {e}")
            logger.error(f"WebSearch init failed: {e}")
            return report

        # Generate dynamic queries
        queries = self._generate_queries(mode, categories)
        previous_urls = self._load_previous_urls()

        with trace_operation(
            f"research.{mode}",
            attributes={
                "research.mode": mode,
                "research.query_count": len(queries),
                "research.categories": str(categories or "all"),
            },
            tracer_name="able.research",
        ) as span:
            for query, category in queries:
                try:
                    findings = await self._search_topic(search, query, category)
                    for f in findings:
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

            # Phase 2.5: XCrawl deep extraction for high-priority findings
            await self._xcrawl_enrich(report)

            # Phase 3: LLM analysis — turn raw findings into actionable intelligence
            if report.findings:
                await self._analyze_findings(report)

            # Phase 3.5: Source verification (Feynman pattern)
            await self._verify_sources(report)

            # Phase 3.7: Build knowledge graph from findings
            await self._build_knowledge_graph(report)

            # Save report
            await self._save_report(report)

            span.set_attribute("research.total_findings", report.total_findings)
            span.set_attribute("research.high_priority", len(report.high_priority))
            span.set_attribute("research.queries_run", report.search_queries_run)
            span.set_attribute("research.errors", len(report.errors))

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
        Karpathy cumulative research: generate queries that BUILD on previous findings.

        Each run:
        1. Prioritizes topics that haven't been searched recently (staleness rotation)
        2. Generates follow-up queries from past high-priority findings
        3. Explores open questions identified by previous M2.7 analysis
        4. Falls back to keyword rotation for breadth coverage

        Returns list of (query, category).
        """
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%B %Y")
        recency = "this week" if mode == "nightly" else "this month"

        # Load the research frontier from past reports
        frontier = self._load_research_frontier()

        queries = []

        # === Phase 1: Follow-up queries from previous high-value findings ===
        # This is the Karpathy pattern — each run digs deeper into what the last run found
        for thread in frontier["high_value_threads"][:4]:
            follow_up = (
                f"{thread['title']} latest developments implementation guide {recency}"
            )
            tag = thread["tags"][0] if thread["tags"] else "general"
            queries.append((follow_up, tag))

        # === Phase 2: Explore open questions from past analysis ===
        for q in frontier["open_questions"][:3]:
            queries.append((
                f"{q['question'][:60]} how to approach {date_str}",
                q["category"] or "general",
            ))

        # === Phase 3: Stale topic rotation (prioritize topics not searched recently) ===
        all_topics = list(RESEARCH_TOPICS.keys())
        if categories:
            selected_topics = [t for t in categories if t in RESEARCH_TOPICS]
        elif mode == "nightly":
            # Sort topics by staleness — search least-recently-touched first
            topic_staleness = []
            for t in all_topics:
                days = frontier["topic_freshness"].get(t, 999)  # never searched = most stale
                topic_staleness.append((days, t))
            topic_staleness.sort(reverse=True)  # Most stale first
            selected_topics = [t for _, t in topic_staleness[:NIGHTLY_TOPIC_COUNT]]
        else:
            selected_topics = all_topics

        for topic_name in selected_topics:
            topic = RESEARCH_TOPICS[topic_name]
            keywords = topic["keywords"]

            if mode == "nightly":
                # 1 query per topic (reduced — we have follow-up queries now)
                kw = keywords[now.timetuple().tm_yday % len(keywords)]
                queries.append((f"{kw} new release update {recency}", topic_name))
            else:
                # 2 queries per topic (reduced from 3 — follow-ups cover depth)
                for kw in keywords[:2]:
                    queries.append((f"{kw} {date_str} latest", topic_name))

        # === Phase 4: Goal-aware queries ===
        goal_queries = self._generate_goal_queries(recency)
        queries.extend(goal_queries)

        # === Phase 5: System evolution queries (auto-discovers new ABLE components) ===
        # This is the "research grows WITH the system" pattern — when we add a new
        # tool, provider, or module, research automatically picks it up and looks
        # for improvements, alternatives, and best practices.
        evo_queries = self._generate_system_evolution_queries(recency)
        queries.extend(evo_queries)

        # === Phase 6: Improvement/growth queries from past learnings ===
        # Mine ABLE's own learnings.md for growth opportunities
        growth_queries = self._generate_growth_queries(recency)
        queries.extend(growth_queries)

        # Log frontier state for debugging
        logger.info(
            "Research frontier: %d explored topics, %d follow-up threads, "
            "%d open questions, %d system-evo queries, %d total queries generated",
            len(frontier["explored_topics"]),
            len(frontier["high_value_threads"]),
            len(frontier["open_questions"]),
            len(evo_queries),
            len(queries),
        )

        return queries

    def _generate_goal_queries(self, recency: str) -> List[tuple]:
        """Generate queries based on current ABLE objectives."""
        queries = []
        try:
            goals_path = Path.home() / ".able" / "memory" / "current_objectives.yaml"
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

    def _generate_system_evolution_queries(self, recency: str) -> List[tuple]:
        """
        Auto-research: generate queries based on ABLE's actual current capabilities.

        Scans the codebase for tools, providers, and modules — then generates
        research queries about improvements, alternatives, and best practices
        for each component. This makes research evolve WITH the system.
        """
        queries = []
        project_root = Path(__file__).parent.parent.parent.parent

        # 1. Discover active providers from routing config
        try:
            import yaml
            config_path = project_root / "config" / "routing_config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    config = yaml.safe_load(f) or {}
                for p in config.get("providers", []):
                    if p.get("enabled", True):
                        model = p.get("model_id", "")
                        if model:
                            queries.append((
                                f"{model} performance benchmarks alternatives {recency}",
                                "models_training",
                            ))
        except Exception:
            pass

        # 2. Discover active tools from skill index
        try:
            import yaml
            skill_path = project_root / "able" / "skills" / "SKILL_INDEX.yaml"
            if skill_path.exists():
                with open(skill_path) as f:
                    skills = yaml.safe_load(f) or {}
                # Research improvements for most-used skills
                for name, info in (skills.get("skills") or {}).items():
                    count = info.get("use_count", 0)
                    if count > 0:
                        queries.append((
                            f"AI agent {name.replace('-', ' ')} skill best practices {recency}",
                            "agentic_systems",
                        ))
        except Exception:
            pass

        # 3. Discover new modules added since last scan
        try:
            module_dirs = [
                project_root / "able" / "core",
                project_root / "able" / "tools",
            ]
            new_modules = set()
            for d in module_dirs:
                if d.exists():
                    for py_file in d.rglob("*.py"):
                        if py_file.stat().st_mtime > (datetime.now().timestamp() - 7 * 86400):
                            module_name = py_file.stem
                            if module_name != "__init__" and module_name not in new_modules:
                                new_modules.add(module_name)

            # Research best practices for recently added modules
            for mod in list(new_modules)[:3]:
                clean_name = mod.replace("_", " ")
                queries.append((
                    f"AI agent {clean_name} implementation best practices {recency}",
                    "agentic_systems",
                ))
        except Exception:
            pass

        return queries[:5]  # Cap to avoid explosion

    def _generate_growth_queries(self, recency: str) -> List[tuple]:
        """
        Mine ABLE's own learnings, errors, and audit results for growth opportunities.

        This closes the loop: ABLE encounters problems → logs them → research
        finds solutions → solutions get implemented → new problems emerge.
        """
        queries = []
        project_root = Path(__file__).parent.parent.parent.parent

        # 1. Mine learnings.md for recurring patterns worth researching
        try:
            learnings_path = Path.home() / ".able" / "memory" / "learnings.md"
            if learnings_path.exists():
                content = learnings_path.read_text()[:3000]
                # Look for error patterns, tool mentions, improvement areas
                import re
                # Extract lines with "error", "issue", "improve", "bug", "fix"
                problem_lines = [
                    line.strip("- ").strip()
                    for line in content.split("\n")
                    if any(kw in line.lower() for kw in ["error", "issue", "improve", "bug", "fix", "slow", "fail"])
                    and len(line.strip()) > 15
                ]
                for problem in problem_lines[:2]:
                    queries.append((
                        f"AI agent solution for {problem[:50]} {recency}",
                        "agentic_systems",
                    ))
        except Exception:
            pass

        # 2. Mine recent audit failures for improvement research
        try:
            import sqlite3
            db_path = project_root / "data" / "interaction_log.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                # Find domains with low audit scores
                rows = conn.execute(
                    "SELECT domain, AVG(audit_score) as avg, COUNT(*) as cnt "
                    "FROM interaction_log WHERE audit_score IS NOT NULL "
                    "GROUP BY domain HAVING avg < 3.0 AND cnt > 2 "
                    "ORDER BY avg ASC LIMIT 3"
                ).fetchall()
                conn.close()

                for row in rows:
                    domain = row["domain"] or "general"
                    queries.append((
                        f"AI agent {domain} response quality improvement techniques {recency}",
                        domain if domain in RESEARCH_TOPICS else "agentic_systems",
                    ))
        except Exception:
            pass

        # 3. Check for recently added skills that might have improvement docs
        try:
            import yaml
            skill_path = project_root / "able" / "skills" / "SKILL_INDEX.yaml"
            if skill_path.exists():
                with open(skill_path) as f:
                    skills = yaml.safe_load(f) or {}
                # Find recently created skills (last 14 days)
                now = datetime.now(timezone.utc)
                for name, info in (skills.get("skills") or {}).items():
                    created = info.get("created", "")
                    if created:
                        try:
                            created_dt = datetime.fromisoformat(created)
                            if (now - created_dt.replace(tzinfo=timezone.utc)).days < 14:
                                queries.append((
                                    f"AI {name.replace('-', ' ')} skill optimization best practices",
                                    "agentic_systems",
                                ))
                        except Exception:
                            pass
        except Exception:
            pass

        return queries[:4]  # Cap growth queries

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

    def _load_research_frontier(self) -> dict:
        """
        Karpathy cumulative research: load the knowledge frontier from past reports.

        Returns a dict with:
          - explored_topics: set of topics already deeply researched
          - open_questions: list of unanswered questions / gaps from previous runs
          - high_value_threads: findings worth following up on
          - topic_freshness: {topic: days_since_last_searched} for rotation
        """
        frontier = {
            "explored_topics": set(),
            "open_questions": [],
            "high_value_threads": [],
            "topic_freshness": {},
        }

        try:
            reports = sorted(self.report_dir.glob("research_*.json"), reverse=True)
            now = datetime.now(timezone.utc)

            for report_file in reports[:10]:  # Look at last 10 reports
                try:
                    data = json.load(open(report_file))
                except Exception:
                    continue

                report_ts = data.get("timestamp", "")
                try:
                    report_dt = datetime.fromisoformat(report_ts.replace("Z", "+00:00"))
                    days_ago = (now - report_dt).days
                except Exception:
                    days_ago = 30

                # Track explored topics
                for finding in data.get("findings", []):
                    for tag in finding.get("tags", []):
                        if tag in RESEARCH_TOPICS:
                            frontier["explored_topics"].add(tag)
                            # Track freshness
                            if tag not in frontier["topic_freshness"] or days_ago < frontier["topic_freshness"][tag]:
                                frontier["topic_freshness"][tag] = days_ago

                # Extract high-value threads to follow up on
                for finding in data.get("findings", []):
                    if finding.get("relevance") == "high" and days_ago < 14:
                        action = finding.get("action", "")
                        title = finding.get("title", "")
                        if action and action != "Review for potential improvement":
                            frontier["high_value_threads"].append({
                                "title": title[:80],
                                "action": action[:150],
                                "tags": finding.get("tags", []),
                                "days_ago": days_ago,
                            })

                # Extract open questions from action items
                for item in data.get("action_items", []):
                    effort = item.get("effort", "")
                    if effort in ("medium", "major"):
                        frontier["open_questions"].append({
                            "question": item.get("action", "")[:150],
                            "category": item.get("category", ""),
                            "days_ago": days_ago,
                        })

        except Exception as e:
            logger.debug("Frontier loading failed: %s", e)

        # Deduplicate threads by title prefix
        seen_titles = set()
        unique_threads = []
        for t in frontier["high_value_threads"]:
            key = t["title"][:30].lower()
            if key not in seen_titles:
                seen_titles.add(key)
                unique_threads.append(t)
        frontier["high_value_threads"] = unique_threads[:10]

        # Deduplicate questions
        seen_q = set()
        unique_q = []
        for q in frontier["open_questions"]:
            key = q["question"][:40].lower()
            if key not in seen_q:
                seen_q.add(key)
                unique_q.append(q)
        frontier["open_questions"] = unique_q[:8]

        return frontier

    async def _xcrawl_enrich(self, report: WeeklyResearchReport):
        """
        Use XCrawl to get full structured content for high-priority findings.

        Addresses the "partial context problem" — instead of relying on search
        snippets, we get the full document content for important findings.
        """
        try:
            from able.tools.xcrawl.client import XCrawlClient
        except ImportError:
            return

        async with XCrawlClient() as client:
            if not client.is_available:
                return

            enriched = 0
            for finding in report.high_priority[:5]:
                if not finding.url:
                    continue
                try:
                    result = await client.scrape(finding.url)
                    if result.markdown and not result.error:
                        # Extend summary with full content (capped)
                        full_text = result.markdown[:2000]
                        if len(full_text) > len(finding.summary) * 2:
                            finding.summary = full_text
                            enriched += 1
                except Exception as e:
                    logger.debug("XCrawl enrich failed for %s: %s", finding.url, e)

            if enriched:
                logger.info("XCrawl enriched %d high-priority findings", enriched)

    async def _verify_sources(self, report: WeeklyResearchReport):
        """
        Feynman source-grounding: verify all high-priority finding citations.

        Tags findings with #verified, #unverified, #broken-link, or #contested.
        """
        try:
            from able.core.evolution.source_grounder import SourceGrounder
        except ImportError:
            return

        grounder = SourceGrounder()
        findings_dicts = [
            {
                "title": f.title,
                "summary": f.summary,
                "url": f.url,
                "relevance": f.relevance,
                "tags": f.tags,
            }
            for f in report.high_priority
        ]

        if not findings_dicts:
            return

        try:
            verifications = await grounder.verify_findings(findings_dicts)
            # Apply verification tags back to findings
            for finding, vr in zip(report.high_priority, verifications):
                if vr.verification_tag not in finding.tags:
                    finding.tags.append(f"#{vr.verification_tag}")

            verified = sum(1 for v in verifications if v.verification_tag == "verified")
            broken = sum(1 for v in verifications if v.verification_tag == "broken-link")
            logger.info(
                "Source verification: %d verified, %d broken-link, %d total",
                verified, broken, len(verifications),
            )
        except Exception as e:
            logger.debug("Source verification failed: %s", e)

    async def _build_knowledge_graph(self, report: WeeklyResearchReport):
        """Build and export knowledge graph from research findings.

        Generates:
        - Interactive D3 HTML visualization (data/research_graph.html)
        - JSON export for semantic indexing (data/research_graph.json)
        - Files the HTML visualization to Trilium as a visual note
        """
        try:
            from able.tools.graphify.builder import build_research_graph
        except ImportError:
            return

        findings_dicts = [
            {
                "title": f.title,
                "summary": f.summary,
                "url": f.url,
                "source": f.source,
                "tags": f.tags,
                "relevance": f.relevance,
                "action": f.action,
            }
            for f in (report.high_priority + report.findings)
        ]

        if len(findings_dicts) < 3:
            return

        try:
            export = await build_research_graph(findings_dicts)
            if export and export.stats:
                logger.info(
                    "Knowledge graph: %d nodes, %d edges, %d communities",
                    export.stats.get("node_count", 0),
                    export.stats.get("edge_count", 0),
                    export.stats.get("community_count", 0),
                )

                # File the interactive graph to Trilium as a visual note
                await self._file_graph_to_trilium(export)
        except Exception as e:
            logger.debug("Knowledge graph build failed: %s", e)

    async def _file_graph_to_trilium(self, export):
        """Upload graphify D3 HTML visualization + mermaid to Trilium."""
        try:
            from able.tools.trilium.client import TriliumClient, ensure_parent
            from pathlib import Path as _Path

            async with TriliumClient() as client:
                if not await client.is_available():
                    return

                parent_id = await ensure_parent(client, "weekly_research")
                if not parent_id:
                    logger.warning("Cannot file graph to Trilium — parent note missing")
                    return

                date_str = datetime.now().strftime("%Y-%m-%d")

                # Upload D3 HTML visualization
                html_path = _Path(__file__).parent.parent.parent.parent / "data" / "research_graph.html"
                if html_path.exists():
                    html_content = html_path.read_text(encoding="utf-8")
                    await client.create_note(
                        parent_id,
                        f"Knowledge Graph — {date_str}",
                        html_content,
                        note_type="render",
                        mime="text/html",
                    )
                    logger.info("Filed interactive knowledge graph to Trilium")

                # Upload mermaid version for in-note rendering
                if export.mermaid:
                    await client.create_note(
                        parent_id,
                        f"Graph (Mermaid) — {date_str}",
                        export.mermaid,
                        note_type="mermaid",
                        mime="text/mermaid",
                    )

        except Exception as e:
            logger.debug("Graph Trilium filing failed (non-fatal): %s", e)

    async def _deep_research_phase(self, report: WeeklyResearchReport):
        """
        Use Claude Code SDK (Max subscription) for deep research.

        Generates questions dynamically from high-priority web findings
        rather than asking the same static questions every week.
        """
        try:
            from able.tools.claude_code_sdk import ClaudeCodeSDK
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
            return "Compare against current ABLE performance"
        if "technique" in text or "method" in text or "approach" in text:
            return "Evaluate for integration"
        if "deprecat" in text:
            return "Check if ABLE uses deprecated feature"

        return "Review for potential improvement"

    async def _analyze_findings(self, report: WeeklyResearchReport):
        """
        Use M2.7 (Tier 3 — background only) to analyze raw findings and generate
        specific, actionable intelligence tied to ABLE goals.

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
            goals_path = Path.home() / ".able" / "memory" / "current_objectives.yaml"
            if goals_path.exists():
                import yaml
                with open(goals_path) as gf:
                    goals_context = f"\nCurrent objectives:\n{gf.read()[:500]}"
        except Exception:
            pass

        prompt = f"""You are ABLE's research analyst. Analyze these findings and generate SPECIFIC action items.

ABLE CONTEXT:
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
    "ties_to": "Which ABLE goal or system this improves"
  }}
]
```

RULES:
- Skip findings that are just noise or don't apply to ABLE's stack
- "Review for potential improvement" is BANNED — be specific or skip it
- Every action must answer: WHAT to do, WHERE in the code/system, and WHY it matters
- Prioritize: security fixes > cost savings > revenue enablers > performance > nice-to-have
- If a finding enables landing clients faster, flag it prominently
- Max 15 action items, sorted by impact"""

        try:
            # Use M2.7 via OpenRouter (Tier 3 — background analysis only)
            try:
                from able.core.providers.openrouter import OpenRouterProvider
            except ImportError:
                from able.core.providers.openrouter import OpenRouterProvider

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

    def _report_payload(self, report: WeeklyResearchReport) -> Dict[str, Any]:
        """Serialize the report for JSON persistence."""
        action_items = getattr(report, "_action_items", None) or []
        return {
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

    def _render_markdown_report(self, report: WeeklyResearchReport) -> str:
        """Generate a readable local report with action items first."""
        payload = self._report_payload(report)
        lines = [
            "# ABLE Research Report",
            "",
            f"- Timestamp: {payload['timestamp']}",
            f"- Findings: {payload['total_findings']}",
            f"- High priority: {payload['high_priority_count']}",
            f"- Queries run: {payload['search_queries_run']}",
            "",
        ]

        action_items = payload.get("action_items") or []
        if action_items:
            lines.append("## Action Items")
            lines.append("")
            for idx, item in enumerate(action_items, 1):
                ties_to = f" [{item.get('ties_to')}]" if item.get("ties_to") else ""
                impact = item.get("impact", "unknown")
                effort = item.get("effort", "unknown")
                lines.append(f"{idx}. {item.get('action', 'No action text')}{ties_to}")
                lines.append(f"   - Category: {item.get('category', 'general')}")
                lines.append(f"   - Impact: {impact}")
                lines.append(f"   - Effort: {effort}")
                if item.get("source_title"):
                    lines.append(f"   - Source: {item['source_title']}")
                if item.get("url"):
                    lines.append(f"   - URL: {item['url']}")
            lines.append("")

        high_priority = [f for f in payload["findings"] if f["relevance"] == "high"]
        if high_priority:
            lines.append("## High Priority Findings")
            lines.append("")
            for finding in high_priority:
                lines.append(f"- {finding['title']}")
                if finding.get("action"):
                    lines.append(f"  - Action: {finding['action']}")
                if finding.get("url"):
                    lines.append(f"  - URL: {finding['url']}")
            lines.append("")

        if payload["errors"]:
            lines.append("## Errors")
            lines.append("")
            for error in payload["errors"]:
                lines.append(f"- {error}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    async def _save_report(self, report: WeeklyResearchReport):
        """Save report JSON plus an operator-facing markdown summary."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        payload = self._report_payload(report)
        markdown = self._render_markdown_report(report)

        json_paths = [
            self.report_dir / f"research_{date_str}.json",
            self.operator_report_dir / f"research_{date_str}.json",
            self.operator_report_dir / "latest.json",
        ]
        markdown_paths = [
            self.operator_report_dir / f"research_{date_str}.md",
            self.operator_report_dir / "latest.md",
        ]

        for path in json_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)

        for path in markdown_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")

        logger.info(
            "Research report saved: %s and %s",
            json_paths[0],
            self.operator_report_dir / "latest.md",
        )

        # Index findings for OMEGA-style semantic search (scales wiki queries)
        self._index_findings(report, date_str)

        # File findings to TriliumNext knowledge base (with rich note types)
        await self._file_to_trilium(report, date_str, payload)

    def _index_findings(self, report: WeeklyResearchReport, date_str: str):
        """Index all findings in the research semantic search index."""
        try:
            from able.memory.research_index import ResearchIndex
            index = ResearchIndex()
            all_findings = report.high_priority + report.findings
            for f in all_findings:
                index.add_finding(
                    title=f.title,
                    summary=f.summary,
                    url=f.url,
                    tags=f.tags,
                    date_added=date_str,
                    source=f.source,
                    relevance=f.relevance,
                )
            logger.info("Indexed %d findings in research search index", len(all_findings))
        except Exception as e:
            logger.debug("Research index update failed (non-fatal): %s", e)

    async def _file_to_trilium(
        self, report: WeeklyResearchReport, date_str: str, payload: dict
    ):
        """
        File research findings to Trilium with rich note types.

        Creates:
        1. Individual finding notes with cross-references
        2. Mermaid topic-relationship diagram
        3. Summary dashboard note
        Uses wiki_ingest_research() for full cross-referencing + web clipper linking.
        """
        try:
            from able.tools.trilium.client import TriliumClient, KNOWN_PARENTS, ensure_parent
            from able.tools.trilium.wiki_skill import wiki_ingest_research

            async with TriliumClient() as client:
                if not await client.is_available():
                    logger.debug("Trilium not available, skipping filing")
                    return

                parent_id = await ensure_parent(client, "weekly_research")
                if not parent_id:
                    logger.warning(
                        "TRILIUM_WEEKLY_RESEARCH not set and auto-create failed — "
                        "research findings will NOT be filed to Trilium"
                    )
                    return

                findings = report.high_priority + report.findings
                if not findings:
                    return

                # 1. Use wiki_ingest_research for structured ingestion with
                #    cross-references, relation building, and web clipper linking
                ingest_data = {
                    "findings": [
                        {
                            "title": f.title,
                            "summary": f.summary,
                            "url": f.url,
                            "action": f.action,
                            "tags": f.tags,
                            "relevance": f.relevance,
                            "source": f.source,
                        }
                        for f in findings[:20]
                    ],
                    "high_priority_count": len(report.high_priority),
                    "search_queries_run": report.search_queries_run,
                }
                result = await wiki_ingest_research(ingest_data, date_str)
                logger.info("Trilium ingestion: %s", result)

                # 2. Generate mermaid topic-relationship diagram
                mermaid = self._generate_topic_mermaid(findings, date_str)
                if mermaid:
                    try:
                        await client.create_note(
                            parent_id,
                            f"Topic Map — {date_str}",
                            mermaid,
                            note_type="mermaid",
                            mime="text/mermaid",
                        )
                        logger.info("Created Trilium mermaid topic map")
                    except Exception as e:
                        logger.debug("Mermaid note creation failed: %s", e)

                # 3. Create summary dashboard note
                dashboard_html = self._generate_dashboard_html(payload, date_str)
                try:
                    await client.create_note(
                        parent_id,
                        f"Dashboard — {date_str}",
                        dashboard_html,
                    )
                except Exception as e:
                    logger.debug("Dashboard note creation failed: %s", e)

        except ImportError:
            logger.debug("Trilium client not available, skipping filing")
        except Exception as e:
            logger.warning("Trilium filing failed (non-fatal): %s", e)

    def _generate_topic_mermaid(
        self, findings: list, date_str: str
    ) -> str:
        """Generate a mermaid flowchart showing topic relationships from findings."""
        if not findings:
            return ""

        # Build tag → findings mapping
        tag_findings: Dict[str, List[str]] = {}
        for f in findings:
            tags = f.tags if hasattr(f, "tags") else []
            for tag in tags:
                tag_findings.setdefault(tag, []).append(
                    f.title[:40] if hasattr(f, "title") else str(f)[:40]
                )

        if not tag_findings:
            return ""

        lines = [f"graph TD"]
        lines.append(f'    R["Research {date_str}"]')

        node_id = 0
        tag_nodes = {}
        for tag, titles in tag_findings.items():
            tag_node = f"T{node_id}"
            tag_nodes[tag] = tag_node
            safe_tag = tag.replace('"', "'")
            count = len(titles)
            lines.append(f'    {tag_node}["{safe_tag} ({count})"]')
            lines.append(f"    R --> {tag_node}")
            node_id += 1

            for title in titles[:3]:
                finding_node = f"F{node_id}"
                safe_title = title.replace('"', "'").replace("\n", " ")
                lines.append(f'    {finding_node}["{safe_title}"]')
                lines.append(f"    {tag_node} --> {finding_node}")
                node_id += 1

        # Cross-link tags that share findings
        tag_list = list(tag_findings.keys())
        for i, t1 in enumerate(tag_list):
            for t2 in tag_list[i + 1:]:
                shared = set(tag_findings[t1]) & set(tag_findings[t2])
                if shared:
                    lines.append(
                        f"    {tag_nodes[t1]} -.-> {tag_nodes[t2]}"
                    )

        return "\n".join(lines)

    def _generate_dashboard_html(self, payload: dict, date_str: str) -> str:
        """Generate an HTML summary dashboard for Trilium."""
        findings = payload.get("findings", [])
        high = sum(1 for f in findings if f.get("relevance") == "high")
        medium = sum(1 for f in findings if f.get("relevance") == "medium")
        low = sum(1 for f in findings if f.get("relevance") == "low")

        # Category breakdown
        categories: Dict[str, int] = {}
        for f in findings:
            for tag in f.get("tags", []):
                categories[tag] = categories.get(tag, 0) + 1

        cat_rows = "".join(
            f"<tr><td>{cat}</td><td>{cnt}</td></tr>"
            for cat, cnt in sorted(categories.items(), key=lambda x: -x[1])
        )

        action_items = payload.get("action_items", [])
        action_rows = "".join(
            f"<tr><td>{a.get('action', '')[:100]}</td>"
            f"<td>{a.get('impact', '?')}</td>"
            f"<td>{a.get('effort', '?')}</td></tr>"
            for a in action_items[:10]
        )

        return f"""<h2>Research Dashboard — {date_str}</h2>
<h3>Summary</h3>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Total Findings</td><td>{payload.get('total_findings', 0)}</td></tr>
<tr><td>High Priority</td><td>{high}</td></tr>
<tr><td>Medium</td><td>{medium}</td></tr>
<tr><td>Low</td><td>{low}</td></tr>
<tr><td>Queries Run</td><td>{payload.get('search_queries_run', 0)}</td></tr>
<tr><td>Errors</td><td>{len(payload.get('errors', []))}</td></tr>
</table>
<h3>By Category</h3>
<table><tr><th>Category</th><th>Findings</th></tr>{cat_rows}</table>
{"<h3>Action Items</h3><table><tr><th>Action</th><th>Impact</th><th>Effort</th></tr>" + action_rows + "</table>" if action_rows else ""}"""

    def format_telegram(self, report: WeeklyResearchReport, mode: str = "weekly") -> str:
        """Format report for Telegram — actionable intelligence, not link dumps."""
        label = "Weekly" if mode == "weekly" else "Nightly"
        lines = [f"🔬 *ABLE {label} Research Scout*\n"]
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

        lines.append(f"\n📁 Latest report: ~/.able/reports/research/latest.md")
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
