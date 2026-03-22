"""Validation gate for distilled student models.

4-stage pipeline: eval suite -> teacher comparison -> security red-team -> regression check.
"""

from atlas.core.distillation.validation.validation_gate import (
    GateDecision,
    StageResult,
    ValidationGate,
    ValidationResult,
)
from atlas.core.distillation.validation.comparison_runner import ComparisonRunner

__all__ = [
    "ComparisonRunner",
    "GateDecision",
    "StageResult",
    "ValidationGate",
    "ValidationResult",
]
