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
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

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
    output_spec: Optional[Dict] = None  # token budget, format, structure
    memory_applied: List[str] = field(default_factory=list)
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

# ── Output Steering (token budget, format, structure per domain) ──

OUTPUT_SPECS: Dict[str, Dict] = {
    "code": {
        "token_budget": "800-2000",
        "format": "fenced code blocks with language tags",
        "structure": [
            "Brief explanation of approach (2-3 sentences max)",
            "Complete implementation code",
            "Usage example if non-obvious",
        ],
        "anti_laziness": [
            "Write the COMPLETE implementation — no placeholder comments like '# TODO' or '# add logic here'",
            "Include all imports and type annotations",
            "Handle the primary error cases, not just the happy path",
        ],
        "output_constraints": [
            "If a function exceeds 30 lines, extract helpers",
            "Every public function gets a one-line docstring",
        ],
    },
    "security": {
        "token_budget": "1000-2500",
        "format": "code with inline security comments explaining WHY each measure exists",
        "structure": [
            "Threat model summary (what we're defending against)",
            "Implementation with security annotations",
            "Configuration notes (env vars, headers, settings)",
        ],
        "anti_laziness": [
            "Show ACTUAL bcrypt/hashing code, not 'hash_password(pw)' stubs",
            "Include the REAL parameterized query syntax, not pseudo-code",
            "Write the actual validation logic, not 'validate(input)' placeholders",
            "Specify exact header values (e.g., 'Strict-Transport-Security: max-age=63072000')",
        ],
        "output_constraints": [
            "Never output example secrets, tokens, or keys — use environment variables",
            "Include .env.example with placeholder values if config is involved",
        ],
    },
    "copywriting": {
        "token_budget": "150-500",
        "format": "ready-to-send copy with clear sections (subject, body, CTA)",
        "structure": [
            "Hook / opening line",
            "Value proposition / body",
            "Call to action with specific next step",
        ],
        "anti_laziness": [
            "Write the ACTUAL copy, not a description of what the copy should say",
            "Include specific numbers, proof points, or examples — not '[insert stat here]'",
            "The CTA must be a concrete action ('Book a 15-min call' not 'reach out')",
            "Match the exact word count constraint if one is given",
        ],
        "output_constraints": [
            "No meta-commentary ('Here's a draft...' or 'This copy uses AIDA...')",
            "Output ONLY the final deliverable text",
            "If word count specified, stay within ±10%",
        ],
    },
    "content": {
        "token_budget": "800-2000",
        "format": "structured production brief or content plan",
        "structure": [
            "Content concept / hook",
            "Production specifications (resolution, audio, format)",
            "Platform-specific deliverables with aspect ratios",
            "Distribution / SEO notes",
        ],
        "anti_laziness": [
            "Specify EXACT settings (e.g., '1080p60 H.264, -14 LUFS audio') not 'high quality video'",
            "Include actual equipment recommendations if relevant, not 'professional camera'",
            "Platform specs must be exact (1080x1920 for Reels, 1920x1080 for YouTube)",
        ],
        "output_constraints": [],
    },
    "design": {
        "token_budget": "500-1500",
        "format": "component code or design specification with exact values",
        "structure": [
            "Design decisions and rationale",
            "Implementation with exact values (colors, spacing, fonts)",
            "Responsive behavior across breakpoints",
            "Accessibility notes",
        ],
        "anti_laziness": [
            "Use exact hex colors (#D4AF37) not 'gold'",
            "Specify exact spacing in px/rem (padding: 1.5rem) not 'add spacing'",
            "Include actual font-family stacks with fallbacks",
            "Define all interactive states (hover, focus, active, disabled)",
        ],
        "output_constraints": [
            "All colors must be defined as variables/tokens",
            "Spacing uses consistent scale (4/8/12/16/24/32/48px)",
        ],
    },
    "data": {
        "token_budget": "500-1500",
        "format": "SQL/code with schema definitions and sample queries",
        "structure": [
            "Schema design with types and constraints",
            "Query implementation",
            "Index recommendations",
            "Sample data or test cases",
        ],
        "anti_laziness": [
            "Include actual column types and constraints (NOT NULL, UNIQUE, CHECK)",
            "Write complete SQL — not 'SELECT relevant columns FROM table'",
            "Include EXPLAIN ANALYZE notes for performance-critical queries",
        ],
        "output_constraints": [],
    },
    "research": {
        "token_budget": "1000-3000",
        "format": "structured report with citations and confidence levels",
        "structure": [
            "Executive summary (3-5 bullet points)",
            "Findings organized by theme",
            "Evidence quality assessment",
            "Actionable recommendations ranked by impact",
        ],
        "anti_laziness": [
            "Cite specific sources, not 'studies show' or 'experts say'",
            "Quantify findings where possible — percentages, dollar amounts, timelines",
            "Distinguish between verified facts and inferences",
            "Flag low-confidence claims explicitly",
        ],
        "output_constraints": [
            "Each claim needs a source or confidence qualifier",
            "Recommendations must be actionable, not 'consider exploring'",
        ],
    },
    "strategy": {
        "token_budget": "1000-2500",
        "format": "structured plan with phases, owners, and success metrics",
        "structure": [
            "Objective and success criteria (measurable)",
            "Phased implementation plan",
            "Resource requirements and dependencies",
            "Risk matrix (probability × impact)",
            "Decision points and go/no-go criteria",
        ],
        "anti_laziness": [
            "Success metrics must be measurable ('reduce churn by 15%' not 'improve retention')",
            "Timelines need actual durations ('2 weeks' not 'soon')",
            "Risks must include specific mitigation actions, not just identification",
            "Each phase needs a concrete deliverable, not just a description",
        ],
        "output_constraints": [],
    },
}

