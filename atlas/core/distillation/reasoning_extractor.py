"""
Reasoning Extractor — Normalize chain-of-thought from model responses.

Handles multiple reasoning formats:
- <think>...</think> blocks (Qwen, DeepSeek, etc.)
- Step-by-step patterns ("Step 1:", "First,", numbered lists)
- Tool-use decision chains ("I'll use X to...", "Calling tool...")

Normalizes all to: <think>{reasoning}</think>{answer}

Usage:
    from atlas.core.distillation.reasoning_extractor import ReasoningExtractor

    extractor = ReasoningExtractor()
    result = extractor.extract("Let me think step by step. Step 1: ...")
    # result.reasoning = "Step 1: ..."
    # result.answer = "..."
    # result.normalized = "<think>Step 1: ...</think>..."
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Patterns for detecting reasoning blocks ────────────────────────────

# XML-style thinking tags (case-insensitive, handles nesting)
_THINK_TAG_RE = re.compile(
    r"<think(?:ing)?>(.*?)</think(?:ing)?>",
    re.DOTALL | re.IGNORECASE,
)

# Step-by-step patterns
_STEP_NUMBERED_RE = re.compile(
    r"^(?:Step\s+\d+[.:]\s*)",
    re.MULTILINE | re.IGNORECASE,
)
_STEP_ORDINAL_RE = re.compile(
    r"^(?:First(?:ly)?|Second(?:ly)?|Third(?:ly)?|Next|Then|Finally|Lastly|"
    r"Additionally|Furthermore|Moreover|In conclusion|To summarize)[,:]?\s",
    re.MULTILINE | re.IGNORECASE,
)

# Tool-use decision chains
_TOOL_CHAIN_RE = re.compile(
    r"(?:I(?:'ll| will| need to| should) (?:use|call|invoke|run|execute|check)|"
    r"(?:Using|Calling|Invoking|Running|Executing) (?:the )?(?:tool|function|command|script)|"
    r"Let me (?:use|call|invoke|check|run|try))",
    re.IGNORECASE,
)

# "Let me think" / "Let me reason" preambles
_PREAMBLE_RE = re.compile(
    r"^(?:Let me (?:think|reason|analyze|consider|break this down|work through)"
    r"[^.]*\.?\s*)",
    re.MULTILINE | re.IGNORECASE,
)

# Numbered list items (1. / 1) style)
_NUMBERED_LIST_RE = re.compile(
    r"^\d+[.)]\s+",
    re.MULTILINE,
)

# Final answer separators
_ANSWER_SEPARATOR_RE = re.compile(
    r"\n(?:(?:Final )?(?:Answer|Response|Result|Output|Conclusion|Summary)"
    r"\s*[:=]\s*\n?|---+\n?|===+\n?)",
    re.IGNORECASE,
)


@dataclass
class ExtractionResult:
    """Result of reasoning extraction."""

    reasoning: str = ""
    answer: str = ""
    format_detected: str = "none"  # think_tag, step_by_step, tool_chain, none
    normalized: str = ""
    confidence: float = 0.0  # 0.0-1.0, how confident we are in the split

    @property
    def has_reasoning(self) -> bool:
        return bool(self.reasoning.strip())


@dataclass
class ExtractionStats:
    """Aggregate stats across many extractions."""

    total: int = 0
    with_reasoning: int = 0
    by_format: dict = field(default_factory=lambda: {
        "think_tag": 0,
        "step_by_step": 0,
        "tool_chain": 0,
        "none": 0,
    })


class ReasoningExtractor:
    """
    Extract chain-of-thought from model responses.

    Handles:
    - <think>...</think> blocks
    - Step-by-step patterns ("Step 1:", "First,", etc.)
    - Tool-use decision chains
    Normalizes all to: <think>{reasoning}</think>{answer} format
    """

    def __init__(
        self,
        min_reasoning_words: int = 5,
        min_confidence: float = 0.3,
    ):
        """
        Args:
            min_reasoning_words: Minimum words to consider as real reasoning
            min_confidence: Below this, mark format as 'none'
        """
        self.min_reasoning_words = min_reasoning_words
        self.min_confidence = min_confidence
        self._stats = ExtractionStats()

    def extract(self, text: str) -> ExtractionResult:
        """
        Extract reasoning and answer from a model response.

        Tries extraction methods in priority order:
        1. Explicit <think> tags (highest confidence)
        2. Step-by-step patterns
        3. Tool-use chains
        4. Falls back to no-reasoning (full text is the answer)

        Args:
            text: Raw model response text

        Returns:
            ExtractionResult with reasoning, answer, format, and normalized output
        """
        if not text or not text.strip():
            return ExtractionResult(answer="", confidence=0.0)

        self._stats.total += 1

        # Try each extractor in priority order
        result = self._try_think_tags(text)
        if not result:
            result = self._try_step_by_step(text)
        if not result:
            result = self._try_tool_chain(text)
        if not result:
            result = ExtractionResult(
                answer=text.strip(),
                format_detected="none",
                normalized=text.strip(),
                confidence=1.0,  # Confident there's no reasoning
            )

        # Apply confidence threshold
        if result.confidence < self.min_confidence:
            result.format_detected = "none"
            result.reasoning = ""
            result.answer = text.strip()
            result.normalized = text.strip()

        # Update stats
        self._stats.by_format[result.format_detected] = (
            self._stats.by_format.get(result.format_detected, 0) + 1
        )
        if result.has_reasoning:
            self._stats.with_reasoning += 1

        return result

    def normalize(self, text: str) -> str:
        """
        Convenience method: extract and return normalized form only.

        Args:
            text: Raw model response

        Returns:
            Normalized string in <think>{reasoning}</think>{answer} format
        """
        return self.extract(text).normalized

    def strip_reasoning(self, text: str) -> str:
        """
        Remove all reasoning, return only the answer.

        Useful for cleaning "thinking bleed" from model outputs.

        Args:
            text: Raw model response

        Returns:
            Just the answer portion
        """
        return self.extract(text).answer

    def get_stats(self) -> dict:
        """Return extraction statistics."""
        return {
            "total": self._stats.total,
            "with_reasoning": self._stats.with_reasoning,
            "reasoning_rate": (
                self._stats.with_reasoning / self._stats.total
                if self._stats.total > 0
                else 0.0
            ),
            "by_format": dict(self._stats.by_format),
        }

    def reset_stats(self) -> None:
        """Reset extraction statistics."""
        self._stats = ExtractionStats()

    # ── Private extraction methods ─────────────────────────────────────

    def _try_think_tags(self, text: str) -> Optional[ExtractionResult]:
        """Extract from <think>...</think> blocks."""
        matches = _THINK_TAG_RE.findall(text)
        if not matches:
            return None

        reasoning_parts = [m.strip() for m in matches if m.strip()]
        if not reasoning_parts:
            return None

        reasoning = "\n\n".join(reasoning_parts)
        if len(reasoning.split()) < self.min_reasoning_words:
            return None

        # Everything outside think tags is the answer
        answer = _THINK_TAG_RE.sub("", text).strip()

        return ExtractionResult(
            reasoning=reasoning,
            answer=answer,
            format_detected="think_tag",
            normalized=f"<think>{reasoning}</think>{answer}",
            confidence=0.95,
        )

    def _try_step_by_step(self, text: str) -> Optional[ExtractionResult]:
        """Extract from step-by-step reasoning patterns."""
        # Count step indicators
        step_count = len(_STEP_NUMBERED_RE.findall(text))
        ordinal_count = len(_STEP_ORDINAL_RE.findall(text))
        numbered_count = len(_NUMBERED_LIST_RE.findall(text))
        has_preamble = bool(_PREAMBLE_RE.search(text))

        total_indicators = step_count + ordinal_count
        if total_indicators < 2 and not (has_preamble and numbered_count >= 2):
            return None

        # Try to split at answer separator
        separator_match = _ANSWER_SEPARATOR_RE.search(text)
        if separator_match:
            reasoning = text[: separator_match.start()].strip()
            answer = text[separator_match.end() :].strip()
            confidence = 0.8
        else:
            # Heuristic: if we have step markers, the last paragraph
            # after the last step marker is likely the answer
            paragraphs = text.strip().split("\n\n")
            if len(paragraphs) >= 2:
                # Check if last paragraph has step indicators
                last_para = paragraphs[-1]
                if not (_STEP_NUMBERED_RE.search(last_para) or
                        _STEP_ORDINAL_RE.search(last_para)):
                    reasoning = "\n\n".join(paragraphs[:-1])
                    answer = last_para
                    confidence = 0.6
                else:
                    # All paragraphs are reasoning — no clear answer split
                    reasoning = text.strip()
                    answer = text.strip()
                    confidence = 0.4
            else:
                reasoning = text.strip()
                answer = text.strip()
                confidence = 0.3

        # Remove preamble from reasoning
        reasoning = _PREAMBLE_RE.sub("", reasoning).strip()

        if len(reasoning.split()) < self.min_reasoning_words:
            return None

        return ExtractionResult(
            reasoning=reasoning,
            answer=answer,
            format_detected="step_by_step",
            normalized=f"<think>{reasoning}</think>{answer}",
            confidence=confidence,
        )

    def _try_tool_chain(self, text: str) -> Optional[ExtractionResult]:
        """Extract from tool-use decision chains."""
        tool_matches = _TOOL_CHAIN_RE.findall(text)
        if len(tool_matches) < 1:
            return None

        # Find the last tool-use indicator — everything after is likely the answer
        last_match = None
        for m in _TOOL_CHAIN_RE.finditer(text):
            last_match = m

        if not last_match:
            return None

        # Look for a results/output section after tool usage
        post_tool = text[last_match.end() :]
        result_match = re.search(
            r"\n(?:Result|Output|Response|Here(?:'s| is)|Based on|The (?:result|output|answer))",
            post_tool,
            re.IGNORECASE,
        )

        if result_match:
            reasoning = text[: last_match.end() + result_match.start()].strip()
            answer = post_tool[result_match.start() :].strip()
            confidence = 0.7
        else:
            # Fallback: reasoning is everything up to the last tool mention's paragraph
            paragraphs = text.strip().split("\n\n")
            if len(paragraphs) >= 2:
                reasoning = "\n\n".join(paragraphs[:-1])
                answer = paragraphs[-1]
                confidence = 0.5
            else:
                reasoning = text.strip()
                answer = text.strip()
                confidence = 0.3

        if len(reasoning.split()) < self.min_reasoning_words:
            return None

        return ExtractionResult(
            reasoning=reasoning,
            answer=answer,
            format_detected="tool_chain",
            normalized=f"<think>{reasoning}</think>{answer}",
            confidence=confidence,
        )
