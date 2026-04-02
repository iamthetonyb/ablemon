"""
Harvester for ABLE CLI session JSONL transcripts.

Reads ``~/.able/sessions/*.jsonl``, filters for quality (tool-use chains,
multi-turn reasoning, accepted responses), extracts thinking tokens with
dual-path handling (display stripped, log full), and tags metadata for the
distillation pipeline.
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

_DEFAULT_SESSIONS_DIR = Path.home() / ".able" / "sessions"
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# Minimum turn count for a session to be worth harvesting
_MIN_TURNS = 2

# Minimum total content length across all messages
_MIN_TOTAL_CONTENT_LENGTH = 100


class CLISessionHarvester(BaseHarvester):
    """Extract training conversations from ABLE CLI session JSONL files.

    Each ``.jsonl`` file in ``~/.able/sessions/`` represents one CLI session.
    Records follow a simple schema::

        {"role": "user"|"assistant"|"system", "content": "...", ...}

    The harvester filters for quality, extracts thinking tokens (keeping both
    the raw trace for logging and the stripped version for display), and tags
    each conversation with source, teacher model, and auto-detected domain.
    """

    source_name = "able_cli"

    def __init__(self, sessions_dir: str | Path | None = None) -> None:
        self._sessions_dir = Path(sessions_dir) if sessions_dir else _DEFAULT_SESSIONS_DIR

    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        sessions_dir = Path(source_path) if source_path else self._sessions_dir
        if not sessions_dir.exists():
            logger.warning("CLI sessions dir not found: %s", sessions_dir)
            return []

        results: list[HarvestedConversation] = []
        for jsonl_file in sorted(sessions_dir.glob("*.jsonl")):
            try:
                convo = self._parse_session(jsonl_file, since)
                if convo is not None:
                    results.append(convo)
            except Exception:
                logger.warning("Failed to parse CLI session %s", jsonl_file, exc_info=True)

        logger.info("[cli_harvester] Harvested %d sessions from %s", len(results), sessions_dir)
        return results

    # ── Internal parsing ───────────────────────────────────────────

    def _parse_session(
        self, path: Path, since: datetime | None
    ) -> HarvestedConversation | None:
        """Parse a single JSONL session file into a HarvestedConversation."""
        timestamp = datetime.fromtimestamp(path.stat().st_mtime)

        # Normalize timezone for comparison
        if since and since.tzinfo is not None and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if since and timestamp < since:
            return None

        messages: list[dict] = []
        thinking_blocks: list[str] = []
        tool_uses: list[dict] = []
        model_name = "unknown"
        has_accepted_response = False

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
                )

                # Extract model name
                if record.get("model"):
                    model_name = record["model"]
                elif record.get("role") == "assistant" and record.get("model"):
                    model_name = record["model"]

                # Track accepted responses (user didn't reject/retry)
                if record.get("accepted") or record.get("status") == "accepted":
                    has_accepted_response = True

        if not messages:
            return None

        # Quality filters
        if not self._is_quality_session(messages, tool_uses, has_accepted_response):
            return None

        if self._is_meta_conversation(messages):
            return None

        domain = self._detect_domain(messages)

        return HarvestedConversation(
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
                "teacher_model": model_name,
                "has_tool_use": len(tool_uses) > 0,
                "turn_count": len(messages),
            },
        )

    def _process_record(
        self,
        record: dict,
        messages: list[dict],
        thinking_blocks: list[str],
        tool_uses: list[dict],
    ) -> None:
        """Process a single JSONL record into messages / metadata."""
        role = record.get("role", "")
        if role not in ("user", "assistant", "system"):
            return

        content = record.get("content", "")

        # Content can be a list of content blocks (Anthropic format)
        if isinstance(content, list):
            text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "thinking":
                    thinking_blocks.append(block.get("thinking", ""))
                elif block_type in ("tool_use", "tool_call", "function_call"):
                    tool_uses.append(block)
                elif block_type == "tool_result":
                    tool_uses.append(block)
            content = "\n".join(text_parts)

        if isinstance(content, str):
            # Dual-path: extract thinking for the log, strip for display content
            for match in _THINK_RE.finditer(content):
                thinking_blocks.append(match.group(1).strip())
            cleaned = _THINK_RE.sub("", content).strip()
            if cleaned:
                messages.append({"role": role, "content": cleaned})

    def _is_quality_session(
        self,
        messages: list[dict],
        tool_uses: list[dict],
        has_accepted: bool,
    ) -> bool:
        """Determine if a session is high enough quality for training data.

        Quality signals:
        - Multi-turn exchanges (>= _MIN_TURNS messages)
        - Sufficient total content length
        - Tool-use chains (indicates real work, not just chat)
        - Accepted (non-rejected) responses
        """
        if len(messages) < _MIN_TURNS:
            return False

        total_length = sum(
            len(m.get("content", ""))
            for m in messages
            if isinstance(m.get("content"), str)
        )
        if total_length < _MIN_TOTAL_CONTENT_LENGTH:
            return False

        # Sessions with tool use are always high quality
        if tool_uses:
            return True

        # Multi-turn reasoning (4+ messages) is high quality
        if len(messages) >= 4:
            return True

        # Accepted responses are good
        if has_accepted:
            return True

        # Default: keep if it passed the basic length/turn checks
        return True

    def dedup_against_corpus(
        self,
        conversations: list[HarvestedConversation],
        existing_hashes: set[str],
    ) -> list[HarvestedConversation]:
        """Filter out conversations whose instruction hash already exists."""
        unique: list[HarvestedConversation] = []
        for convo in conversations:
            # Hash based on the first user message (the instruction)
            instruction = ""
            for msg in convo.messages:
                if msg.get("role") == "user":
                    instruction = msg.get("content", "")
                    break
            if not instruction:
                continue
            h = hashlib.sha256(instruction.encode()).hexdigest()
            if h not in existing_hashes:
                existing_hashes.add(h)
                unique.append(convo)
        return unique
