"""Data models for the distillation pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


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
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
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


@dataclass
class TrainingPair:
    """Canonical typed training example used inside the distillation pipeline."""

    id: str
    prompt: str
    response: str
    domain: str
    quality_score: float
    source: str
    teacher_model: str
    thinking: Optional[str] = None
    tenant_id: str = "default"
    messages: List[Dict[str, Any]] = field(default_factory=list)
    response_accepted: bool = True
    escalated: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            raw = f"{self.prompt}:{self.response}:{self.teacher_model}"
            self.content_hash = hashlib.sha256(raw.encode()).hexdigest()

    def to_distillation_pair(self) -> DistillationPair:
        """Convert to the persisted pair schema."""
        return DistillationPair(
            id=self.id,
            prompt=self.prompt,
            gold_response=self.response,
            gold_model=self.teacher_model,
            gold_thinking=self.thinking,
            domain=self.domain,
            quality_score=self.quality_score,
            tenant_id=self.tenant_id,
            tags=[self.source],
            created_at=self.created_at,
            content_hash=self.content_hash,
        )

    def to_chatml(self, system_prompt: str = "") -> Dict[str, Any]:
        """Convert the canonical pair into ChatML export format."""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": self.prompt})
        assistant_content = self.response
        if self.thinking:
            assistant_content = f"<think>{self.thinking}</think>\n{self.response}"
        messages.append({"role": "assistant", "content": assistant_content})
        return {
            "conversations": messages,
            "metadata": {
                "source": self.source,
                "teacher_model": self.teacher_model,
                "domain": self.domain,
                "quality_score": self.quality_score,
                "content_hash": self.content_hash,
                "tenant_id": self.tenant_id,
            },
        }

    def to_corpus_record(self) -> Dict[str, Any]:
        """Convert to the dict shape consumed by CorpusBuilder."""
        record: Dict[str, Any] = {
            "id": self.id,
            "prompt": self.prompt,
            "response": self.response,
            "domain": self.domain,
            "quality_score": self.quality_score,
            "source": self.source,
            "teacher_model": self.teacher_model,
            "tenant_id": self.tenant_id,
            "content_hash": self.content_hash,
            "response_accepted": self.response_accepted,
            "escalated": self.escalated,
            "created_at": self.created_at.isoformat(),
        }
        if self.thinking:
            record["thinking"] = self.thinking
        if self.messages:
            record["messages"] = self.messages
        if self.metadata:
            record["metadata"] = self.metadata
        return record

    @classmethod
    def from_corpus_record(cls, record: Dict[str, Any]) -> "TrainingPair":
        """Create a canonical pair from a builder/store record."""
        response = record.get("clean_answer") or record.get("response") or record.get(
            "gold_response", ""
        )
        return cls(
            id=record.get("id", ""),
            prompt=record.get("prompt", ""),
            response=response,
            domain=record.get("domain", "general"),
            quality_score=float(record.get("quality_score", 0.0)),
            source=record.get("source", record.get("teacher_model", "unknown")),
            teacher_model=record.get("teacher_model", record.get("model", "unknown")),
            thinking=record.get("thinking") or record.get("gold_thinking"),
            tenant_id=record.get("tenant_id", "default"),
            messages=list(record.get("messages", [])),
            response_accepted=record.get("response_accepted", True),
            escalated=record.get("escalated", False),
            metadata=dict(record.get("metadata", {})),
            content_hash=record.get("content_hash", ""),
        )

    @classmethod
    def from_chatml(cls, record: Dict[str, Any]) -> "TrainingPair":
        """Convert a ChatML export record back into the canonical shape."""
        metadata = dict(record.get("metadata", {}))
        conversations = list(record.get("conversations", []))
        prompt = ""
        response = ""
        thinking = None

        for idx in range(len(conversations) - 1, -1, -1):
            message = conversations[idx]
            role = message.get("role")
            content = message.get("content", "")
            if role == "assistant" and not response:
                extraction = ThinkingTraceExtractor.extract(content)
                response = extraction["response"]
                thinking = extraction["thinking"]
            elif role == "user" and not prompt:
                prompt = content
            if prompt and response:
                break

        return cls(
            id=metadata.get("content_hash", ""),
            prompt=prompt,
            response=response,
            domain=metadata.get("domain", "general"),
            quality_score=float(metadata.get("quality_score", 0.0)),
            source=metadata.get("source", "unknown"),
            teacher_model=metadata.get("teacher_model", "unknown"),
            thinking=thinking,
            tenant_id=metadata.get("tenant_id", "default"),
            messages=conversations,
            metadata=metadata,
            content_hash=metadata.get("content_hash", ""),
        )


class ThinkingTraceExtractor:
    """Minimal helper to avoid importing the full extractor from model helpers."""

    @staticmethod
    def extract(text: str) -> Dict[str, Optional[str]]:
        start = "<think>"
        end = "</think>"
        if start in text and end in text:
            thinking = text.split(start, 1)[1].split(end, 1)[0].strip()
            response = text.split(end, 1)[1].strip()
            return {"thinking": thinking or None, "response": response}
        return {"thinking": None, "response": text.strip()}
