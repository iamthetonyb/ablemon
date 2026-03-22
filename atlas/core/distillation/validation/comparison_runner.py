"""Side-by-side comparison of teacher vs student model outputs.

Used by ValidationGate stage 2 to evaluate whether a student model
is competitive with its teacher on held-out prompts.

Usage:
    runner = ComparisonRunner()
    result = await runner.compare(
        prompts=["Explain X", "Write Y"],
        model_a="opus-4.6",
        model_b="qwen3.5-27b-atlas-v1",
    )
    print(result["quality_delta"])
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Patterns that suggest hallucinated content.
_HALLUCINATION_MARKERS = re.compile(
    r"(?i)"
    r"(?:as an ai|i cannot|i don't have access|my training data|"
    r"as of my last update|i'm not sure but|i believe that maybe|"
    r"reportedly|allegedly|it is said that|some sources claim)",
)

# Reasoning connectives that indicate structured thought.
_REASONING_MARKERS = re.compile(
    r"\b(?:because|therefore|however|consequently|thus|"
    r"first|second|finally|in contrast|as a result|"
    r"this means|which leads to|the reason)\b",
    re.IGNORECASE,
)


class ComparisonRunner:
    """Compare two models head-to-head on the same prompts.

    The runner sends each prompt to both models, scores the outputs
    using heuristic quality metrics, and aggregates wins/ties.

    For real deployments, wire ``_generate`` to actual model providers.
    The default implementation raises NotImplementedError to force
    explicit integration.
    """

    def __init__(self, tie_margin: float = 0.05) -> None:
        """Args:
            tie_margin: Score difference below this is a tie.
        """
        self.tie_margin = tie_margin

    async def compare(
        self,
        prompts: list[str],
        model_a: str,
        model_b: str,
    ) -> dict[str, Any]:
        """Run same prompts through both models and compare outputs.

        Returns:
            {
                "total": N,
                "model_a_wins": N,
                "model_b_wins": N,
                "ties": N,
                "quality_delta": float,  # mean(b_scores) - mean(a_scores)
                "per_prompt": [{"prompt": ..., "winner": ...}, ...],
            }
        """
        per_prompt: list[dict[str, Any]] = []
        a_wins = 0
        b_wins = 0
        ties = 0
        a_scores: list[float] = []
        b_scores: list[float] = []

        for prompt in prompts:
            out_a = await self._generate(model_a, prompt)
            out_b = await self._generate(model_b, prompt)

            score_a = self._score_output(prompt, out_a)
            score_b = self._score_output(prompt, out_b)

            a_scores.append(score_a)
            b_scores.append(score_b)

            delta = score_b - score_a
            if abs(delta) < self.tie_margin:
                winner = "tie"
                ties += 1
            elif delta > 0:
                winner = "model_b"
                b_wins += 1
            else:
                winner = "model_a"
                a_wins += 1

            per_prompt.append(
                {
                    "prompt": prompt,
                    "model_a_output": out_a,
                    "model_b_output": out_b,
                    "model_a_score": score_a,
                    "model_b_score": score_b,
                    "winner": winner,
                }
            )

        mean_a = sum(a_scores) / len(a_scores) if a_scores else 0.0
        mean_b = sum(b_scores) / len(b_scores) if b_scores else 0.0

        return {
            "total": len(prompts),
            "model_a_wins": a_wins,
            "model_b_wins": b_wins,
            "ties": ties,
            "quality_delta": mean_b - mean_a,
            "per_prompt": per_prompt,
        }

    def _score_output(self, prompt: str, output: str) -> float:
        """Score a single output on a 0.0-1.0 scale.

        Heuristic factors:
        - Length and substance (not too short, not padded)
        - Reasoning presence (because, therefore, however)
        - Relevance to prompt (keyword overlap)
        - Hallucination markers (penalty)
        """
        if not output or not output.strip():
            return 0.0

        score = 0.0
        words = output.split()
        word_count = len(words)

        # --- Length / substance (0.0 - 0.30) ---
        if word_count < 10:
            score += 0.05
        elif word_count < 50:
            score += 0.15
        elif word_count <= 500:
            score += 0.30
        else:
            # Diminishing returns past 500 words — slight penalty for bloat.
            score += 0.25

        # --- Reasoning markers (0.0 - 0.25) ---
        reasoning_hits = len(_REASONING_MARKERS.findall(output))
        score += min(reasoning_hits * 0.05, 0.25)

        # --- Relevance to prompt (0.0 - 0.25) ---
        prompt_words = set(prompt.lower().split())
        # Drop stop words.
        stop = {"the", "a", "an", "is", "are", "to", "of", "and", "in", "for", "it"}
        prompt_keywords = prompt_words - stop
        if prompt_keywords:
            output_lower = output.lower()
            overlap = sum(1 for w in prompt_keywords if w in output_lower)
            relevance = overlap / len(prompt_keywords)
            score += relevance * 0.25

        # --- Hallucination penalty (0.0 - -0.20) ---
        hallucination_hits = len(_HALLUCINATION_MARKERS.findall(output))
        score -= min(hallucination_hits * 0.05, 0.20)

        # --- Structure bonus (0.0 - 0.20) ---
        has_lists = bool(re.search(r"(?m)^[\s]*[-*\d]+[.)]\s", output))
        has_headers = bool(re.search(r"(?m)^#+\s", output))
        has_code = "```" in output
        structure_signals = sum([has_lists, has_headers, has_code])
        score += min(structure_signals * 0.07, 0.20)

        return max(0.0, min(1.0, score))

    async def _generate(self, model: str, prompt: str) -> str:
        """Generate output from a model.

        Override this method to wire up actual model providers.
        The default raises NotImplementedError so callers know
        they need to integrate their provider layer.
        """
        raise NotImplementedError(
            f"ComparisonRunner._generate() must be overridden to call model '{model}'. "
            "Subclass ComparisonRunner and implement _generate() with your provider."
        )
