"""
Training data formatter — converts HarvestedConversation objects into
ChatML training format suitable for fine-tuning.

Produces records compatible with Unsloth / HuggingFace SFT trainers with
``train_on_responses_only`` masking support.
"""

from __future__ import annotations

import logging
from typing import Sequence

from atlas.core.distillation.harvesters.base import HarvestedConversation

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are ATLAS, an autonomous AI agent. Be direct, accurate, and helpful."
)


class TrainingFormatter:
    """Standardises harvested data to ChatML training format."""

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
        sys_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        conversations: list[dict] = [{"role": "system", "content": sys_prompt}]

        for msg in conversation.messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role not in ("user", "assistant", "system"):
                continue
            if not content:
                continue
            # Skip duplicate system messages
            if role == "system" and content == sys_prompt:
                continue
            conversations.append({"role": role, "content": content})

        return {
            "conversations": conversations,
            "metadata": {
                "source": conversation.source,
                "teacher_model": conversation.model,
                "domain": conversation.domain,
                "quality_score": 0.0,  # placeholder for future scoring
                "content_hash": conversation.content_hash,
                "tenant_id": "default",
            },
        }

    def format_batch(
        self,
        conversations: Sequence[HarvestedConversation],
        system_prompt: str = "",
    ) -> list[dict]:
        """Format a batch of conversations."""
        return [self.format(c, system_prompt) for c in conversations]

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
