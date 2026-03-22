"""
Tenant Evaluator — Per-tenant evaluation with custom criteria and drift detection.
"""

from __future__ import annotations

import logging
import statistics
from typing import Dict, List, Optional

from .evaluators import ABLEEvaluator

logger = logging.getLogger(__name__)


class TenantEvaluator:
    """Runs evaluations per-tenant with custom criteria."""

    def __init__(self, evaluator: Optional[ABLEEvaluator] = None) -> None:
        self.evaluator = evaluator or ABLEEvaluator()

    def evaluate_for_tenant(
        self,
        tenant_id: str,
        input_text: str,
        output_text: str,
        tenant_config: Optional[Dict] = None,
    ) -> Dict:
        """
        Standard evals + tenant-specific criteria.

        tenant_config may contain:
          - skill_spec: str
          - personality: str
          - required_keywords: list[str]
          - banned_keywords: list[str]
        """
        cfg = tenant_config or {}

        # Run standard evaluators
        context = {
            "skill_spec": cfg.get("skill_spec"),
            "personality": cfg.get("personality"),
        }
        scores = self.evaluator.evaluate(input_text, output_text, context)

        required = cfg.get("required_keywords", [])
        banned = cfg.get("banned_keywords", [])

        if required or banned:
            output_lower = output_text.lower()

            if required:
                hits = sum(1 for kw in required if kw.lower() in output_lower)
                scores["required_keywords"] = hits / len(required)

            if banned:
                violations = sum(1 for kw in banned if kw.lower() in output_lower)
                scores["banned_keywords"] = max(0.0, 1.0 - violations * 0.25)

        average = sum(scores.values()) / len(scores) if scores else 0.0

        return {
            "tenant_id": tenant_id,
            "scores": scores,
            "average": round(average, 4),
            "passed": average >= 0.7,
        }

    def detect_drift(
        self,
        tenant_id: str,
        recent_scores: List[float],
        baseline_scores: List[float],
    ) -> Dict:
        """
        Compare recent vs baseline scores to detect quality drift.

        Returns a dict with drift magnitude, direction, and alert flag.
        A drift > 0.10 triggers an alert.
        """
        if not recent_scores or not baseline_scores:
            return {
                "tenant_id": tenant_id,
                "drift": 0.0,
                "direction": "stable",
                "alert": False,
                "reason": "insufficient data",
            }

        recent_mean = statistics.mean(recent_scores)
        baseline_mean = statistics.mean(baseline_scores)
        drift = recent_mean - baseline_mean

        if abs(drift) < 0.05:
            direction = "stable"
        elif drift > 0:
            direction = "improving"
        else:
            direction = "degrading"

        alert = abs(drift) > 0.10

        if alert:
            logger.warning(
                "Quality drift alert for tenant %s: %.3f (%s)",
                tenant_id,
                drift,
                direction,
            )

        return {
            "tenant_id": tenant_id,
            "drift": round(drift, 4),
            "direction": direction,
            "alert": alert,
            "recent_mean": round(recent_mean, 4),
            "baseline_mean": round(baseline_mean, 4),
        }
