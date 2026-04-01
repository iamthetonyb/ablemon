"""
SessionHistory — Manage JSONL session transcripts for resume and distillation.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


@dataclass
class SessionEntry:
    """A single turn from a session transcript."""

    timestamp: str
    session_id: str
    tenant_id: str
    user_input: str
    response: str
    tier: int
    complexity_score: float
    message_count: int


class SessionHistory:
    """Read and manage JSONL session files."""

    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def list_sessions(self, limit: int = 20) -> List[str]:
        """Return the most recent session IDs (by mtime)."""
        files = sorted(self.session_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [f.stem for f in files[:limit]]

    def load_session(self, session_id: str) -> List[SessionEntry]:
        """Load all turns from a session JSONL file."""
        path = self.session_dir / f"{session_id}.jsonl"
        if not path.exists():
            logger.warning(f"Session {session_id} not found at {path}")
            return []

        entries: List[SessionEntry] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entries.append(
                    SessionEntry(
                        timestamp=data.get("timestamp", ""),
                        session_id=data.get("session_id", session_id),
                        tenant_id=data.get("tenant_id", ""),
                        user_input=data.get("user_input", ""),
                        response=data.get("response", ""),
                        tier=data.get("tier", 0),
                        complexity_score=data.get("complexity_score", 0.0),
                        message_count=data.get("message_count", 0),
                    )
                )
            except (json.JSONDecodeError, KeyError) as exc:
                logger.debug(f"Skipping malformed line in {session_id}: {exc}")
        return entries

    def rebuild_messages(self, session_id: str) -> List[Dict]:
        """Rebuild a conversation message list from a session transcript."""
        entries = self.load_session(session_id)
        messages: List[Dict] = []
        for entry in entries:
            messages.append({"role": "user", "content": entry.user_input})
            messages.append({"role": "assistant", "content": entry.response})
        return messages

    def session_exists(self, session_id: str) -> bool:
        return (self.session_dir / f"{session_id}.jsonl").exists()

    def delete_session(self, session_id: str) -> bool:
        path = self.session_dir / f"{session_id}.jsonl"
        if path.exists():
            path.unlink()
            return True
        return False
