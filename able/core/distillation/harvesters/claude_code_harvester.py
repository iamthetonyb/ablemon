"""
Harvester for Claude Code session JSONL files.

Scans ``~/.claude/projects/`` for session JSONL files, extracts
user/assistant turns, preserves tool_use / tool_result blocks and
``<think>`` chain-of-thought content.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)

logger = logging.getLogger(__name__)

_DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# Entry types in Claude Code JSONL that are NOT conversation data.
# These are session metadata, UI state, and internal bookkeeping.
# Training on them teaches the model to hallucinate transcript noise.
_SKIP_ENTRY_TYPES: frozenset[str] = frozenset({
    "file-history-snapshot",    # File state tracking
    "queue-operation",          # Message queue state
    "permission-mode",          # Permission mode changes
    "attachment",               # File/image attachments (binary refs, not text)
    "last-prompt",              # Last prompt bookmark
    "summary",                  # Session summary metadata
    "custom-title",             # User-set title
    "ai-title",                 # AI-generated title
    "tag",                      # Session tags
    "agent-name",               # Agent name metadata
    "agent-color",              # Agent color metadata
    "agent-setting",            # Agent config metadata
    "pr-link",                  # GitHub PR link metadata
    "mode",                     # coordinator/normal mode flag
    "worktree-state",           # Worktree session state
    "content-replacement",      # Content stub replacements
    "marble-origami-commit",    # Context collapse commit (obfuscated type)
    "marble-origami-snapshot",  # Context collapse snapshot
    "speculation-accept",       # Speculative execution accept
    "attribution-snapshot",     # File attribution tracking
    "task-summary",             # Agent task summary
    "stream_event",             # Raw API stream events
    "tombstone",                # Removal signals
    "progress",                 # Tool execution progress
    "comment-label",            # Bash comment label metadata
    "bash-progress",            # Bash tool progress updates (intermediate output)
    "code-indexing",            # Code indexing/search telemetry
    "plugin-hint",              # Plugin recommendation hint records
    "claude-code-hint",         # Zero-token side-channel hint protocol
})

# System message subtypes that are scaffolding, not reasoning
_SKIP_SYSTEM_SUBTYPES: frozenset[str] = frozenset({
    "stop_hook_summary",    # Hook execution summary
    "turn_duration",        # Timing metadata
    "compact_boundary",     # Context compaction marker
    "api_error",            # API error passthrough
})

# File-extension hints for domain tagging
_EXT_DOMAIN_MAP: dict[str, str] = {
    ".py": "coding",
    ".js": "coding",
    ".ts": "coding",
    ".tsx": "coding",
    ".rs": "coding",
    ".go": "coding",
    ".java": "coding",
    ".yaml": "devops",
    ".yml": "devops",
    ".tf": "devops",
    ".sql": "data",
    ".md": "copywriting",
}


class ClaudeCodeHarvester(BaseHarvester):
    """Extract conversations from Claude Code JSONL session files."""

    source_name = "claude_code"

    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        projects_dir = Path(source_path) if source_path else _DEFAULT_PROJECTS_DIR
        if not projects_dir.exists():
            logger.warning("Claude Code projects dir not found: %s", projects_dir)
            return []

        results: list[HarvestedConversation] = []
        for jsonl_file in sorted(projects_dir.rglob("*.jsonl")):
            try:
                convos = self._parse_session_file(jsonl_file, since)
                results.extend(convos)
            except Exception:
                logger.warning("Failed to parse %s", jsonl_file, exc_info=True)

        return results

    # ── Internal parsing ───────────────────────────────────────────

    def _parse_session_file(
        self, path: Path, since: datetime | None
    ) -> list[HarvestedConversation]:
        """Parse a single JSONL session file into conversations."""
        messages: list[dict] = []
        thinking_blocks: list[str] = []
        tool_uses: list[dict] = []
        file_extensions_seen: set[str] = set()
        model_name = "claude"
        timestamp = datetime.fromtimestamp(path.stat().st_mtime)

        # Normalize timezone awareness for comparison
        if since and since.tzinfo is not None and timestamp.tzinfo is None:
            from datetime import timezone
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if since and timestamp < since:
            return []

        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                self._process_record(
                    record, messages, thinking_blocks, tool_uses,
                    file_extensions_seen,
                )

                # Extract model name from assistant message
                if record.get("type") == "assistant" and "message" in record:
                    _msg = record["message"]
                    if isinstance(_msg, dict) and "model" in _msg:
                        model_name = _msg["model"]

        if not messages:
            return []

        # Final scaffolding pass on all message content
        messages = self._clean_messages(messages)
        if not messages:
            return []

        if self._is_meta_conversation(messages):
            return []

        # Domain detection: file extensions first, then content keywords
        domain = self._domain_from_extensions(file_extensions_seen)
        if not domain:
            domain = self._detect_domain(messages)

        compression_mode = self._detect_compression_mode(messages)

        convo = HarvestedConversation(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, str(path))),
            source=self.source_name,
            messages=messages,
            model=model_name,
            timestamp=timestamp,
            domain=domain,
            thinking_blocks=thinking_blocks,
            tool_uses=tool_uses,
            metadata={
                "file": str(path),
                "compression_mode": compression_mode,
            },
        )
        return [convo]

    def _process_record(
        self,
        record: dict,
        messages: list[dict],
        thinking_blocks: list[str],
        tool_uses: list[dict],
        file_extensions_seen: set[str],
    ) -> None:
        """Process a single JSONL record into messages / metadata.

        Claude Code JSONL format has top-level ``type`` field
        (``user`` / ``assistant`` / ``system``), with assistant records
        containing a nested ``message`` dict that holds the API-style
        ``content`` list (text / thinking / tool_use blocks).

        Filters out:
        - 20+ metadata entry types (file-history-snapshot, queue-operation, etc.)
        - System messages with scaffolding subtypes (stop_hook_summary, etc.)
        - isMeta-flagged session bookkeeping records
        - Agent sidechain records (isSidechain=True)
        - Scaffolding XML tags (<system-reminder>, <command-name>, etc.)
        - Bloated tool_result content (file dumps >2000 chars)
        """
        record_type = record.get("type", "")

        # Skip metadata entry types (file-history-snapshot, queue-operation, etc.)
        if record_type in _SKIP_ENTRY_TYPES:
            return

        # Skip non-conversation records
        if record_type not in ("user", "assistant", "system"):
            return

        # Skip meta messages (session bookkeeping)
        if record.get("isMeta"):
            return

        # Skip sidechain records (agent sub-conversations)
        if record.get("isSidechain"):
            return

        # Skip system messages with scaffolding subtypes
        if record_type == "system":
            subtype = record.get("subtype", "")
            if subtype in _SKIP_SYSTEM_SUBTYPES:
                return

        role = record_type  # user/assistant/system map directly

        # For assistant records, content lives inside record["message"]["content"]
        if role == "assistant" and "message" in record:
            msg = record["message"]
            content = msg.get("content", "")
        else:
            # User/system messages: content is in record["message"]["content"]
            # or sometimes directly in record["content"]
            msg = record.get("message", {})
            if isinstance(msg, dict):
                content = msg.get("content", record.get("content", ""))
            else:
                content = record.get("content", "")

        # Content can be a list of content blocks (Anthropic format)
        if isinstance(content, list):
            text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    text = self._strip_scaffolding(block.get("text", ""))
                    if text:
                        text_parts.append(text)
                elif block_type == "thinking":
                    thinking_blocks.append(block.get("thinking", ""))
                elif block_type == "tool_use":
                    tool_uses.append(block)
                    self._collect_extensions(block, file_extensions_seen)
                elif block_type == "tool_result":
                    # Keep tool results but truncate bloated ones
                    result_content = block.get("content", "")
                    if isinstance(result_content, str):
                        result_content = self._strip_scaffolding(result_content)
                        result_content = self._truncate_tool_result(result_content)
                    elif isinstance(result_content, list):
                        # Content blocks inside tool_result
                        for sub in result_content:
                            if isinstance(sub, dict) and sub.get("type") == "text":
                                text = self._strip_scaffolding(sub.get("text", ""))
                                text = self._truncate_tool_result(text)
                                if text:
                                    text_parts.append(f"[tool output] {text}")
                        continue
                    # Skip empty tool results
                    if not result_content:
                        continue
                elif block_type == "tool_use_summary":
                    # Skip — runtime bookkeeping, not reasoning
                    continue
            content = "\n".join(text_parts)

        if isinstance(content, str):
            # Strip scaffolding tags from string content
            content = self._strip_scaffolding(content)
            # Extract inline <think> blocks
            for match in _THINK_RE.finditer(content):
                thinking_blocks.append(match.group(1).strip())
            cleaned = _THINK_RE.sub("", content).strip()
            if cleaned:
                messages.append({"role": role, "content": cleaned})

    @staticmethod
    def _collect_extensions(record: dict, exts: set[str]) -> None:
        """Collect file extensions mentioned in tool_use inputs."""
        inp = record.get("input", {})
        if not isinstance(inp, dict):
            return
        for val in inp.values():
            if isinstance(val, str) and "." in val:
                suffix = Path(val).suffix.lower()
                if suffix:
                    exts.add(suffix)

    # ── Compression mode detection ──────────────────────────────

    # Wenyan indicators: CJK unified ideographs + common classical particles
    _WENYAN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
    # Caveman-ultra indicators: hyper-shorthand patterns
    _CAVEMAN_PATTERNS = re.compile(
        r"\bu\b(?=/| )|→|←|\bw/o?\b|\b#s\b|\bb4\b|\bbc\b|\bbtwn\b|\bthru\b|\bur\b"
    )
    # Tech abbreviations common in ultramode
    _TECH_ABBREVS = re.compile(
        r"\b(?:DB|auth|mw|EP|param|comp|tmpl|conn|txn|sched|ctr|infra|k8s|i18n"
        r"|impl|fn|srv|dep|pkg|msg|err|req|res)\b"
    )

    @classmethod
    def _detect_compression_mode(cls, messages: list[dict]) -> str:
        """Heuristic detection of compression mode from message content.

        Scans assistant messages for ultramode patterns:
        - wenyan chars + tech abbrevs → "wenyan-ultra"
        - caveman shorthand (u/ur, →, w/, b4, bc, #s) → "caveman-ultra"
        - Both layers present → "ultramode"
        - None detected → ""
        """
        assistant_text = " ".join(
            m.get("content", "") for m in messages
            if m.get("role") == "assistant" and isinstance(m.get("content"), str)
        )
        if not assistant_text:
            return ""

        # Sample first 3000 chars for efficiency
        sample = assistant_text[:3000]

        has_wenyan = bool(cls._WENYAN_RE.search(sample))
        has_caveman = len(cls._CAVEMAN_PATTERNS.findall(sample)) >= 3
        has_tech = len(cls._TECH_ABBREVS.findall(sample)) >= 3

        if has_wenyan and (has_caveman or has_tech):
            return "ultramode"  # Both layers detected (dual-mode)
        if has_wenyan:
            return "wenyan-ultra"
        if has_caveman or has_tech:
            return "caveman-ultra"
        return ""

    @staticmethod
    def _domain_from_extensions(exts: set[str]) -> str:
        """Pick a domain from observed file extensions."""
        counts: dict[str, int] = {}
        for ext in exts:
            domain = _EXT_DOMAIN_MAP.get(ext, "")
            if domain:
                counts[domain] = counts.get(domain, 0) + 1
        if not counts:
            return ""
        return max(counts, key=counts.get)  # type: ignore[arg-type]
