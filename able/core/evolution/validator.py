"""
Change Validator — Step 4 of the evolution cycle.

Validates proposed weight changes against sanity checks
before they're deployed. Catches destructive changes.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .improver import Improvement, WEIGHT_MIN, WEIGHT_MAX, MAX_CHANGE_PCT, THRESHOLD_MIN_GAP

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of validating proposed changes."""

    valid: bool = True
    approved_improvements: List[Improvement] = field(default_factory=list)
    rejected_improvements: List[Improvement] = field(default_factory=list)
    rejection_reasons: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


class ChangeValidator:
    """
    Validates proposed scorer weight changes.

    Checks:
    - Weight bounds [0.0, 1.0]
    - Change rate limit (max 20% per cycle)
    - Tier threshold gap preservation
    - Feature weights sum sanity
    - No tier collapse (all tiers must remain reachable)
    """

    def __init__(self, current_weights: Dict[str, Any]):
        self._current = current_weights

    def validate(self, improvements: List[Improvement]) -> ValidationResult:
        """
        Validate a batch of proposed improvements.

        Returns ValidationResult with approved/rejected lists.
        """
        result = ValidationResult()

        for imp in improvements:
            reasons = self._check_improvement(imp)
            if reasons:
                result.rejected_improvements.append(imp)
                result.rejection_reasons[imp.target] = "; ".join(reasons)
                logger.warning(
                    f"Rejected {imp.target}: {'; '.join(reasons)}"
                )
            else:
                result.approved_improvements.append(imp)

        # Cross-improvement validation
        cross_warnings = self._cross_validate(result.approved_improvements)
        result.warnings.extend(cross_warnings)

        result.valid = len(result.rejected_improvements) == 0
        return result

    def _check_improvement(self, imp: Improvement) -> List[str]:
        """Check a single improvement for issues. Returns list of reasons if invalid."""
        reasons = []

        # Bounds check
        if not (WEIGHT_MIN <= imp.proposed_value <= WEIGHT_MAX):
            reasons.append(
                f"Out of bounds: {imp.proposed_value} not in [{WEIGHT_MIN}, {WEIGHT_MAX}]"
            )

        # Rate limit check
        if abs(imp.change_pct) > MAX_CHANGE_PCT:
            reasons.append(
                f"Change too large: {imp.change_pct*100:.1f}% exceeds {MAX_CHANGE_PCT*100:.0f}% limit"
            )

        # No-op check
        if imp.proposed_value == imp.current_value:
            reasons.append("No change proposed")

        # Threshold-specific checks
        if "tier_thresholds" in imp.target:
            threshold_reasons = self._check_threshold(imp)
            reasons.extend(threshold_reasons)

        return reasons

    def _check_threshold(self, imp: Improvement) -> List[str]:
        """Additional checks for tier threshold changes."""
        reasons = []
        thresholds = self._current.get("tier_thresholds", {})

        t1_max = thresholds.get("tier_1_max", 0.4)
        t2_max = thresholds.get("tier_2_max", 0.7)

        if "tier_1_max" in imp.target:
            new_t1 = imp.proposed_value
            if new_t1 >= t2_max - THRESHOLD_MIN_GAP:
                reasons.append(
                    f"tier_1_max ({new_t1}) too close to tier_2_max ({t2_max}), "
                    f"minimum gap is {THRESHOLD_MIN_GAP}"
                )
            if new_t1 <= 0.1:
                reasons.append(f"tier_1_max ({new_t1}) too low — Tier 1 would be unreachable")

        elif "tier_2_max" in imp.target:
            new_t2 = imp.proposed_value
            if new_t2 <= t1_max + THRESHOLD_MIN_GAP:
                reasons.append(
                    f"tier_2_max ({new_t2}) too close to tier_1_max ({t1_max}), "
                    f"minimum gap is {THRESHOLD_MIN_GAP}"
                )
            if new_t2 >= 0.95:
                reasons.append(f"tier_2_max ({new_t2}) too high — Tier 4 would be unreachable")

        return reasons

    def _cross_validate(self, improvements: List[Improvement]) -> List[str]:
        """
        Cross-validate a batch of improvements together.

        Catches issues that only appear when multiple changes combine.
        """
        warnings = []

        # Check if both thresholds are being modified
        threshold_changes = [
            imp for imp in improvements if "tier_thresholds" in imp.target
        ]
        if len(threshold_changes) >= 2:
            t1_change = next(
                (i for i in threshold_changes if "tier_1_max" in i.target), None
            )
            t2_change = next(
                (i for i in threshold_changes if "tier_2_max" in i.target), None
            )
            if t1_change and t2_change:
                gap = t2_change.proposed_value - t1_change.proposed_value
                if gap < THRESHOLD_MIN_GAP:
                    warnings.append(
                        f"Combined threshold changes would collapse gap to {gap:.3f} "
                        f"(minimum {THRESHOLD_MIN_GAP})"
                    )

        # Check if total feature weight sum is reasonable
        features = dict(self._current.get("features", {}))
        weight_keys = [k for k in features if k.endswith("_weight")]
        for imp in improvements:
            if imp.target.startswith("features."):
                key = imp.target.split(".")[-1]
                if key in features:
                    features[key] = imp.proposed_value

        total_weight = sum(features.get(k, 0) for k in weight_keys)
        if total_weight > 1.5:
            warnings.append(
                f"Total feature weights ({total_weight:.2f}) are high — "
                f"may cause scores to cluster near maximum"
            )
        elif total_weight < 0.5:
            warnings.append(
                f"Total feature weights ({total_weight:.2f}) are low — "
                f"may cause scores to cluster near minimum"
            )

        return warnings
