"""
Harvester for Antigravity (Google Gemini IDE) sessions.

Antigravity stores conversation data in encrypted protobuf files, but
its "brain" directory contains readable markdown artifacts — task
plans, implementation plans, walkthroughs, and scratchpads — that
represent high-quality assistant-generated content.

Data locations:
  ~/.gemini/antigravity/brain/{session_id}/*.md       — plans + walkthroughs
  ~/.gemini/antigravity/brain/{session_id}/*.metadata.json — context metadata

Each session directory maps to one conversation.  We synthesize
user/assistant turn pairs from the task.md (user intent) and
implementation_plan.md / walkthrough.md (assistant response).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)

logger = logging.getLogger(__name__)

_DEFAULT_BRAIN_DIR = Path.home() / ".gemini" / "antigravity" / "brain"


class AntigravityHarvester(BaseHarvester):
    """Extract training data from Antigravity brain artifacts."""

    source_name = "antigravity"

    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        brain_dir = Path(source_path) if source_path else _DEFAULT_BRAIN_DIR
        if not brain_dir.exists():
            logger.info("Antigravity brain dir not found: %s", brain_dir)
            return []

        results: list[HarvestedConversation] = []
        for session_dir in sorted(brain_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            # Skip non-UUID directories (e.g. tempmediaStorage)
            if len(session_dir.name) < 30:
                continue

            try:
                convo = self._parse_session(session_dir, since)
                if convo:
                    results.append(convo)
            except Exception:
                logger.warning("Failed to parse %s", session_dir, exc_info=True)

        logger.info("Antigravity: harvested %d conversations from %s", len(results), brain_dir)
        return results

    def _parse_session(
        self, session_dir: Path, since: datetime | None
    ) -> HarvestedConversation | None:
        """Parse a single Antigravity session directory into a conversation."""
        # Check modification time
        mtime = datetime.fromtimestamp(session_dir.stat().st_mtime)
        if since:
            if since.tzinfo is not None and mtime.tzinfo is None:
                mtime = mtime.replace(tzinfo=timezone.utc)
            if mtime < since:
                return None

        # Read available markdown files
        task_md = self._read_file(session_dir / "task.md")
        plan_md = self._read_file(session_dir / "implementation_plan.md")
        walkthrough_md = self._read_file(session_dir / "walkthrough.md")

        # Also collect any scratchpad files from browser subdirectory
        scratchpads = []
        browser_dir = session_dir / "browser"
        if browser_dir.exists():
            for sp in sorted(browser_dir.glob("scratchpad_*.md")):
                content = self._read_file(sp)
                if content:
                    scratchpads.append(content)

        # We need at least a task AND a response (plan or walkthrough)
        if not task_md:
            return None
        response_parts = [p for p in [plan_md, walkthrough_md] if p]
        if not response_parts and not scratchpads:
            return None

        # Build conversation turns
        messages: list[dict] = []

        # The task.md represents what the user asked for
        messages.append({
            "role": "user",
            "content": task_md,
        })

        # The implementation plan is the primary response
        if plan_md:
            messages.append({
                "role": "assistant",
                "content": plan_md,
            })

        # Walkthrough as a follow-up response if different from plan
        if walkthrough_md and walkthrough_md != plan_md:
            messages.append({
                "role": "assistant",
                "content": walkthrough_md,
            })

        # Scratchpads as additional context
        for sp_content in scratchpads[:3]:  # Limit to 3 scratchpads
            messages.append({
                "role": "assistant",
                "content": sp_content,
            })

        if self._is_meta_conversation(messages):
            return None

        # Read metadata for additional context
        metadata = {"session_dir": str(session_dir)}
        for meta_file in session_dir.glob("*.metadata.json"):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    metadata.update(meta)
            except Exception:
                pass

        return HarvestedConversation(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"antigravity:{session_dir.name}")),
            source=self.source_name,
            messages=messages,
            model="gemini-2.5-pro",
            timestamp=mtime,
            domain=self._detect_domain(messages),
            thinking_blocks=[],
            metadata=metadata,
        )

    @staticmethod
    def _read_file(path: Path) -> str:
        """Read a text file, return empty string if missing or unreadable."""
        if not path.exists():
            return ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            # Skip very short files (just headers or empty templates)
            if len(text) < 50:
                return ""
            return text
        except Exception:
            return ""
