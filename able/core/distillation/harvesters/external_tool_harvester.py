"""
Harvester for third-party AI tool sessions.

Reads ``~/.able/external_sessions/*.jsonl`` — a generic drop directory
where users (or adapters) place JSONL transcripts from any AI tool:
Cursor, Windsurf, Copilot, Grok, custom agents, etc.

Each ``.jsonl`` file represents one session.  Records follow the same
schema as CLISessionHarvester::

    {"role": "user"|"assistant"|"system", "content": "...", ...}

Optional fields:
    "model"   — teacher model name (e.g. "gpt-4.1", "gemini-2.5-pro")
    "source"  — originating tool (e.g. "cursor", "windsurf")
    "ts"      — ISO timestamp

If the user wants to tag a specific tool, they can include a
``_source.txt`` file in the session directory with a single line
containing the tool name, or set the "source" field on each record.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path.home() / ".able" / "external_sessions"
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_MIN_TURNS = 2
_MIN_TOTAL_CONTENT_LENGTH = 100


class ExternalToolHarvester(BaseHarvester):
    """Harvest training conversations from third-party AI tool JSONL files.

    Any user can drop session files into ``~/.able/external_sessions/`` and
    they will be picked up during the nightly distillation harvest.  This
    makes ABLE's learning pipeline tool-agnostic — it learns from whatever
    AI tools the operator uses.
    """

    source_name = "external_tool"

    def __init__(self, sessions_dir: str | Path | None = None) -> None:
        self._sessions_dir = Path(sessions_dir) if sessions_dir else _DEFAULT_DIR

    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        sessions_dir = Path(source_path) if source_path else self._sessions_dir
        if not sessions_dir.exists():
            # Create the drop dir so users know where to put files
            sessions_dir.mkdir(parents=True, exist_ok=True)
            return []

        # Check for a _source.txt tag
        default_source = self.source_name
        source_tag = sessions_dir / "_source.txt"
        if source_tag.exists():
            tag = source_tag.read_text().strip()
            if tag:
                default_source = tag

        results: list[HarvestedConversation] = []
        for jsonl_file in sorted(sessions_dir.glob("*.jsonl")):
            try:
                convo = self._parse_session(jsonl_file, since, default_source)
                if convo is not None:
                    results.append(convo)
            except Exception:
                logger.warning(
                    "Failed to parse external session %s",
                    jsonl_file,
                    exc_info=True,
                )

        logger.info(
            "[external_harvester] Harvested %d sessions from %s",
            len(results),
            sessions_dir,
        )
        return results

    def _parse_session(
        self,
        path: Path,
        since: datetime | None,
        default_source: str,
    ) -> HarvestedConversation | None:
        timestamp = datetime.fromtimestamp(path.stat().st_mtime)
        if since and since.tzinfo is not None and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if since and timestamp < since:
            return None

        messages: list[dict] = []
        thinking_blocks: list[str] = []
        tool_uses: list[dict] = []
        model_name = "unknown"
        record_source = default_source

        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                role = record.get("role", "")
                if role not in ("user", "assistant", "system"):
                    continue

                content = record.get("content", "")

                # Handle structured content blocks
                if isinstance(content, list):
                    text_parts: list[str] = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type", "")
                        if bt == "text":
                            text_parts.append(block.get("text", ""))
                        elif bt == "thinking":
                            thinking_blocks.append(block.get("thinking", ""))
                        elif bt in ("tool_use", "tool_call", "function_call", "tool_result"):
                            tool_uses.append(block)
                    content = "\n".join(text_parts)

                if isinstance(content, str):
                    for match in _THINK_RE.finditer(content):
                        thinking_blocks.append(match.group(1).strip())
                    cleaned = _THINK_RE.sub("", content).strip()
                    if cleaned:
                        messages.append({"role": role, "content": cleaned})

                if record.get("model"):
                    model_name = record["model"]
                if record.get("source"):
                    record_source = record["source"]

        # Strip scaffolding from all messages (handles tags from any AI tool)
        messages = self._clean_messages(messages)

        if not messages or len(messages) < _MIN_TURNS:
            return None

        total_len = sum(len(m.get("content", "")) for m in messages)
        if total_len < _MIN_TOTAL_CONTENT_LENGTH:
            return None

        if self._is_meta_conversation(messages):
            return None

        domain = self._detect_domain(messages)

        return HarvestedConversation(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, str(path))),
            source=record_source,
            messages=messages,
            model=model_name,
            timestamp=timestamp,
            domain=domain,
            thinking_blocks=thinking_blocks,
            tool_uses=tool_uses,
            metadata={
                "file": str(path),
                "teacher_model": model_name,
                "original_tool": record_source,
                "has_tool_use": len(tool_uses) > 0,
                "turn_count": len(messages),
            },
        )