# ── General anti-laziness directives (applied to all enriched prompts) ──

UNIVERSAL_DIRECTIVES = [
    "Deliver the COMPLETE output — do not truncate, summarize prematurely, or use '...' to skip content",
    "If you reference something, include the actual content — not '[insert X here]' placeholders",
    "Prefer concrete specifics over abstract generalities in every section",
]

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

    def enrich(self, message: str, memory_context: Optional[Dict] = None) -> EnrichmentResult:
        """
        Main enrichment entry point.

        Applies layered enrichment that complements (not competes with) skills/tools:
          1. Flavor word expansion — what vague quality words mean concretely
          2. Output steering — compact format/budget/anti-laziness hints
          3. Memory context — user prefs, project context, patterns
          4. Skill hints — which ABLE skills are available

        Args:
            message: The user's prompt text.
            memory_context: Optional dict with keys:
                user_preferences (list[str]), project_context (str),
                known_patterns (list[str]), people (dict)
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

        # Early exit: nothing to enrich
        if not flavor_words and not memory_context:
            return EnrichmentResult(
                original=message,
                enriched=message,
                domain=domain,
                flavor_words_found=[],
                criteria_added=[],
                enrichment_level="none",
                skip_reason="no flavor words detected",
            )

        enriched = message
        criteria = []
        output_spec = None
        memory_applied = []

        # Phase 1: Expand flavor words into domain-specific criteria
        if flavor_words:
            enriched, criteria = self._expand_flavor_words(enriched, flavor_words, domain)

            # Phase 2: Compact output steering (format + budget + top anti-laziness rules)
            # Kept light so skills/tools remain the primary execution driver
            enriched, output_spec = self._build_output_steering(enriched, domain)

        # Phase 3: Memory/pattern personalization (1-2 lines, not a biography)
        if memory_context:
            enriched, memory_applied = self._apply_memory_context(
                enriched, domain, memory_context
            )

        # Edge case: memory provided but nothing relevant applied
        if enriched == message:
            return EnrichmentResult(
                original=message,
                enriched=message,
                domain=domain,
                flavor_words_found=[],
                criteria_added=[],
                enrichment_level="none",
                skip_reason="no enrichment applicable",
            )

        # Add skill/tool references if relevant
        enriched = self._add_skill_hints(enriched, domain)

        # Determine enrichment level based on what was applied
        if len(criteria) >= 3:
            level = "deep"
        elif len(criteria) >= 2 or (criteria and output_spec):
            level = "standard"
        else:
            level = "light"

        result = EnrichmentResult(
            original=message,
            enriched=enriched,
            domain=domain,
            flavor_words_found=flavor_words,
            criteria_added=criteria,
            enrichment_level=level,
            output_spec=output_spec,
            memory_applied=memory_applied,
        )

        logger.info(
            f"[ENRICHER] domain={domain} words={flavor_words} "
            f"level={level} criteria={len(criteria)} steering={output_spec is not None} "
            f"memory={len(memory_applied)} len_delta=+{len(enriched) - len(message)} chars"
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

    def _build_output_steering(
        self, message: str, domain: str
    ) -> Tuple[str, Optional[Dict]]:
        """
        Add compact output steering: format hint, token budget, top anti-laziness rules.

        Intentionally lightweight — skills/tools own methodology and structure.
        This just sets quality floor expectations for the model's output.
        """
        spec = OUTPUT_SPECS.get(domain)
        if not spec:
            return message, None

        # One-line format + budget header
        parts = [f"[Output: {spec['format']} | Budget: {spec['token_budget']} tokens]"]

        # Top 2 anti-laziness directives (domain's most impactful rules)
        for rule in spec.get("anti_laziness", [])[:2]:
            parts.append(f"- {rule}")

        # Top constraint if any
        constraints = spec.get("output_constraints", [])
        if constraints:
            parts.append(f"- {constraints[0]}")

        # Single universal directive (the most important one)
        parts.append(f"- {UNIVERSAL_DIRECTIVES[0]}")

        steering_block = "\n".join(parts)
        return f"{message}\n{steering_block}", spec

    def _apply_memory_context(
        self, message: str, domain: str, memory_context: Dict
    ) -> Tuple[str, List[str]]:
        """
        Add 1-2 lines of relevant memory context for personalization.

        Kept minimal — the model should focus on the task, not the user's life story.
        Project context and top preferences only.
        """
        applied = []
        parts = []

        # Project context — most useful for steering (one line)
        project = memory_context.get("project_context")
        if project:
            parts.append(f"[Context: {project}]")
            applied.append(f"project: {project[:60]}")

        # Top 2 relevant preferences
        prefs = memory_context.get("user_preferences", [])
        if prefs:
            pref_str = "; ".join(prefs[:2])
            parts.append(f"[Prefs: {pref_str}]")
            applied.extend(f"pref: {p[:60]}" for p in prefs[:2])

        # Known patterns (1 line max)
        patterns = memory_context.get("known_patterns", [])
        if patterns:
            parts.append(f"[Pattern: {patterns[0]}]")
            applied.append(f"pattern: {patterns[0][:60]}")

        if not parts:
            return message, []

        memory_block = "\n".join(parts)
        return f"{message}\n{memory_block}", applied

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
            message += f"\n[Available ABLE skills for this domain: {skill_hints}]"

        return message


# ── Model-assisted deep enrichment (GPT 5.4 nano fallback) ────────

DEEP_ENRICHMENT_SYSTEM = """You are a prompt optimization specialist. Enhance the user's prompt so the AI produces maximum quality output.

