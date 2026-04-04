"""
Training data formatter — converts HarvestedConversation objects into
ChatML training format suitable for fine-tuning.

Produces records compatible with Unsloth / HuggingFace SFT trainers with
``train_on_responses_only`` masking support.
"""

from __future__ import annotations

import logging
import uuid
from typing import Sequence

from able.core.distillation.harvesters.base import HarvestedConversation
from able.core.distillation.models import TrainingPair
from able.core.distillation.reasoning_extractor import ReasoningExtractor

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are Able, the operator-facing voice of ABLE (Autonomous Business & Learning Engine). "
    "Be direct, accurate, helpful, and lightly warm."
)


class TrainingFormatter:
    """Standardises harvested data to ChatML training format."""

    _SOURCE_QUALITY_BONUS = {
        "claude_code": 0.16,
        "cowork": 0.16,
        "able_cli": 0.14,
        "able_interaction": 0.12,
        "codex": 0.12,
        "chatgpt": 0.10,
        "opencli": 0.08,
        "antigravity": 0.06,
        "inbox": 0.02,
    }

    def __init__(self) -> None:
        self.reasoning_extractor = ReasoningExtractor()

    def normalize(self, conversation: HarvestedConversation) -> TrainingPair:
        """Convert a harvested conversation into the canonical pair type."""
        prompt, response = self._extract_prompt_response(conversation)
        extraction = self.reasoning_extractor.extract(response)
        thinking_blocks = [
            block.strip() for block in conversation.thinking_blocks if block and block.strip()
        ]
        thinking = "\n\n".join(thinking_blocks) if thinking_blocks else extraction.thinking
        clean_response = extraction.answer
        quality_score = self._score_conversation(
            conversation,
            prompt=prompt,
            response=clean_response,
            thinking=thinking,
        )

        metadata = dict(conversation.metadata)
        tenant_id = metadata.get("tenant_id", "default")
        response_accepted = metadata.get("response_accepted", True)
        escalated = metadata.get("escalated", False)

        return TrainingPair(
            id=conversation.id or str(uuid.uuid4()),
            prompt=prompt,
            response=clean_response,
            domain=conversation.domain or metadata.get("domain", "general"),
            quality_score=quality_score,
            source=conversation.source,
            teacher_model=conversation.model,
            thinking=thinking,
            tenant_id=tenant_id,
            messages=[
                {
                    "role": msg.get("role", ""),
                    "content": msg.get("content", ""),
                }
                for msg in conversation.messages
                if msg.get("role") in ("user", "assistant", "system")
                and msg.get("content")
            ],
            response_accepted=response_accepted,
            escalated=escalated,
            metadata=metadata,
            content_hash=conversation.content_hash,
        )

    def normalize_batch(
        self,
        conversations: Sequence[HarvestedConversation],
    ) -> list[TrainingPair]:
        """Normalize a batch into canonical typed pairs."""
        return [self.normalize(c) for c in conversations]

    def format(
        self,
        conversation: HarvestedConversation,
        system_prompt: str = "",
    ) -> dict:
        """Convert a single HarvestedConversation to ChatML format.

        Returns::

            {
                "conversations": [
                    {"role": "system", "content": "..."},
                    {"role": "user", "content": "..."},
                    {"role": "assistant", "content": "..."},
                    ...
                ],
                "metadata": {
                    "source": "...",
                    "teacher_model": "...",
                    "domain": "...",
                    "quality_score": 0.0,
                    "content_hash": "...",
                    "tenant_id": "default",
                }
            }

        All assistant turns are tagged for ``train_on_responses_only``
        masking by convention (the trainer reads role == "assistant").
        """
        pair = self.normalize(conversation)
        return self.to_chatml(pair, system_prompt=system_prompt)

    def format_batch(
        self,
        conversations: Sequence[HarvestedConversation],
        system_prompt: str = "",
    ) -> list[dict]:
        """Format a batch of conversations."""
        return [self.format(c, system_prompt) for c in conversations]

    def to_chatml(self, pair: TrainingPair, system_prompt: str = "") -> dict:
        """Export a canonical pair to ChatML."""
        return pair.to_chatml(system_prompt=system_prompt or _DEFAULT_SYSTEM_PROMPT)

    def deduplicate(self, formatted: list[dict]) -> list[dict]:
        """Remove duplicates by ``content_hash`` across platforms."""
        seen: set[str] = set()
        deduped: list[dict] = []
        for record in formatted:
            h = record.get("metadata", {}).get("content_hash", "")
            if h and h in seen:
                continue
            if h:
                seen.add(h)
            deduped.append(record)
        return deduped

    def deduplicate_pairs(self, pairs: Sequence[TrainingPair]) -> list[TrainingPair]:
        """Remove duplicate canonical pairs by content hash."""
        seen: set[str] = set()
        deduped: list[TrainingPair] = []
        for pair in pairs:
            if pair.content_hash in seen:
                continue
            seen.add(pair.content_hash)
            deduped.append(pair)
        return deduped

    def _extract_prompt_response(
        self, conversation: HarvestedConversation
    ) -> tuple[str, str]:
        """Take the last substantive user/assistant exchange from the conversation."""
        filtered = [
            msg
            for msg in conversation.messages
            if msg.get("role") in ("user", "assistant") and msg.get("content")
        ]
        assistant_index = None
        for idx in range(len(filtered) - 1, -1, -1):
            if filtered[idx]["role"] == "assistant":
                assistant_index = idx
                break

        if assistant_index is None:
            return "", ""

        prompt = ""
        for idx in range(assistant_index - 1, -1, -1):
            if filtered[idx]["role"] == "user":
                prompt = filtered[idx]["content"]
                break

        response = filtered[assistant_index]["content"]
        return prompt, response

    # Model name fragments that indicate high-quality teacher models
    _PREMIUM_MODELS = ("opus", "claude-3-opus", "claude-opus", "gpt-4o", "gpt-5", "gpt-4-turbo")
    _MID_MODELS = ("sonnet", "claude-3-sonnet", "claude-sonnet", "gpt-4", "codex")

    def _score_conversation(
        self,
        conversation: HarvestedConversation,
        *,
        prompt: str,
        response: str,
        thinking: str | None,
    ) -> float:
        """Assign a heuristic quality score for corpus filtering.

        Scoring signals (all rule-based, <1ms):
        - Source quality bonus (Claude Code > inbox)
        - Content depth (substantive turns, response length gradient)
        - Teacher model tier (Opus > Sonnet > Mini)
        - Reasoning traces, tool use, domain presence
        - Penalties for terse responses, missing content, scaffolding leaks
        """
        score = 0.45
        score += self._SOURCE_QUALITY_BONUS.get(conversation.source, 0.05)

        substantive_turns = sum(
            1 for msg in conversation.messages if len(msg.get("content", "").strip()) >= 40
        )
        score += min(substantive_turns, 4) * 0.04

        if conversation.domain:
            score += 0.05
        if prompt and len(prompt.strip()) >= 60:
            score += 0.05
        if response and len(response.strip()) >= 80:
            score += 0.05
        if conversation.tool_uses:
            score += 0.10
        if thinking:
            score += 0.08

        # Response depth gradient — longer responses teach more
        resp_len = len(response.strip()) if response else 0
        if resp_len >= 500:
            score += 0.06
        elif resp_len >= 200:
            score += 0.03
        elif resp_len < 20:
            score -= 0.15  # Terse responses are poor training data

        # Teacher model tier — premium model outputs are more valuable.
        # Exclude mini/nano/haiku — they're student-tier, not teacher-tier.
        model = (conversation.model or "").lower()
        is_small = any(s in model for s in ("mini", "nano", "haiku", "flash"))
        if not is_small and any(t in model for t in self._PREMIUM_MODELS):
            score += 0.08
        elif not is_small and any(t in model for t in self._MID_MODELS):
            score += 0.04

        # Scaffolding leak penalty — unstripped thinking tokens reduce quality
        if response and ("<think>" in response or "</think>" in response):
            score -= 0.10

        if not prompt or not response:
            score -= 0.20

        return round(max(0.05, min(score, 0.98)), 4)
