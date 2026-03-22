"""
Reasoning Extractor — separates chain-of-thought from model responses.

Handles multiple CoT formats emitted by different model families:
  1. <think>...</think> blocks (Qwen, DeepSeek, Claude)
  2. Step-by-step patterns ("Step 1:", "First,", "Let me think...")
  3. Tool-use decision chains ("I'll use X to...", "Calling tool...")
  4. "Thinking: ..." preambles (Nemotron)

Used by CorpusBuilder to normalize all training pairs into a standard
<think>{reasoning}</think>{answer} format for distillation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ExtractionResult:
    """Result of reasoning extraction from a model response."""

    thinking: str | None  # Extracted reasoning (None if no reasoning found)
    answer: str  # Clean answer without reasoning
    method: str  # "think_tags" | "step_by_step" | "thinking_preamble" | "tool_chain" | "none"


class ReasoningExtractor:
    """Extract chain-of-thought from model responses.

    Handles multiple formats:
    1. <think>...</think> blocks (Qwen, DeepSeek, Claude)
    2. Step-by-step patterns ("Step 1:", "First,", "Let me think...")
    3. Tool-use decision chains
    4. "Thinking: ..." preambles (Nemotron)
    """

    # Regex patterns for different thinking formats
    THINK_TAG = re.compile(r"<think>(.*?)</think>", re.DOTALL)
    THINKING_PREAMBLE = re.compile(r"^Thinking:?\s*(.*?)\n\n", re.DOTALL)
    STEP_BY_STEP = re.compile(
        r"^((?:(?:Step \d+|First|Second|Third|Finally|Let me think|"
        r"Let me analyze|I need to)[^\n]*\n)+)",
        re.MULTILINE,
    )
    TOOL_CHAIN = re.compile(
        r"^((?:(?:I'll use|I will use|Calling|Using tool|Let me call|"
        r"I need to call|Tool:)[^\n]*\n)+)",
        re.MULTILINE,
    )

    def extract(self, text: str) -> ExtractionResult:
        """Extract reasoning from response text.

        Tries extraction methods in priority order:
        1. <think> tags (most explicit)
        2. Thinking: preamble
        3. Tool chain patterns
        4. Step-by-step patterns

        Returns ExtractionResult with separated thinking and answer.
        """
        if not text or not text.strip():
            return ExtractionResult(thinking=None, answer=text or "", method="none")

        # 1. <think>...</think> tags — highest priority, most explicit
        think_matches = list(self.THINK_TAG.finditer(text))
        if think_matches:
            thinking_parts = [m.group(1).strip() for m in think_matches]
            thinking = "\n\n".join(thinking_parts)
            answer = self.THINK_TAG.sub("", text).strip()
            if thinking:
                return ExtractionResult(thinking=thinking, answer=answer, method="think_tags")

        # 2. "Thinking: ..." preamble
        preamble_match = self.THINKING_PREAMBLE.match(text)
        if preamble_match:
            thinking = preamble_match.group(1).strip()
            answer = text[preamble_match.end() :].strip()
            if thinking:
                return ExtractionResult(
                    thinking=thinking, answer=answer, method="thinking_preamble"
                )

        # 3. Tool-use decision chains
        tool_match = self.TOOL_CHAIN.match(text)
        if tool_match:
            thinking = tool_match.group(1).strip()
            answer = text[tool_match.end() :].strip()
            if thinking and answer:
                return ExtractionResult(
                    thinking=thinking, answer=answer, method="tool_chain"
                )

        # 4. Step-by-step patterns
        step_match = self.STEP_BY_STEP.match(text)
        if step_match:
            thinking = step_match.group(1).strip()
            answer = text[step_match.end() :].strip()
            if thinking and answer:
                return ExtractionResult(
                    thinking=thinking, answer=answer, method="step_by_step"
                )

        # No reasoning found
        return ExtractionResult(thinking=None, answer=text.strip(), method="none")

    def normalize(self, thinking: str, answer: str) -> str:
        """Normalize to standard format: <think>{reasoning}</think>{answer}"""
        return f"<think>{thinking.strip()}</think>\n{answer.strip()}"
