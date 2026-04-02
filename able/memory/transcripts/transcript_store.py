"""
Transcript Store
Stores and retrieves conversation transcripts for clients and sessions.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field


@dataclass
class TranscriptMessage:
    """A single message in a transcript"""
    timestamp: datetime
    role: str  # "user", "assistant", "system"
    content: str
    user_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptSession:
    """A conversation session"""
    session_id: str
    client_id: str
    started_at: datetime
    messages: List[TranscriptMessage] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    ended_at: Optional[datetime] = None


class TranscriptStore:
    """
    Stores conversation transcripts organized by client and date.
    Syncs with v1 (~/.able/memory/daily/) for compatibility.
    """

    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = Path(base_path) if base_path else Path(__file__).parent
        self.base_path.mkdir(parents=True, exist_ok=True)

        # v1 bridge
        self._v1_daily_path = Path.home() / ".able" / "memory" / "daily"

    def _get_transcript_path(self, client_id: str, date: datetime = None) -> Path:
        """Get path to transcript file"""
        if date is None:
            date = datetime.utcnow()

        date_str = date.strftime("%Y-%m-%d")
        path = self.base_path / client_id / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def log_message(
        self,
        client_id: str,
        session_id: str,
        role: str,
        content: str,
        user_id: Optional[str] = None,
        metadata: Dict[str, Any] = None
    ):
        """Log a message to transcript"""
        message = TranscriptMessage(
            timestamp=datetime.utcnow(),
            role=role,
            content=content,
            user_id=user_id,
            metadata=metadata or {}
        )

        # Write to client transcript
        transcript_path = self._get_transcript_path(client_id)
        entry = {
            "timestamp": message.timestamp.isoformat(),
            "session_id": session_id,
            "role": role,
            "content": content,
            "user_id": user_id,
            "metadata": metadata or {}
        }

        with open(transcript_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Sync to v1 daily log if it's the master/owner
        if client_id == "master" or client_id == "owner":
            self._sync_to_v1_daily(message)

    def _sync_to_v1_daily(self, message: TranscriptMessage):
        """Sync message to v1 daily log format"""
        if not self._v1_daily_path.exists():
            return

        date_str = message.timestamp.strftime("%Y-%m-%d")
        daily_file = self._v1_daily_path / f"{date_str}.md"

        if daily_file.exists():
            time_str = message.timestamp.strftime("%H:%M")
            role_prefix = "User" if message.role == "user" else "ABLE"
            entry = f"\n**{time_str}** [{role_prefix}]: {message.content[:200]}{'...' if len(message.content) > 200 else ''}\n"

            with open(daily_file, "a") as f:
                f.write(entry)

    def get_session_transcript(
        self,
        client_id: str,
        session_id: str,
        limit: int = 100
    ) -> List[TranscriptMessage]:
        """Get messages for a specific session"""
        messages = []

        # Look through recent transcript files
        client_path = self.base_path / client_id
        if not client_path.exists():
            return messages

        for transcript_file in sorted(client_path.glob("*.jsonl"), reverse=True):
            with open(transcript_file) as f:
                for line in f:
                    entry = json.loads(line)
                    if entry.get("session_id") == session_id:
                        messages.append(TranscriptMessage(
                            timestamp=datetime.fromisoformat(entry["timestamp"]),
                            role=entry["role"],
                            content=entry["content"],
                            user_id=entry.get("user_id"),
                            metadata=entry.get("metadata", {})
                        ))

            if len(messages) >= limit:
                break

        # Return in chronological order
        messages.sort(key=lambda m: m.timestamp)
        return messages[:limit]

    def get_recent_messages(
        self,
        client_id: str,
        limit: int = 50,
        hours: int = 24
    ) -> List[TranscriptMessage]:
        """Get recent messages for a client"""
        messages = []
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        client_path = self.base_path / client_id
        if not client_path.exists():
            return messages

        for transcript_file in sorted(client_path.glob("*.jsonl"), reverse=True):
            with open(transcript_file) as f:
                for line in f:
                    entry = json.loads(line)
                    timestamp = datetime.fromisoformat(entry["timestamp"])

                    if timestamp >= cutoff:
                        messages.append(TranscriptMessage(
                            timestamp=timestamp,
                            role=entry["role"],
                            content=entry["content"],
                            user_id=entry.get("user_id"),
                            metadata=entry.get("metadata", {})
                        ))

            if len(messages) >= limit:
                break

        messages.sort(key=lambda m: m.timestamp, reverse=True)
        return messages[:limit]

    def search_transcripts(
        self,
        client_id: str,
        query: str,
        limit: int = 20
    ) -> List[TranscriptMessage]:
        """Search transcripts for a query string"""
        results = []
        query_lower = query.lower()

        client_path = self.base_path / client_id
        if not client_path.exists():
            return results

        for transcript_file in sorted(client_path.glob("*.jsonl"), reverse=True):
            with open(transcript_file) as f:
                for line in f:
                    entry = json.loads(line)
                    if query_lower in entry.get("content", "").lower():
                        results.append(TranscriptMessage(
                            timestamp=datetime.fromisoformat(entry["timestamp"]),
                            role=entry["role"],
                            content=entry["content"],
                            user_id=entry.get("user_id"),
                            metadata=entry.get("metadata", {})
                        ))

                        if len(results) >= limit:
                            return results

        return results

    def get_client_stats(self, client_id: str) -> Dict[str, Any]:
        """Get statistics for a client"""
        stats = {
            "total_messages": 0,
            "user_messages": 0,
            "assistant_messages": 0,
            "first_message": None,
            "last_message": None,
            "days_active": 0
        }

        client_path = self.base_path / client_id
        if not client_path.exists():
            return stats

        dates = set()
        for transcript_file in client_path.glob("*.jsonl"):
            with open(transcript_file) as f:
                for line in f:
                    entry = json.loads(line)
                    stats["total_messages"] += 1

                    if entry.get("role") == "user":
                        stats["user_messages"] += 1
                    elif entry.get("role") == "assistant":
                        stats["assistant_messages"] += 1

                    timestamp = datetime.fromisoformat(entry["timestamp"])
                    dates.add(timestamp.date())

                    if stats["first_message"] is None or timestamp < stats["first_message"]:
                        stats["first_message"] = timestamp
                    if stats["last_message"] is None or timestamp > stats["last_message"]:
                        stats["last_message"] = timestamp

        stats["days_active"] = len(dates)
        return stats

    def export_session(
        self,
        client_id: str,
        session_id: str,
        format: str = "json"
    ) -> str:
        """Export a session transcript"""
        messages = self.get_session_transcript(client_id, session_id)

        if format == "json":
            return json.dumps([
                {
                    "timestamp": m.timestamp.isoformat(),
                    "role": m.role,
                    "content": m.content
                }
                for m in messages
            ], indent=2)

        elif format == "markdown":
            lines = [f"# Transcript: {session_id}\n"]
            for m in messages:
                time_str = m.timestamp.strftime("%H:%M:%S")
                role = "User" if m.role == "user" else "Assistant"
                lines.append(f"**{time_str}** [{role}]\n{m.content}\n")
            return "\n".join(lines)

        elif format == "text":
            lines = []
            for m in messages:
                time_str = m.timestamp.strftime("%H:%M:%S")
                role = "U" if m.role == "user" else "A"
                lines.append(f"[{time_str}] {role}: {m.content}")
            return "\n".join(lines)

        return ""
