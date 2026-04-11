"""
Base classes for conversation harvesters.

Every harvester converts platform-specific conversation data into
HarvestedConversation objects that the TrainingFormatter can turn
into ChatML training pairs.

Scaffolding stripping: Claude Code, Codex, and other AI tools inject
metadata tags into message content that must be removed before training.
Without this, fine-tuned models learn to hallucinate system-reminder
blocks, fake tool calls, command XML tags, and other artifacts.
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

# ── Scaffolding tag patterns (stripped from training data) ────────

# Claude Code / Codex inject these into message content. If a model
# trains on them it will hallucinate system-reminder blocks, fake
# tool schemas, memory index dumps, and command XML in production.
_SCAFFOLDING_TAG_RE = re.compile(
    r"<(?:"
    r"system-reminder|"            # Claude Code task/memory/git reminders
    r"command-name|"               # Slash command echoes
    r"command-message|"            # Slash command payload
    r"local-command-stdout|"       # Local command output wrappers
    r"local-command-caveat|"       # Caveat about local commands
    r"local-command-stderr|"       # Stderr wrappers
    r"user-prompt-submit-hook|"    # Pre-submit hook output
    r"functions|function|"         # Deferred tool schema dumps
    r"antml_function_calls|"       # Anthropic function call XML
    r"antml_invoke|"               # Anthropic invoke XML
    r"antml_parameter|"            # Anthropic parameter XML
    r"task-notification|"          # Background task notifications
    r"claude-code-hint|"           # Zero-token side-channel hint protocol
    r"example_agent_descriptions|" # Agent description examples in prompts
    r"example"                     # Inline prompt examples
    r")(?:\s[^>]*)?>.*?</(?:"
    r"system-reminder|command-name|command-message|"
    r"local-command-stdout|local-command-caveat|local-command-stderr|"
    r"user-prompt-submit-hook|functions|function|"
    r"antml_function_calls|antml_invoke|antml_parameter|task-notification|"
    r"claude-code-hint|example_agent_descriptions|example"
    r")>",
    re.DOTALL,
)

# Self-closing or orphaned opening tags (sometimes the closing tag is missing)
_SCAFFOLDING_OPEN_RE = re.compile(
    r"<(?:system-reminder|local-command-caveat|local-command-stdout|"
    r"local-command-stderr|user-prompt-submit-hook|task-notification|"
    r"claude-code-hint)"
    r"(?:\s[^>]*)?>",
)

# Base64 data URIs (image dumps from tool results — huge, no training value)
_DATA_URI_RE = re.compile(r"data:image/[a-z0-9.+_-]+;base64,[A-Za-z0-9+/=]{100,}")

# Analytics event names injected by Claude Code internals
_ANALYTICS_EVENT_RE = re.compile(r"\btengu_[a-z_]+\b")

# Tool result content over this size is truncated — raw file dumps and
# git diffs are not useful for teaching reasoning.
_MAX_TOOL_RESULT_CHARS = 2000

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

    @staticmethod
    def _strip_scaffolding(text: str) -> str:
        """Remove AI-tool scaffolding tags from message content.

        Claude Code, Codex, and similar tools inject XML tags like
        ``<system-reminder>``, ``<command-name>``, ``<functions>``,
        ``<antml_function_calls>``, and ``<claude-code-hint>`` into
        conversation transcripts.  These are runtime artifacts — NOT
        part of the reasoning.  If they leak into training data,
        fine-tuned models learn to hallucinate them.

        Also strips base64 image data URIs (tool result dumps of
        screenshots/charts — huge payload, zero reasoning value) and
        internal analytics event names (tengu_* identifiers).
        """
        # Strip matched pairs first
        text = _SCAFFOLDING_TAG_RE.sub("", text)
        # Then orphaned opening tags
        text = _SCAFFOLDING_OPEN_RE.sub("", text)
        # Strip base64 image data URIs (can be 100KB+ of noise)
        text = _DATA_URI_RE.sub("[image]", text)
        # Strip internal analytics event names
        text = _ANALYTICS_EVENT_RE.sub("", text)
        # Collapse leftover whitespace
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text

    # Edit/Write success patterns — extract path, discard verbose confirmation
    _EDIT_RESULT_RE = re.compile(
        r"(?:The file (\S+) has been (?:updated|created|written) successfully)"
        r"|(?:File (?:updated|written|created):? (\S+))",
        re.IGNORECASE,
    )

    @classmethod
    def _truncate_tool_result(cls, content: str) -> str:
        """Truncate bloated tool results (file dumps, diffs).

        Tool results over 2000 chars are almost always raw file content
        or git diffs — not useful for teaching reasoning.  Keep the first
        and last lines so the model sees what tool was called and a hint
        of the output shape, but discard the bulk.

        Special handling:
        - Edit/Write results: extract path + confirmation only
        - Persisted pointers: keep as-is (already compact)
        """
        if len(content) <= _MAX_TOOL_RESULT_CHARS:
            return content

        # Already a persistence pointer — compact, keep it
        if content.startswith("[Full output saved to"):
            return content[:500]

        # Edit/Write confirmations buried in verbose output — extract
        m = cls._EDIT_RESULT_RE.search(content)
        if m:
            path = m.group(1) or m.group(2) or "file"
            return f"[Edit: {path} updated successfully]"

        head = content[:800]
        tail = content[-200:]
        return f"{head}\n[... {len(content) - 1000} chars truncated for training ...]\n{tail}"

    def _clean_messages(self, messages: list[dict]) -> list[dict]:
        """Strip scaffolding and truncate tool dumps from all messages."""
        cleaned: list[dict] = []
        for msg in messages:
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            content = self._strip_scaffolding(content)
            if not content:
                continue
            cleaned.append({**msg, "content": content})
        return cleaned

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
