"""
Prompt Enricher — Expands vague "flavor words" into domain-specific actionable criteria.

Sits in the pipeline between security scanning and complexity scoring:
  User Input → Scanner → Auditor → **PromptEnricher** → ComplexityScorer → Model

Design principles:
  - Rule-based for known patterns (0ms, $0) — no LLM call needed
  - Domain-aware: "robust" means different things for code vs content vs security
  - Knows when NOT to enrich (simple questions, greetings, system commands)
  - References available skills, tools, and memories
  - Enrichment is additive — never removes user intent
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentResult:
    """Result of prompt enrichment."""
    original: str
    enriched: str
    domain: str
    flavor_words_found: List[str]
    criteria_added: List[str]
    enrichment_level: str  # "none", "light", "standard", "deep"
    skip_reason: Optional[str] = None


# ── Domain Detection ──────────────────────────────────────────────

DOMAIN_SIGNALS = {
    "content": {
        "keywords": [
            "blog", "article", "post", "video", "podcast", "content",
            "youtube", "tiktok", "instagram", "reel", "thumbnail",
            "script", "storyboard", "shoot", "production", "filming",
            "photo", "image", "graphic", "visual", "animation",
        ],
        "weight": 1.0,
    },
    "copywriting": {
        "keywords": [
            "copy", "email", "headline", "subject line", "ad", "landing page",
            "cta", "pitch", "outreach", "dm", "cold", "follow-up", "sales",
            "newsletter", "campaign", "announce", "launch",
            "blog", "article", "post", "whitepaper", "press release",
        ],
        "weight": 1.2,  # Boost to win over content for text-based tasks
    },
    "code": {
        "keywords": [
            "code", "function", "class", "api", "endpoint", "database",
            "implement", "build", "debug", "refactor", "test", "deploy",
            "backend", "frontend", "server", "client", "sdk", "library",
            "microservice", "pipeline", "ci/cd", "docker", "kubernetes",
        ],
        "weight": 1.0,
    },
    "security": {
        "keywords": [
            "security", "auth", "authentication", "authorization", "encrypt",
            "vulnerability", "owasp", "pentest", "audit", "firewall",
            "injection", "xss", "csrf", "token", "jwt", "oauth", "rbac",
            "compliance", "gdpr", "hipaa", "pci", "soc2",
        ],
        "weight": 1.2,
    },
    "design": {
        "keywords": [
            "ui", "ux", "design", "layout", "component", "dashboard",
            "wireframe", "mockup", "prototype", "figma", "responsive",
            "mobile", "theme", "color", "typography", "icon", "animation",
            "accessibility", "wcag", "dark mode", "light mode",
        ],
        "weight": 1.0,
    },
    "data": {
        "keywords": [
            "data", "analytics", "metrics", "chart", "graph", "report",
            "sql", "query", "etl", "pipeline", "dashboard", "visualization",
            "csv", "json", "parquet", "warehouse", "bigquery", "postgres",
        ],
        "weight": 1.0,
    },
    "strategy": {
        "keywords": [
            "strategy", "plan", "roadmap", "architecture", "system design",
            "tradeoff", "decision", "evaluate", "compare", "recommend",
            "migration", "scaling", "growth", "budget", "timeline",
        ],
        "weight": 1.0,
    },
    "research": {
        "keywords": [
            "research", "investigate", "analyze", "study", "survey",
            "benchmark", "competitor", "market", "trend", "report",
            "white paper", "case study", "literature",
        ],
        "weight": 1.0,
    },
}

# ── Flavor Word Expansions (Domain-Specific) ─────────────────────

# Each flavor word maps to domain-specific criteria that replace the vague word
# with concrete, measurable requirements.

FLAVOR_EXPANSIONS: Dict[str, Dict[str, str]] = {
    "robust": {
        "code": (
            "with comprehensive error handling, input validation, type safety, "
            "edge case coverage, retry logic with exponential backoff, graceful "
            "degradation, structured logging, and defensive programming patterns"
        ),
        "security": (
            "with defense-in-depth architecture, OWASP Top 10 coverage, zero-trust "
            "principles, secrets rotation, rate limiting, audit trail, encryption at "
            "rest and in transit, and incident response hooks"
        ),
        "content": (
            "with professional production value — 4K resolution, 3-point lighting setup, "
            "color-graded footage, professional audio (lavalier + shotgun mic), B-roll "
            "coverage, motion graphics, proper aspect ratios per platform, and SEO-optimized "
            "metadata (titles, descriptions, tags, thumbnails)"
        ),
        "copywriting": (
            "using psychological profiling (NLP meta-programs), framework-driven structure "
            "(AIDA/PAS/FAB selected per context), forbidden lexicon applied, specific social "
            "proof and metrics, objection handling, clear CTA with urgency mechanism, and "
            "A/B testable variants"
        ),
        "design": (
            "with responsive breakpoints (mobile-first), WCAG 2.2 AA accessibility, "
            "complete state coverage (loading, empty, error, success), consistent design "
            "tokens (spacing, color, typography), keyboard navigation, screen reader support, "
            "and cross-browser testing"
        ),
        "data": (
            "with data validation at ingestion, schema enforcement, idempotent processing, "
            "incremental updates, proper indexing, monitoring/alerting on data quality, "
            "partitioning strategy, and backup/recovery plan"
        ),
        "strategy": (
            "with clear success metrics, risk assessment matrix, dependency mapping, "
            "phased rollout plan, rollback strategy, stakeholder alignment checkpoints, "
            "resource allocation, and measurable milestones"
        ),
        "research": (
            "with primary and secondary sources, methodology documentation, data triangulation, "
            "bias acknowledgment, confidence intervals, peer-reviewed references where available, "
            "and actionable recommendations ranked by impact"
        ),
        "_default": (
            "with thorough coverage, edge case handling, clear documentation, "
            "and production-ready quality"
        ),
    },
    "elegant": {
        "code": (
            "with clean abstractions, minimal coupling, expressive naming, DRY without "
            "over-abstraction, consistent patterns throughout, and code that reads like "
            "well-written prose — prefer clarity over cleverness"
        ),
        "content": (
            "with minimalist composition, thoughtful negative space, refined transitions, "
            "cohesive color palette, professional typography, and a polished aesthetic "
            "that feels intentional in every detail"
        ),
        "copywriting": (
            "with concise language, rhythmic sentence structure, emotional precision, "
            "no wasted words, power verbs over adjectives, and a closing line that "
            "resonates and drives action"
        ),
        "design": (
            "with consistent 8px spacing grid, thoughtful micro-interactions, refined "
            "shadows and depth, cohesive design tokens, smooth 200-300ms transitions, "
            "and visual hierarchy that guides the eye naturally"
        ),
        "_default": (
            "with clean structure, refined aesthetics, and attention to detail "
            "throughout — simple but not simplistic"
        ),
    },
    "professional": {
        "code": (
            "with proper error handling, comprehensive test coverage (>80%), "
            "CI/CD pipeline, semantic versioning, API documentation (OpenAPI/Swagger), "
            "structured logging, and code review-ready formatting"
        ),
        "content": (
            "with industry-standard formats, consistent branding, proper aspect ratios "
            "per platform, professional color grading (LUT-based), broadcast-quality audio "
            "(−14 LUFS for streaming, −24 LUFS for broadcast), and metadata optimization"
        ),
        "copywriting": (
            "with proper formatting (headers, bullets, whitespace), brand voice consistency, "
            "legally reviewed claims, accessibility-friendly language, and mobile-optimized "
            "structure (front-loaded value, scannable)"
        ),
        "design": (
            "with pixel-perfect alignment, consistent component library, documented design "
            "system, responsive across all breakpoints, print-ready exports where applicable, "
            "and brand guideline compliance"
        ),
        "_default": (
            "meeting industry standards with attention to formatting, consistency, "
            "and production-ready polish"
        ),
    },
    "thorough": {
        "code": (
            "covering all code paths, boundary conditions, concurrent access scenarios, "
            "performance under load, memory management, and both happy path and failure modes"
        ),
        "security": (
            "with full OWASP Top 10 assessment, dependency vulnerability scan, secrets "
            "detection, infrastructure review, access control audit, logging verification, "
            "incident response readiness check, and compliance gap analysis"
        ),
        "research": (
            "with exhaustive source coverage across academic, industry, and primary sources, "
            "methodology transparency, limitations acknowledged, competing viewpoints presented, "
            "and synthesis that connects findings to actionable insights"
        ),
        "_default": (
            "with comprehensive coverage — no shortcuts, all angles considered, "
            "edge cases addressed, and nothing left implicit"
        ),
    },
    "scalable": {
        "code": (
            "with horizontal scaling capability, stateless design, connection pooling, "
            "caching strategy (Redis/CDN), async processing for heavy operations, "
            "database query optimization, and load testing benchmarks"
        ),
        "data": (
            "with partitioning strategy, incremental processing, distributed compute "
            "capability, storage tiering (hot/warm/cold), and cost projections at 10x/100x volume"
        ),
        "strategy": (
            "with growth modeling at 10x/100x scale, infrastructure cost curves, "
            "team scaling plan, process automation priorities, and bottleneck identification"
        ),
        "_default": (
            "designed to handle significant growth — with clear scaling dimensions, "
            "bottleneck awareness, and a path from current to 10x load"
        ),
    },
    "modern": {
        "code": (
            "using current best practices and latest stable versions — async/await patterns, "
            "type annotations, package manager lockfiles, and contemporary frameworks "
            "over legacy approaches"
        ),
        "design": (
            "with glassmorphism or subtle depth effects, variable fonts, micro-interactions, "
            "dark mode support, fluid typography (clamp-based), container queries, "
            "and motion design that enhances rather than distracts"
        ),
        "content": (
            "with platform-native formats (vertical 9:16 for Reels/TikTok, 1:1 for feed), "
            "current trends integrated authentically, fast-paced editing, captions/subtitles "
            "baked in, and hook within first 3 seconds"
        ),
        "_default": (
            "using current best practices and contemporary patterns — "
            "avoiding legacy approaches where better alternatives exist"
        ),
    },
    "secure": {
        "code": (
            "with parameterized queries, bcrypt password hashing (cost ≥ 12), HTTPS enforced, "
            "CORS properly configured, security headers (CSP, HSTS, X-Frame-Options), "
            "input sanitization, output encoding, and secrets in environment variables"
        ),
        "design": (
            "with CSRF tokens on all forms, Content Security Policy headers, secure cookie "
            "flags (HttpOnly, Secure, SameSite), and no sensitive data in URLs or localStorage"
        ),
        "_default": (
            "following security best practices — input validation, output encoding, "
            "secrets management, and principle of least privilege"
        ),
    },
    "clean": {
        "code": (
            "with consistent formatting, single-responsibility functions, descriptive naming, "
            "no dead code, no commented-out blocks, minimal nesting (guard clauses preferred), "
            "and self-documenting structure"
        ),
        "design": (
            "with ample whitespace, clear visual hierarchy, consistent spacing, "
            "limited color palette (3-5 colors max), and uncluttered layouts"
        ),
        "_default": (
            "with clear structure, no unnecessary complexity, and everything in its place"
        ),
    },
    "comprehensive": {
        "code": (
            "with complete CRUD operations, pagination, filtering, sorting, error responses, "
            "validation, authentication, rate limiting, logging, health checks, and documentation"
        ),
        "copywriting": (
            "with full-funnel coverage (awareness → consideration → decision), multi-format "
            "deliverables (long-form + short-form + social snippets), audience segmentation, "
            "A/B testable subject lines, clear CTA hierarchy, and metrics-driven success criteria"
        ),
        "research": (
            "covering all major perspectives, primary and secondary sources, quantitative and "
            "qualitative data, historical context, current state, future projections, and "
            "a clear executive summary with ranked recommendations"
        ),
        "strategy": (
            "including market analysis, competitive landscape, SWOT assessment, financial "
            "projections, resource requirements, risk matrix, implementation timeline, "
            "success metrics, and contingency plans"
        ),
        "_default": (
            "covering all relevant aspects — nothing important omitted, "
            "with clear organization and complete coverage"
        ),
    },
    "optimized": {
        "code": (
            "with profiled hot paths, minimized allocations, lazy loading where appropriate, "
            "database query optimization (explain plans), caching at appropriate layers, "
            "bundle size management, and measurable performance benchmarks"
        ),
        "content": (
            "with platform-specific encoding (H.264/H.265), compressed assets, lazy-loaded "
            "media, responsive images (srcset), CDN delivery, and Core Web Vitals compliance "
            "(LCP < 2.5s, FID < 100ms, CLS < 0.1)"
        ),
        "_default": (
            "with measurable performance targets, identified bottlenecks addressed, "
            "and efficiency gains documented"
        ),
    },
    "detailed": {
        "code": (
            "with inline documentation for non-obvious logic, type annotations, "
            "docstrings on public interfaces, architecture decision records for key choices, "
            "and README with setup/usage/deployment instructions"
        ),
        "content": (
            "with shot list, equipment specs, lighting diagrams, audio setup notes, "
            "post-production workflow, color grading references, and delivery specifications "
            "per platform"
        ),
        "strategy": (
            "with granular task breakdown, owner assignments, dependency chains, "
            "time estimates with confidence ranges, decision criteria documented, "
            "and assumptions explicitly stated"
        ),
        "_default": (
            "with specifics rather than generalizations — concrete examples, "
            "exact specifications, and nothing left vague"
        ),
    },
}

# ── Skip Patterns (don't enrich these) ───────────────────────────

SKIP_PATTERNS = [
    re.compile(r"^(hi|hello|hey|sup|yo|gm|good morning|good evening)\b", re.I),
    re.compile(r"^(status|what.?s next|show queue|morning briefing)\b", re.I),
    re.compile(r"^(clock (in|out)|billing|invoice)\b", re.I),
    re.compile(r"^(remember|recall|forget)\b", re.I),
    re.compile(r"^(yes|no|ok|sure|got it|thanks|thank you)\b", re.I),
    re.compile(r"^/\w+", re.I),  # slash commands
]

# Messages under this length are likely too simple to enrich
MIN_ENRICH_LENGTH = 15

# Messages over this length probably have enough specificity already
MAX_ENRICH_LENGTH = 2000

# Flavor words to detect (compiled regex for speed)
FLAVOR_WORD_PATTERN = re.compile(
    r"\b(" + "|".join(FLAVOR_EXPANSIONS.keys()) + r")\b",
    re.IGNORECASE
)


class PromptEnricher:
    """
    Expands vague quality words into domain-specific actionable criteria.

    Rule-based (0ms, $0) — no LLM call needed for enrichment.
    """

    def __init__(self, skill_index_path: str = None):
        """
        Args:
            skill_index_path: Path to SKILL_INDEX.yaml for skill-aware enrichment.
                             If None, skill references are skipped.
        """
        self.available_skills = {}
        if skill_index_path:
            self._load_skill_index(skill_index_path)

    def _load_skill_index(self, path: str):
        """Load skill registry for context-aware enrichment."""
        try:
            import yaml
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            # Build skill lookup by trigger keywords
            for name, info in data.items():
                if isinstance(info, dict):
                    self.available_skills[name] = {
                        "description": info.get("description", ""),
                        "triggers": info.get("triggers", []),
                        "type": info.get("type", ""),
                    }
            logger.info(f"[ENRICHER] Loaded {len(self.available_skills)} skills from index")
        except Exception as e:
            logger.warning(f"[ENRICHER] Could not load skill index: {e}")

    def enrich(self, message: str) -> EnrichmentResult:
        """
        Main enrichment entry point.

        Returns EnrichmentResult with the enriched prompt and metadata.
        """
        # Check if we should skip enrichment
        skip = self._should_skip(message)
        if skip:
            return EnrichmentResult(
                original=message,
                enriched=message,
                domain="none",
                flavor_words_found=[],
                criteria_added=[],
                enrichment_level="none",
                skip_reason=skip,
            )

        # Detect domain
        domain = self._detect_domain(message)

        # Find flavor words
        flavor_words = self._find_flavor_words(message)

        if not flavor_words:
            return EnrichmentResult(
                original=message,
                enriched=message,
                domain=domain,
                flavor_words_found=[],
                criteria_added=[],
                enrichment_level="none",
                skip_reason="no flavor words detected",
            )

        # Expand flavor words into domain-specific criteria
        enriched, criteria = self._expand_flavor_words(message, flavor_words, domain)

        # Add skill/tool references if relevant
        enriched = self._add_skill_hints(enriched, domain)

        # Determine enrichment level
        level = "light" if len(criteria) <= 1 else "standard" if len(criteria) <= 3 else "deep"

        result = EnrichmentResult(
            original=message,
            enriched=enriched,
            domain=domain,
            flavor_words_found=flavor_words,
            criteria_added=criteria,
            enrichment_level=level,
        )

        logger.info(
            f"[ENRICHER] domain={domain} words={flavor_words} "
            f"level={level} criteria={len(criteria)} "
            f"len_delta=+{len(enriched) - len(message)} chars"
        )

        return result

    def _should_skip(self, message: str) -> Optional[str]:
        """Check if message should bypass enrichment."""
        if len(message.strip()) < MIN_ENRICH_LENGTH:
            return "too short"

        if len(message) > MAX_ENRICH_LENGTH:
            return "already detailed enough"

        for pattern in SKIP_PATTERNS:
            if pattern.search(message.strip()):
                return "skip pattern matched"

        return None

    def _detect_domain(self, message: str) -> str:
        """Detect the primary domain of the message using weighted keyword matching."""
        msg_lower = message.lower()
        scores: Dict[str, float] = {}

        for domain, config in DOMAIN_SIGNALS.items():
            count = sum(1 for kw in config["keywords"] if kw in msg_lower)
            if count > 0:
                scores[domain] = count * config["weight"]

        if not scores:
            return "general"

        return max(scores, key=scores.get)

    def _find_flavor_words(self, message: str) -> List[str]:
        """Find all flavor/quality words in the message."""
        found = FLAVOR_WORD_PATTERN.findall(message)
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for w in found:
            w_lower = w.lower()
            if w_lower not in seen:
                seen.add(w_lower)
                unique.append(w_lower)
        return unique

    def _expand_flavor_words(
        self, message: str, flavor_words: List[str], domain: str
    ) -> Tuple[str, List[str]]:
        """
        Replace/augment flavor words with domain-specific criteria.

        Strategy: Append a structured criteria block rather than inline replacement.
        This preserves the user's original phrasing while adding specificity.
        """
        criteria_parts = []

        for word in flavor_words:
            expansions = FLAVOR_EXPANSIONS.get(word, {})
            # Try domain-specific, then fall back to default
            expansion = expansions.get(domain, expansions.get("_default", ""))
            if expansion:
                criteria_parts.append(f"- \"{word}\" means: {expansion}")

        if not criteria_parts:
            return message, []

        # Build the enrichment block
        criteria_block = "\n".join(criteria_parts)
        enriched = (
            f"{message}\n\n"
            f"[Quality criteria — expand \"{', '.join(flavor_words)}\" into these specifics:]\n"
            f"{criteria_block}"
        )

        return enriched, [f"{w} → {domain}" for w in flavor_words]

    def _add_skill_hints(self, message: str, domain: str) -> str:
        """Add references to available skills/tools relevant to the domain."""
        if not self.available_skills:
            return message

        # Map domains to relevant skill names
        domain_skill_map = {
            "copywriting": ["copywriting", "copy-editing"],
            "content": ["copywriting", "remotion-video"],
            "code": ["code-refactoring", "code-review", "security-best-practices"],
            "security": ["security-best-practices", "security-audit"],
            "design": ["ui-ux-pro-max"],
            "research": ["web-research"],
            "data": ["document-analysis"],
            "strategy": ["paid-ads"],
        }

        relevant_skills = domain_skill_map.get(domain, [])
        available = [s for s in relevant_skills if s in self.available_skills]

        if available:
            skill_hints = ", ".join(available)
            message += f"\n[Available ATLAS skills for this domain: {skill_hints}]"

        return message


# ── Convenience function for pipeline integration ─────────────────

_enricher_instance: Optional[PromptEnricher] = None


def get_enricher(skill_index_path: str = None) -> PromptEnricher:
    """Get or create the singleton enricher instance."""
    global _enricher_instance
    if _enricher_instance is None:
        _enricher_instance = PromptEnricher(skill_index_path=skill_index_path)
    return _enricher_instance


def enrich_prompt(message: str, skill_index_path: str = None) -> EnrichmentResult:
    """One-liner for pipeline integration."""
    return get_enricher(skill_index_path).enrich(message)
