"""
Complexity Scorer — Rule-based task complexity scoring for multi-tier routing.

No LLM calls. Must run in <5ms. Pure heuristic pattern matching.

Score mapping:
    < 0.4  → Tier 1 (Nemotron 3 Super — default)
    0.4-0.7 → Tier 2 (MiMo-V2-Pro — escalation)
    > 0.7  → Tier 4 (Opus 4.6 — premium, budget-gated)

Weights are stored in config/scorer_weights.yaml and tunable by the
M2.7 evolution daemon.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ScoringResult:
    """Result of complexity scoring."""
    score: float                     # 0.0 - 1.0
    features: Dict[str, float]      # Individual feature contributions
    domain: str                      # Detected domain
    domain_adjustment: float         # Domain-specific adjustment applied
    selected_tier: int               # 1, 2, or 4
    selected_provider: Optional[str] = None  # Provider name (filled by router)
    budget_gated: bool = False       # True if Opus was requested but budget exhausted
    scorer_version: int = 1


class ComplexityScorer:
    """
    Rule-based complexity scorer. No LLM calls.

    Returns a float 0.0-1.0 that maps to provider tiers:
        < 0.4  → Tier 1 (Nemotron 3 Super)
        0.4-0.7 → Tier 2 (MiMo-V2-Pro)
        > 0.7  → Tier 4 (Opus 4.6, with budget gating)

    Weights are stored in config and tunable by the M2.7 evolution daemon.
    """

    # Feature detection patterns (compiled once)
    TOOL_PATTERNS = re.compile(
        r'\b(search|fetch|browse|deploy|push|commit|create\s+repo|open\s+pr|'
        r'provision|invoke|call|execute|run\s+script|install|download)\b',
        re.IGNORECASE
    )

    CODE_PATTERNS = re.compile(
        r'\b(code|implement|function|class|debug|refactor|optimize|compile|'
        r'import|syntax|algorithm|data\s+structure|api|endpoint|database|'
        r'sql|query|migration|test|unittest|integration|deploy)\b',
        re.IGNORECASE
    )

    MULTI_STEP_PATTERNS = re.compile(
        r'\b(then|after\s+that|next|finally|step\s*\d|first|second|third|'
        r'subsequently|once\s+done|before\s+that|followed\s+by|'
        r'phase\s*\d|stage\s*\d)\b',
        re.IGNORECASE
    )

    SAFETY_DOMAINS = {
        "security": re.compile(
            r'\b(security|vulnerab|exploit|injection|xss|csrf|auth|encrypt|'
            r'credential|secret|token|hack|penetrat|audit|threat|malware)\b',
            re.IGNORECASE
        ),
        "financial": re.compile(
            r'\b(financ|payment|invoice|billing|transact|revenue|cost|budget|'
            r'accounting|tax|pricing|subscription|refund)\b',
            re.IGNORECASE
        ),
        "legal": re.compile(
            r'\b(legal|compliance|regulat|gdpr|hipaa|contract|liability|'
            r'terms\s+of\s+service|privacy\s+policy|copyright|license)\b',
            re.IGNORECASE
        ),
        "production": re.compile(
            r'\b(production|deploy|release|rollback|incident|outage|'
            r'downtime|monitoring|alert|on-?call|sla|uptime)\b',
            re.IGNORECASE
        ),
    }

    DOMAIN_PATTERNS = {
        "coding": re.compile(
            r'\b(code|implement|build|develop|fix|debug|refactor|test|'
            r'function|class|api|database)\b', re.IGNORECASE
        ),
        "creative": re.compile(
            r'\b(write|draft|blog|article|copy|headline|slogan|'
            r'creative|story|content|pitch)\b', re.IGNORECASE
        ),
        "research": re.compile(
            r'\b(research|investigat|find\s+out|look\s+up|compare|'
            r'analyze|benchmark|survey|study)\b', re.IGNORECASE
        ),
        "planning": re.compile(
            r'\b(plan|strategy|roadmap|architect|design|decompose|'
            r'break\s+down|prioritize|schedule)\b', re.IGNORECASE
        ),
    }

    def __init__(self, weights_path: str = "config/scorer_weights.yaml"):
        self.weights = self._load_weights(weights_path)
        self._weights_path = weights_path

    def _load_weights(self, path: str) -> Dict[str, Any]:
        """Load scoring weights from YAML config."""
        p = Path(path)
        if not p.exists():
            logger.warning(f"Scorer weights not found at {path}, using defaults")
            return self._default_weights()

        with open(p) as f:
            data = yaml.safe_load(f)

        return data or self._default_weights()

    def _default_weights(self) -> Dict[str, Any]:
        """Default weights if config file is missing."""
        return {
            "features": {
                "token_count_threshold": 2000,
                "token_count_weight": 0.20,
                "requires_tools_weight": 0.15,
                "requires_code_weight": 0.15,
                "multi_step_weight": 0.20,
                "safety_critical_weight": 0.30,
            },
            "domain_adjustments": {
                "default": 0.0,
                "coding": 0.05,
                "security": 0.15,
                "financial": 0.10,
                "legal": 0.15,
                "production": 0.10,
                "creative": -0.05,
                "research": 0.0,
                "planning": 0.05,
            },
            "tier_thresholds": {
                "tier_1_max": 0.4,
                "tier_2_max": 0.7,
            },
            "opus_daily_budget_usd": 15.00,
            "opus_monthly_budget_usd": 100.00,
            "version": 1,
        }

    def reload_weights(self):
        """Hot-reload weights from disk (called after evolution daemon updates)."""
        self.weights = self._load_weights(self._weights_path)
        logger.info(f"Scorer weights reloaded (version {self.version})")

    @property
    def version(self) -> int:
        """Current scorer weights version."""
        return self.weights.get("version", 1)

    def score(self, message: str, context: Optional[Dict[str, Any]] = None) -> ScoringResult:
        """
        Score a message's complexity. Pure heuristic, no API calls.

        Args:
            message: The user's input message
            context: Optional context dict (conversation history, etc.)

        Returns:
            ScoringResult with score, features breakdown, and tier selection
        """
        context = context or {}
        features: Dict[str, float] = {}
        feat_cfg = self.weights.get("features", {})

        # ── Feature 1: Token count ─────────────────────────────────
        # Approximate tokens as words * 1.3
        word_count = len(message.split())
        estimated_tokens = int(word_count * 1.3)
        threshold = feat_cfg.get("token_count_threshold", 2000)
        weight = feat_cfg.get("token_count_weight", 0.2)

        if estimated_tokens > threshold:
            features["token_count"] = weight
        elif estimated_tokens > threshold * 0.5:
            features["token_count"] = weight * 0.5
        else:
            features["token_count"] = 0.0

        # ── Feature 2: Requires tools ──────────────────────────────
        weight = feat_cfg.get("requires_tools_weight", 0.15)
        tool_matches = len(self.TOOL_PATTERNS.findall(message))
        features["requires_tools"] = min(weight, tool_matches * (weight / 3))

        # ── Feature 3: Requires code ───────────────────────────────
        weight = feat_cfg.get("requires_code_weight", 0.15)
        code_matches = len(self.CODE_PATTERNS.findall(message))
        features["requires_code"] = min(weight, code_matches * (weight / 4))

        # ── Feature 4: Multi-step task ─────────────────────────────
        weight = feat_cfg.get("multi_step_weight", 0.2)
        step_matches = len(self.MULTI_STEP_PATTERNS.findall(message))
        features["multi_step"] = min(weight, step_matches * (weight / 3))

        # ── Feature 5: Safety-critical domain ──────────────────────
        weight = feat_cfg.get("safety_critical_weight", 0.3)
        safety_score = 0.0
        for domain_name, pattern in self.SAFETY_DOMAINS.items():
            if pattern.search(message):
                safety_score = weight
                break
        features["safety_critical"] = safety_score

        # ── Base score ─────────────────────────────────────────────
        base_score = sum(features.values())

        # ── Domain detection + adjustment ──────────────────────────
        domain = self._detect_domain(message)
        adjustments = self.weights.get("domain_adjustments", {})
        domain_adj = adjustments.get(domain, adjustments.get("default", 0.0))
        features["domain_adjustment"] = domain_adj

        final_score = max(0.0, min(1.0, base_score + domain_adj))

        # ── Tier selection ─────────────────────────────────────────
        tier = self.select_tier(final_score)

        return ScoringResult(
            score=round(final_score, 3),
            features=features,
            domain=domain,
            domain_adjustment=domain_adj,
            selected_tier=tier,
            scorer_version=self.version,
        )

    def select_tier(self, score: float, budget_remaining: Optional[float] = None) -> int:
        """
        Map score to tier, with budget gating for Tier 4.

        Args:
            score: Complexity score 0.0-1.0
            budget_remaining: Remaining Opus budget in USD (None = no limit)

        Returns:
            Tier number (1, 2, or 4)
        """
        thresholds = self.weights.get("tier_thresholds", {})
        t1_max = thresholds.get("tier_1_max", 0.4)
        t2_max = thresholds.get("tier_2_max", 0.7)

        if score <= t1_max:
            return 1

        if score <= t2_max:
            return 2

        # Score > t2_max → Tier 4 (Opus), but check budget
        if budget_remaining is not None and budget_remaining <= 0:
            logger.warning(
                f"Opus budget exhausted (score={score:.2f}), capping at Tier 2"
            )
            return 2

        return 4

    def score_and_route(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        budget_remaining: Optional[float] = None,
    ) -> ScoringResult:
        """
        Score and route in one call, with budget awareness.

        This is the primary entry point for the orchestrator.
        """
        result = self.score(message, context)

        # Re-evaluate tier with budget
        if budget_remaining is not None:
            result.selected_tier = self.select_tier(result.score, budget_remaining)
            if result.score > self.weights.get("tier_thresholds", {}).get("tier_2_max", 0.7):
                if result.selected_tier != 4:
                    result.budget_gated = True

        return result

    def _detect_domain(self, message: str) -> str:
        """Detect the primary domain of a message."""
        # Check safety domains first (highest priority)
        for domain_name, pattern in self.SAFETY_DOMAINS.items():
            if pattern.search(message):
                return domain_name

        # Then general domains
        for domain_name, pattern in self.DOMAIN_PATTERNS.items():
            if pattern.search(message):
                return domain_name

        return "default"
