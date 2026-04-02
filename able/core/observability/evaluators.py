"""
ABLE Evaluators — Response quality scoring for observability and training.

Evaluators:
  - HallucinationEvaluator  — pattern-based hallucination detection
  - QACorrectnessEvaluator  — checks if response addresses the input
  - SkillAdherenceEvaluator  — checks skill spec compliance
  - ToneEvaluator            — checks personality/tone match
  - ABLEEvaluator            — orchestrates all evaluators, gates training data
"""

from __future__ import annotations

import re
import logging
from typing import Dict, Optional

try:
    from able.core.factcheck.checker import HALLUCINATION_MARKERS
except ImportError:
    from able.core.factcheck.checker import HALLUCINATION_MARKERS

logger = logging.getLogger(__name__)


class HallucinationEvaluator:
    """Pattern-based hallucination detection (reuses patterns from factcheck/)."""

    def score(self, input_text: str, output_text: str) -> float:
        """
        Return 1.0 (no hallucination) down to 0.0 (severe hallucination).

        Each marker match subtracts 0.15 (capped at 0.0).
        """
        hits = 0
        for pattern in HALLUCINATION_MARKERS:
            if re.search(pattern, output_text, re.IGNORECASE):
                hits += 1
        return max(0.0, 1.0 - hits * 0.15)


class QACorrectnessEvaluator:
    """Checks if the response addresses the input question/task."""

    # Words that typically signal a question was asked
    _QUESTION_WORDS = {"what", "how", "why", "when", "where", "who", "which", "can", "could", "would", "should"}

    def score(self, input_text: str, output_text: str) -> float:
        """
        Heuristic correctness score.  1.0 = good, 0.0 = bad.

        Checks:
          - Non-empty output
          - Output shares topical overlap with the input
          - Output length is proportional to question complexity
        """
        if not output_text or not output_text.strip():
            return 0.0

        input_lower = input_text.lower()
        output_lower = output_text.lower()

        # Extract meaningful words (>3 chars) from input
        input_words = {
            w for w in re.findall(r"\b\w{4,}\b", input_lower)
        } - self._QUESTION_WORDS

        if not input_words:
            # Very short input — accept any non-empty output
            return 0.8

        # Measure topical overlap
        overlap = sum(1 for w in input_words if w in output_lower)
        overlap_ratio = overlap / len(input_words) if input_words else 0

        score = 0.4 + (overlap_ratio * 0.6)

        # Penalise extremely short answers to complex questions
        if len(input_words) > 5 and len(output_text.split()) < 10:
            score -= 0.2

        return max(0.0, min(1.0, score))


class SkillAdherenceEvaluator:
    """Checks if the response follows the skill specification."""

    def score(
        self,
        input_text: str,
        output_text: str,
        skill_spec: Optional[str] = None,
    ) -> float:
        """
        Compare output against the skill spec keywords/directives.

        Without a skill_spec, returns a neutral 0.7 (cannot evaluate).
        """
        if not skill_spec:
            return 0.7

        if not output_text or not output_text.strip():
            return 0.0

        spec_lower = skill_spec.lower()
        output_lower = output_text.lower()

        # Extract directive keywords from spec
        spec_words = {
            w for w in re.findall(r"\b\w{4,}\b", spec_lower)
        }
        if not spec_words:
            return 0.7

        overlap = sum(1 for w in spec_words if w in output_lower)
        ratio = overlap / len(spec_words)

        return max(0.0, min(1.0, 0.3 + ratio * 0.7))


class ToneEvaluator:
    """Checks if the response matches expected tone/personality."""

    # ABLE personality signals (from SOUL.md)
    _SYCOPHANCY_PHRASES = [
        "great question",
        "that's a fantastic",
        "absolutely!",
        "i'd be happy to help",
        "i hope this helps",
        "let me know if you have any questions",
        "wonderful idea",
        "i really appreciate",
    ]

    _DIRECT_SIGNALS = [
        r"^[A-Z]",                   # Starts with a statement
        r"\bhere's\b",               # Direct delivery
        r"\bdon't\b",                # Assertive
        r"\bwon't\b",
        r"\binstead\b",              # Offering alternatives
    ]

    def score(
        self,
        input_text: str,
        output_text: str,
        personality: Optional[str] = None,
    ) -> float:
        """
        Score tone alignment.  1.0 = on-brand, 0.0 = off-brand.

        Default personality target: direct, non-sycophantic (per SOUL.md).
        """
        if not output_text or not output_text.strip():
            return 0.0

        output_lower = output_text.lower()
        score = 0.8  # start with generous baseline

        # Penalise sycophancy
        for phrase in self._SYCOPHANCY_PHRASES:
            if phrase in output_lower:
                score -= 0.15

        # Reward directness
        for pattern in self._DIRECT_SIGNALS:
            if re.search(pattern, output_text):
                score += 0.03

        return max(0.0, min(1.0, score))


class ABLEEvaluator:
    """
    Orchestrates all evaluators.  Results feed into:
      - corpus builder (distillation pipeline)
      - overnight daemon (evolution)
      - tenant dashboard
    """

    def __init__(self) -> None:
        self.evaluators: Dict[str, object] = {
            "hallucination": HallucinationEvaluator(),
            "correctness": QACorrectnessEvaluator(),
            "skill_adherence": SkillAdherenceEvaluator(),
            "tone": ToneEvaluator(),
        }

    def evaluate(
        self,
        input_text: str,
        output_text: str,
        context: Optional[Dict] = None,
    ) -> Dict[str, float]:
        """Run all evaluators and return a scores dict."""
        ctx = context or {}
        return {
            "hallucination": self.evaluators["hallucination"].score(
                input_text, output_text
            ),
            "correctness": self.evaluators["correctness"].score(
                input_text, output_text
            ),
            "skill_adherence": self.evaluators["skill_adherence"].score(
                input_text, output_text, skill_spec=ctx.get("skill_spec")
            ),
            "tone": self.evaluators["tone"].score(
                input_text, output_text, personality=ctx.get("personality")
            ),
        }

    def score_for_training(
        self,
        input_text: str,
        output_text: str,
        context: Optional[Dict] = None,
    ) -> Dict:
        """
        Quality gate for the distillation corpus.

        Only responses with average score >= 0.8 become training data.
        Returns: {eligible: bool, scores: dict, average: float}
        """
        scores = self.evaluate(input_text, output_text, context)
        average = sum(scores.values()) / len(scores) if scores else 0.0
        return {
            "eligible": average >= 0.8,
            "scores": scores,
            "average": round(average, 4),
        }
