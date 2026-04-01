"""
Session Replay — re-run past CLI sessions through different models.

Reads a recorded session transcript, extracts user messages, sends them
through a target model tier, and produces teacher-student distillation
pairs by comparing the original (teacher) response with the new
(student) response.

Usage::

    from atlas.cli.session_replay import replay_session, replay_batch

    # Single session
    pairs = await replay_session("session_abc123", target_tier=1)

    # Batch
    all_pairs = await replay_batch(["sess_1", "sess_2"], target_tier=1)
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_SESSIONS_DIR = Path.home() / ".atlas" / "sessions"


@dataclass
class ReplayPair:
    """A teacher-student pair generated from session replay."""

    id: str
    session_id: str
    user_message: str
    teacher_response: str
    teacher_model: str
    student_response: str
    student_model: str
    target_tier: int
    domain: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            raw = f"{self.user_message}:{self.teacher_response}"
            self.content_hash = hashlib.sha256(raw.encode()).hexdigest()

    def to_chatml(self, system_prompt: str = "") -> dict:
        """Convert to ChatML training format (teacher as gold)."""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": self.user_message})
        messages.append({"role": "assistant", "content": self.teacher_response})
        return {
            "conversations": messages,
            "metadata": {
                "source": "session_replay",
                "teacher_model": self.teacher_model,
                "student_model": self.student_model,
                "target_tier": self.target_tier,
                "domain": self.domain,
                "session_id": self.session_id,
                "content_hash": self.content_hash,
            },
        }


def _load_session(
    session_id: str,
    sessions_dir: Path | None = None,
) -> tuple[list[dict], str]:
    """Load a session transcript and return (messages, model_name).

    Looks for ``{session_id}.jsonl`` in the sessions directory.
    Returns the parsed message list and the original model name.
    """
    base = sessions_dir or _DEFAULT_SESSIONS_DIR
    session_file = base / f"{session_id}.jsonl"

    if not session_file.exists():
        # Try glob match for partial IDs
        matches = list(base.glob(f"*{session_id}*.jsonl"))
        if not matches:
            raise FileNotFoundError(f"Session not found: {session_id} in {base}")
        session_file = matches[0]

    messages: list[dict] = []
    model_name = "unknown"

    with open(session_file, "r", encoding="utf-8", errors="replace") as fh:
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
            if isinstance(content, list):
                text_parts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content = "\n".join(text_parts)

            if not content:
                continue

            messages.append({"role": role, "content": content})

            if role == "assistant" and record.get("model"):
                model_name = record["model"]

    return messages, model_name


async def _call_model(
    messages: list[dict],
    target_tier: int,
) -> tuple[str, str]:
    """Send messages to a target tier model and return (response, model_name).

    Attempts to use the ATLAS routing provider. Falls back to a stub
    response when the provider is unavailable (e.g. in tests or offline).
    """
    try:
        from atlas.core.routing.provider_registry import ProviderRegistry
        registry = ProviderRegistry()
        provider = registry.get_provider_for_tier(target_tier)
        result = await provider.complete(messages)
        return result.content, result.model
    except Exception as exc:
        logger.warning(
            "Provider unavailable for tier %d, returning stub: %s",
            target_tier, exc,
        )
        return f"[replay stub — tier {target_tier} unavailable]", f"stub-tier-{target_tier}"


async def replay_session(
    session_id: str,
    target_tier: int = 1,
    sessions_dir: Path | str | None = None,
) -> list[ReplayPair]:
    """Replay a single session through a different model tier.

    Extracts each user turn from the original transcript, sends it (with
    preceding context) through the target tier model, and pairs the
    original teacher response with the new student response.

    Args:
        session_id: Filename stem or partial match for the session JSONL.
        target_tier: Model tier (1-5) to replay through.
        sessions_dir: Override default sessions directory.

    Returns:
        List of ReplayPair objects (one per user turn in the session).
    """
    base = Path(sessions_dir) if sessions_dir else None
    messages, teacher_model = _load_session(session_id, sessions_dir=base)
    if not messages:
        logger.warning("Session %s has no messages", session_id)
        return []

    pairs: list[ReplayPair] = []
    context: list[dict] = []

    for i, msg in enumerate(messages):
        if msg["role"] == "user":
            # Find the corresponding teacher response
            teacher_response = ""
            for j in range(i + 1, len(messages)):
                if messages[j]["role"] == "assistant":
                    teacher_response = messages[j]["content"]
                    break

            if not teacher_response:
                context.append(msg)
                continue

            # Build context for the student model: prior turns + current user
            student_input = list(context) + [msg]

            student_response, student_model = await _call_model(
                student_input, target_tier
            )

            pair = ReplayPair(
                id=str(uuid.uuid4()),
                session_id=session_id,
                user_message=msg["content"],
                teacher_response=teacher_response,
                teacher_model=teacher_model,
                student_response=student_response,
                student_model=student_model,
                target_tier=target_tier,
            )
            pairs.append(pair)

        # Accumulate context for subsequent turns
        context.append(msg)

    logger.info(
        "[replay] Session %s: %d pairs (teacher=%s, student_tier=%d)",
        session_id, len(pairs), teacher_model, target_tier,
    )
    return pairs


async def replay_batch(
    session_ids: list[str],
    target_tier: int = 1,
    sessions_dir: Path | str | None = None,
) -> list[ReplayPair]:
    """Replay multiple sessions through a different model tier.

    Args:
        session_ids: List of session IDs to replay.
        target_tier: Model tier (1-5) to replay through.
        sessions_dir: Override default sessions directory.

    Returns:
        Flat list of all ReplayPair objects across all sessions.
    """
    all_pairs: list[ReplayPair] = []
    for sid in session_ids:
        try:
            pairs = await replay_session(
                sid, target_tier=target_tier, sessions_dir=sessions_dir,
            )
            all_pairs.extend(pairs)
        except Exception:
            logger.warning("Failed to replay session %s", sid, exc_info=True)
    logger.info(
        "[replay_batch] Replayed %d sessions, %d total pairs",
        len(session_ids), len(all_pairs),
    )
    return all_pairs
