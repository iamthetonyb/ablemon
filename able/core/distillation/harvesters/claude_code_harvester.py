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

        if self._is_meta_conversation(messages):
            return []

        # Domain detection: file extensions first, then content keywords
        domain = self._domain_from_extensions(file_extensions_seen)
        if not domain:
            domain = self._detect_domain(messages)

        convo = HarvestedConversation(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, str(path))),
            source=self.source_name,
            messages=messages,
            model=model_name,
            timestamp=timestamp,
            domain=domain,
            thinking_blocks=thinking_blocks,
            tool_uses=tool_uses,
            metadata={"file": str(path)},
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
        """
        record_type = record.get("type", "")

        # Skip non-conversation records
        if record_type not in ("user", "assistant", "system"):
            return

        # Skip meta messages (session bookkeeping)
        if record.get("isMeta"):
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
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "thinking":
                    thinking_blocks.append(block.get("thinking", ""))
                elif block.get("type") == "tool_use":
                    tool_uses.append(block)
                    self._collect_extensions(block, file_extensions_seen)
            content = "\n".join(text_parts)

        if isinstance(content, str):
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
