"""
Base classes for conversation harvesters.

Every harvester converts platform-specific conversation data into
HarvestedConversation objects that the TrainingFormatter can turn
into ChatML training pairs.
"""

from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Domain detection keyword sets ──────────────────────────────────

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "coding": [
        "function", "class", "def ", "import ", "debug", "refactor",
        "compile", "runtime", "traceback", "exception", "variable",
        "async", "await", "return", "TypeError", "ValueError",
        "git ", "commit", "branch", "merge", "pull request",
    ],
    "security": [
        "vulnerability", "CVE", "exploit", "injection", "XSS",
        "CSRF", "authentication", "authorization", "encryption",
        "malware", "firewall", "penetration", "threat", "audit",
    ],
    "devops": [
        "deploy", "docker", "kubernetes", "CI/CD", "pipeline",
        "terraform", "ansible", "nginx", "systemd", "container",
    ],
    "data": [
        "SQL", "database", "query", "schema", "migration",
        "pandas", "dataframe", "CSV", "ETL", "warehouse",
    ],
    "research": [
        "investigate", "analyze", "compare", "evaluate", "study",
        "literature", "methodology", "findings", "hypothesis",
    ],
    "copywriting": [
        "write", "draft", "email", "blog", "headline",
        "copy", "tone", "audience", "CTA", "subject line",
    ],
}

# Phrases that signal meta-conversation (not useful for training)
_META_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*(ok|okay|got it|thanks|thank you|yes|no|sure)\s*[.!]?\s*$", re.I),
    re.compile(r"^\s*(sounds good|perfect|great|cool|nice)\s*[.!]?\s*$", re.I),
    re.compile(r"^\s*can you (repeat|say that again|clarify)", re.I),
    re.compile(r"^\s*(status|progress|update)\s*\??\s*$", re.I),
]

# Minimum substantive message length (chars) to keep a conversation
_MIN_MESSAGE_LENGTH = 40


@dataclass
class HarvestedConversation:
    """A conversation extracted from any source, before formatting."""

    id: str
    source: str  # platform identifier
    messages: list[dict]  # [{"role": "user/assistant/system", "content": "..."}]
    model: str  # teacher model name
    timestamp: datetime
    domain: str = ""  # auto-detected or tagged
    thinking_blocks: list[str] = field(default_factory=list)
    tool_uses: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    content_hash: str = ""

    def __post_init__(self):
        if not self.content_hash:
            raw = str(self.messages)
            self.content_hash = hashlib.sha256(raw.encode()).hexdigest()


class BaseHarvester(ABC):
    """Base class for all conversation harvesters."""

    source_name: str = "unknown"

    @abstractmethod
    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        """Extract conversations from this source."""
        ...

    # ── Shared helpers ─────────────────────────────────────────────

    def _detect_domain(self, messages: list[dict]) -> str:
        """Auto-detect domain from message content using keyword matching.

        Scans all message content and returns the domain whose keyword set
        has the most hits.  Returns ``""`` when nothing matches clearly.
        """
        corpus = " ".join(
            m.get("content", "") for m in messages if isinstance(m.get("content"), str)
        ).lower()

        scores: dict[str, int] = {}
        for domain, keywords in _DOMAIN_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw.lower() in corpus)
            if score:
                scores[domain] = score

        if not scores:
            return ""

        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        return best

    def _is_meta_conversation(self, messages: list[dict]) -> bool:
        """Return True if the conversation is mostly meta-chatter.

        A conversation is considered meta if fewer than 2 messages contain
        substantive content (longer than ``_MIN_MESSAGE_LENGTH`` and not
        matching a meta pattern).
        """
        substantive = 0
        for msg in messages:
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            if len(content) < _MIN_MESSAGE_LENGTH:
                continue
            if any(p.match(content) for p in _META_PATTERNS):
                continue
            substantive += 1

        return substantive < 2
