"""
Weight Improver — Step 3 of the evolution cycle.

Translates analysis recommendations into concrete weight changes.
Applies safety bounds and rate limits to prevent destructive updates.
"""

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .analyzer import AnalysisResult

logger = logging.getLogger(__name__)

# Maximum change per cycle (% of current value)
MAX_CHANGE_PCT = 0.20

# Absolute bounds
WEIGHT_MIN = 0.0
WEIGHT_MAX = 1.0
THRESHOLD_MIN_GAP = 0.15  # Minimum gap between tier thresholds


@dataclass
class Improvement:
    """A single proposed weight/config change."""

    target: str  # e.g. "features.safety_critical_weight"
    current_value: float
    proposed_value: float
    change_pct: float
    reason: str
    source: str = "rule_based"  # "m2.7" or "rule_based"

    @property
    def is_valid(self) -> bool:
        """Check if the proposed change is within bounds."""
        return (
            self.proposed_value != self.current_value
            and WEIGHT_MIN <= self.proposed_value <= WEIGHT_MAX
            and abs(self.change_pct) <= MAX_CHANGE_PCT
        )


class WeightImprover:
    """
    Generates concrete weight changes from analysis results.

    Applies safety constraints:
    - Max 20% change per value per cycle
    - Weights stay in [0.0, 1.0]
    - Tier thresholds maintain minimum gap
    - Changes are always reversible
    """

    def __init__(self, current_weights: Dict[str, Any]):
        """
        Args:
            current_weights: Current scorer_weights.yaml contents
        """
        self._current = current_weights

    def generate_improvements(
        self, analysis: AnalysisResult
    ) -> List[Improvement]:
        """
        Convert analysis recommendations into bounded improvements.
        """
        improvements = []

        for rec in analysis.recommendations:
            rec_type = rec.get("type", "")

            if rec_type == "weight_adjustment":
                imp = self._weight_adjustment(rec, analysis.analysis_source)
                if imp and imp.is_valid:
                    improvements.append(imp)

            elif rec_type == "threshold_adjustment":
                imp = self._threshold_adjustment(rec, analysis.analysis_source)
                if imp and imp.is_valid:
                    improvements.append(imp)

            elif rec_type == "domain_adjustment":
                imp = self._domain_adjustment(rec, analysis.analysis_source)
                if imp and imp.is_valid:
                    improvements.append(imp)

        return improvements

    def apply_improvements(
        self, improvements: List[Improvement]
    ) -> Dict[str, Any]:
        """
        Apply a list of improvements to a copy of current weights.

        Returns new weights dict (does not modify current).
        """
        new_weights = copy.deepcopy(self._current)

        for imp in improvements:
            if not imp.is_valid:
                logger.warning(f"Skipping invalid improvement: {imp.target}")
                continue

            parts = imp.target.split(".")
            target = new_weights
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = round(imp.proposed_value, 4)

        # Bump version
        new_weights["version"] = new_weights.get("version", 1) + 1

        return new_weights

    def _weight_adjustment(
        self, rec: Dict[str, Any], source: str
    ) -> Optional[Improvement]:
        """Generate a feature weight adjustment."""
        target_key = rec.get("target", "")
        features = self._current.get("features", {})

        current = features.get(target_key)
        if current is None:
            logger.warning(f"Unknown weight target: {target_key}")
            return None

        proposed = rec.get("proposed")
        if proposed is not None:
            # M2.7 gave a specific value — clamp the change
            change_pct = (proposed - current) / max(abs(current), 0.01)
            if abs(change_pct) > MAX_CHANGE_PCT:
                # Clamp to max change
                direction = 1 if proposed > current else -1
                proposed = current + (current * MAX_CHANGE_PCT * direction)
                change_pct = MAX_CHANGE_PCT * direction
        else:
            # Rule-based direction hint
            direction = rec.get("direction", "increase")
            step = current * 0.10  # 10% step
            if direction == "decrease":
                step = -step
            proposed = current + step
            change_pct = step / max(abs(current), 0.01)

        proposed = max(WEIGHT_MIN, min(WEIGHT_MAX, round(proposed, 4)))

        return Improvement(
            target=f"features.{target_key}",
            current_value=current,
            proposed_value=proposed,
            change_pct=round(change_pct, 4),
            reason=rec.get("reason", ""),
            source=source,
        )

    def _threshold_adjustment(
        self, rec: Dict[str, Any], source: str
    ) -> Optional[Improvement]:
        """Generate a tier threshold adjustment."""
        target_key = rec.get("target", "")
        thresholds = self._current.get("tier_thresholds", {})

        current = thresholds.get(target_key)
        if current is None:
            logger.warning(f"Unknown threshold target: {target_key}")
            return None

        direction = rec.get("direction", "increase")
        step = current * 0.05  # 5% step for thresholds (conservative)
        if direction == "decrease":
            step = -step

        proposed = round(current + step, 4)
        change_pct = step / max(abs(current), 0.01)

        # Validate tier gap constraint
        t1_max = thresholds.get("tier_1_max", 0.4)
        t2_max = thresholds.get("tier_2_max", 0.7)

        if target_key == "tier_1_max":
            proposed = min(proposed, t2_max - THRESHOLD_MIN_GAP)
        elif target_key == "tier_2_max":
            proposed = max(proposed, t1_max + THRESHOLD_MIN_GAP)

        proposed = max(0.1, min(0.9, proposed))

        return Improvement(
            target=f"tier_thresholds.{target_key}",
            current_value=current,
            proposed_value=proposed,
            change_pct=round(change_pct, 4),
            reason=rec.get("reason", ""),
            source=source,
        )

    def _domain_adjustment(
        self, rec: Dict[str, Any], source: str
    ) -> Optional[Improvement]:
        """Generate a domain-specific adjustment."""
        domain = rec.get("target", "")
        adjustments = self._current.get("domain_adjustments", {})

        current = adjustments.get(domain, 0.0)
        direction = rec.get("direction", "increase")
        step = 0.02  # Small absolute step for domain adjustments
        if direction == "decrease":
            step = -step

        proposed = round(current + step, 4)
        change_pct = step / max(abs(current), 0.01) if current != 0 else step

        # Clamp domain adjustments to reasonable range
        proposed = max(-0.20, min(0.25, proposed))

        return Improvement(
            target=f"domain_adjustments.{domain}",
            current_value=current,
            proposed_value=proposed,
            change_pct=round(change_pct, 4),
            reason=rec.get("reason", ""),
            source=source,
        )
