"""
Evolution Analyzer — Step 2 of the evolution cycle.

Sends collected metrics to MiniMax M2.7 for analysis.
M2.7 identifies patterns, problems, and improvement opportunities.

If M2.7 is unavailable, falls back to rule-based analysis.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Output of M2.7 analysis."""

    problems: List[Dict[str, Any]] = field(default_factory=list)
    opportunities: List[Dict[str, Any]] = field(default_factory=list)
    recommendations: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0  # 0.0-1.0
    analysis_source: str = "rule_based"  # "m2.7" or "rule_based"
    raw_response: str = ""


# System prompt for M2.7 when analyzing routing metrics
M27_ANALYSIS_PROMPT = """You are the ABLE routing evolution analyzer.

You receive structured metrics from the interaction logging system.
Your job: identify problems, opportunities, and recommend specific
weight adjustments for the complexity scorer.

Output VALID JSON with this structure:
{
    "problems": [
        {"type": "under_routing|over_routing|high_failure|high_fallback",
         "tier": 1, "domain": "security", "severity": "high|medium|low",
         "description": "..."}
    ],
    "opportunities": [
        {"type": "cost_savings|latency_improvement|accuracy_improvement",
         "description": "...", "estimated_impact": "..."}
    ],
    "recommendations": [
        {"type": "weight_adjustment|threshold_adjustment|domain_adjustment",
         "target": "safety_critical_weight",
         "current": 0.30, "proposed": 0.35,
         "reason": "..."}
    ],
    "confidence": 0.8
}

Rules:
- Never propose changes > 20% of current value in a single cycle
- Never propose threshold changes that would collapse tiers
- tier_1_max must be < tier_2_max
- All weights must stay in [0.0, 1.0]
- Be conservative — small adjustments compound over time
"""


class EvolutionAnalyzer:
    """
    Analyzes metrics using M2.7 or rule-based fallback.

    The analyzer is the "brain" of the evolution cycle. It receives
    metrics from the collector and produces actionable recommendations.
    """

    def __init__(self, provider=None):
        """
        Args:
            provider: LLM provider for M2.7 calls. If None, uses rule-based analysis.
        """
        self._provider = provider

    async def analyze(self, metrics: Dict[str, Any]) -> AnalysisResult:
        """
        Analyze collected metrics and produce recommendations.

        Tries M2.7 first, falls back to rule-based analysis.
        """
        if self._provider:
            try:
                return await self._analyze_with_m27(metrics)
            except Exception as e:
                logger.warning(f"M2.7 analysis failed, falling back to rules: {e}")

        return self._analyze_rule_based(metrics)

    async def _analyze_with_m27(self, metrics: Dict[str, Any]) -> AnalysisResult:
        """Send metrics to M2.7 for analysis."""
        prompt = (
            f"{M27_ANALYSIS_PROMPT}\n\n"
            f"## Current Metrics\n```json\n{json.dumps(metrics, indent=2, default=str)}\n```\n\n"
            f"Analyze and respond with JSON only."
        )

        result = await self._provider.complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2000,
        )

        response_text = result.content if hasattr(result, "content") else str(result)

        try:
            # Extract JSON from response
            parsed = self._extract_json(response_text)
            return AnalysisResult(
                problems=parsed.get("problems", []),
                opportunities=parsed.get("opportunities", []),
                recommendations=parsed.get("recommendations", []),
                confidence=parsed.get("confidence", 0.5),
                analysis_source="m2.7",
                raw_response=response_text,
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse M2.7 response: {e}")
            # Fall through to rule-based
            return self._analyze_rule_based(metrics)

    def _analyze_rule_based(self, metrics: Dict[str, Any]) -> AnalysisResult:
        """
        Rule-based fallback analysis when M2.7 is unavailable.

        Applies simple heuristics to detect common routing problems.
        """
        problems = []
        opportunities = []
        recommendations = []

        # ── Check failure rates ───────────────────────────────
        for tier_data in metrics.get("failures_by_tier", []):
            rate = tier_data.get("failure_rate_pct", 0)
            tier = tier_data.get("selected_tier", 0)

            if rate > 20:
                problems.append({
                    "type": "high_failure",
                    "tier": tier,
                    "severity": "high" if rate > 50 else "medium",
                    "description": f"Tier {tier} failure rate is {rate}%",
                })

        # ── Check escalation rate ─────────────────────────────
        escalation = metrics.get("escalation_rate", {})
        override_rate = escalation.get("override_rate_pct", 0)

        if override_rate > 15:
            problems.append({
                "type": "under_routing",
                "severity": "medium",
                "description": f"Override rate {override_rate}% suggests scorer is under-routing",
            })
            recommendations.append({
                "type": "threshold_adjustment",
                "target": "tier_1_max",
                "direction": "decrease",
                "reason": "High override rate — lower tier 1 ceiling to escalate more tasks",
            })

        # ── Check domain accuracy ─────────────────────────────
        for domain_data in metrics.get("domain_accuracy", []):
            domain = domain_data.get("domain", "")
            total = domain_data.get("total", 0)
            escalations = domain_data.get("escalations", 0)

            if total >= 5 and escalations / max(total, 1) > 0.3:
                problems.append({
                    "type": "under_routing",
                    "domain": domain,
                    "severity": "medium",
                    "description": f"Domain '{domain}' has {escalations}/{total} escalations",
                })
                recommendations.append({
                    "type": "domain_adjustment",
                    "target": domain,
                    "direction": "increase",
                    "reason": f"Domain '{domain}' is being under-routed",
                })

        # ── Check cost optimization ───────────────────────────
        cost_data = metrics.get("cost_by_tier", [])
        total_cost = sum(t.get("total_cost_usd", 0) for t in cost_data)
        tier4_cost = sum(
            t.get("total_cost_usd", 0) for t in cost_data if t.get("selected_tier") == 4
        )

        if total_cost > 0 and tier4_cost / total_cost > 0.7:
            opportunities.append({
                "type": "cost_savings",
                "description": f"Tier 4 accounts for {tier4_cost/total_cost*100:.0f}% of cost",
                "estimated_impact": "Shifting 10% of Tier 4 traffic to Tier 2 could save significantly",
            })

        # ── Scoring drift ─────────────────────────────────────
        drift_data = metrics.get("scoring_drift", [])
        if len(drift_data) >= 2:
            v_old = drift_data[-2]
            v_new = drift_data[-1]
            score_shift = abs(
                v_new.get("avg_score", 0) - v_old.get("avg_score", 0)
            )
            if score_shift > 0.1:
                problems.append({
                    "type": "scoring_drift",
                    "severity": "low",
                    "description": (
                        f"Average score shifted {score_shift:.3f} between "
                        f"v{v_old.get('scorer_version')} and v{v_new.get('scorer_version')}"
                    ),
                })

        confidence = 0.6 if problems or recommendations else 0.9
        return AnalysisResult(
            problems=problems,
            opportunities=opportunities,
            recommendations=recommendations,
            confidence=confidence,
            analysis_source="rule_based",
        )

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON from a response that may contain markdown fences."""
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from code fences
        import re
        match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))

        raise json.JSONDecodeError("No valid JSON found", text, 0)
