"""Data models for the distillation pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class CorpusTier(Enum):
    """Training corpus size tiers."""

    SEED = "seed"  # 500-2,000 examples (first training)
    GROWTH = "growth"  # 2,000-10,000 (improved training)
    FULL = "full"  # 10,000-50,000 (comprehensive)


@dataclass
class ThinkingTrace:
    """Preserved thinking tokens from model responses."""

    model: str
    raw_thinking: str  # Original <think>...</think> content
    stripped_output: str  # Clean response without thinking
    extraction_method: str = "regex"  # regex | structured | api_field


@dataclass
class ConversationRecord:
    """A single conversation that may become training data."""

    id: str  # uuid4
    source: str  # "claude_code" | "able" | "inbox" | "chatgpt" | "codex" | etc.
    messages: List[Dict]  # [{"role": "user/assistant/system", "content": "..."}]
    model: str  # teacher model that generated response
    tier: int  # which tier was used
    domain: str  # coding | security | creative | reasoning | etc.
    quality_score: float = 0.0  # 0.0-1.0 from evaluators
    thinking_trace: Optional[ThinkingTrace] = None
    tenant_id: str = "default"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict = field(default_factory=dict)
    content_hash: str = ""  # SHA256 of messages for dedup

    def __post_init__(self) -> None:
        if not self.content_hash:
            raw = str(self.messages)
            self.content_hash = hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class DistillationPair:
    """An actual input/output pair ready for fine-tuning."""

    id: str
    prompt: str  # User message (system prompt prepended during training)
    gold_response: str  # Teacher model response
    gold_model: str  # Which teacher model
    gold_thinking: Optional[str]  # Preserved reasoning trace
    domain: str
    quality_score: float
    tenant_id: str = "default"
    corpus_version: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    content_hash: str = ""  # SHA256 of prompt+response for dedup

    def __post_init__(self) -> None:
        if not self.content_hash:
            raw = f"{self.prompt}:{self.gold_response}"
            self.content_hash = hashlib.sha256(raw.encode()).hexdigest()

    def to_chatml(self, system_prompt: str = "") -> Dict:
        """Convert to ChatML training format."""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": self.prompt})
        response = self.gold_response
        if self.gold_thinking:
            response = f"<think>{self.gold_thinking}</think>\n{response}"
        messages.append({"role": "assistant", "content": response})
        return {
            "conversations": messages,
            "metadata": {
                "source": self.gold_model,
                "domain": self.domain,
                "quality_score": self.quality_score,
                "tenant_id": self.tenant_id,
            },
        }