Given the original prompt and its detected domain, add SPECIFIC quality requirements that are missing.

Rules:
- Output ONLY the additional enrichment text to append (under 150 tokens)
- Be concrete: exact values, specific standards, measurable criteria
- Don't repeat what's already present
- Focus on what moves the quality needle most for this domain
- No explanation, no meta-commentary — just the enhancement text"""


class DeepEnricher:
    """
    Model-assisted enrichment for high-stakes prompts.

    Called when rule-based enrichment is insufficient:
      - Complexity score > 0.7
      - Domain detection ambiguous
      - Explicit user request for deep quality

    Uses GPT 5.4 nano with high reasoning effort — cheap but thorough.
    """

    @staticmethod
    async def refine(
        result: EnrichmentResult,
        model_call: Callable[[str, str], Awaitable[str]],
    ) -> EnrichmentResult:
        """
        Refine rule-based enrichment with model intelligence.

        Args:
            result: The rule-based EnrichmentResult to refine.
            model_call: async callable(system_prompt, user_prompt) -> str
                        Provided by the gateway using its existing provider chain.
        """
        user_prompt = (
            f"Domain: {result.domain}\n"
            f"Original prompt: {result.original}\n"
            f"Flavor words detected: {', '.join(result.flavor_words_found) or 'none'}\n"
            f"Current enrichment level: {result.enrichment_level}\n\n"
            f"Add missing quality requirements for this domain."
        )

        try:
            model_output = await model_call(DEEP_ENRICHMENT_SYSTEM, user_prompt)
            if model_output and model_output.strip():
                refined = f"{result.enriched}\n[Deep enrichment:]\n{model_output.strip()}"
                return EnrichmentResult(
                    original=result.original,
                    enriched=refined,
                    domain=result.domain,
                    flavor_words_found=result.flavor_words_found,
                    criteria_added=result.criteria_added + ["model_refined"],
                    enrichment_level="deep",
                    output_spec=result.output_spec,
                    memory_applied=result.memory_applied,
                )
        except Exception as e:
            logger.warning(f"[DEEP_ENRICHER] Model refinement failed: {e}")

        return result  # Fallback: return rule-based result unchanged


# ── Convenience function for pipeline integration ─────────────────

_enricher_instance: Optional[PromptEnricher] = None


def get_enricher(skill_index_path: str = None) -> PromptEnricher:
    """Get or create the singleton enricher instance."""
    global _enricher_instance
    if _enricher_instance is None:
        _enricher_instance = PromptEnricher(skill_index_path=skill_index_path)
    return _enricher_instance


def enrich_prompt(
    message: str,
    skill_index_path: str = None,
    memory_context: Optional[Dict] = None,
) -> EnrichmentResult:
    """One-liner for pipeline integration."""
    return get_enricher(skill_index_path).enrich(message, memory_context=memory_context)
