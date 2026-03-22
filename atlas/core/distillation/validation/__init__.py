"""
Validation gate for distilled student models.

4-stage validation pipeline:
  Stage 1: Promptfoo eval suite (tool use, skill adherence, reasoning, domain)
  Stage 2: Teacher-student comparison via held-out test set
  Stage 3: Promptfoo red-team (67+ attack plugins)
  Stage 4: Regression check vs previous student version

Decision matrix:
  ALL pass  -> DEPLOY
  S1/S2 fail -> ITERATE (retrain with more data)
  S3 fail   -> BLOCK (security risk)
  S4 regress -> KEEP PREVIOUS
"""

from .validation_gate import ValidationGate, ValidationResult, StageResult
from .comparison_runner import ComparisonRunner, ComparisonReport

__all__ = [
    "ValidationGate",
    "ValidationResult",
    "StageResult",
    "ComparisonRunner",
    "ComparisonReport",
]
