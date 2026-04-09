"""
CVC-Inspired Context Compactor

When conversation context approaches the model's token limit, this module
summarizes the oldest portion and replaces it with a compact continuation
signal. Prevents context overflow while preserving critical information.

Inspired by CVC (Cognitive Version Control) Merkle DAG pattern:
- Snapshots context at decision boundaries
- Compacts when approaching limits
- Preserves tool call results and key decisions

Usage:
    compactor = ContextCompactor()
    messages = compactor.compact_if_needed(messages, model_context_limit=128000)
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default context thresholds
COMPACT_THRESHOLD = 0.80  # Compact when context reaches 80% of limit
COMPACT_RATIO = 0.60       # Summarize the oldest 60% of messages
SUMMARY_TOKEN_BUDGET = 500  # Max tokens for the summary itself

# Death spiral prevention (Hermes PR #4750 + post-v0.8 tail protection)
MAX_COMPRESSION_ATTEMPTS = 3  # Hard cap — prevents compress→fail→compress loops
MIN_TAIL_MESSAGES = 3         # Always keep at least this many recent messages

# Approximate token counting (1 token ≈ 4 chars for English)
_CHARS_PER_TOKEN = 4

# Errors that look like server disconnects but are actually context-length issues.
# When the payload is too large, some providers drop the connection instead of
# returning a proper 413. Treat these as compaction-triggerable.
CONTEXT_LENGTH_ERROR_PATTERNS = (
    "context_length_exceeded",
    "maximum context length",
    "request too large",
    "payload too large",
    "413",
    "content_too_large",
)

DISCONNECT_AS_CONTEXT_ERRORS = (
    "RemoteProtocolError",
    "ServerDisconnectedError",
    "ConnectionResetError",
    "ReadTimeout",
)


class ContextCompactor:
    """
    Manages context window compaction for long conversations.

    At 80% of the model's context limit, summarizes the oldest 60%
    of messages and replaces them with a compact continuation signal.
    """

    def __init__(
        self,
        compact_threshold: float = COMPACT_THRESHOLD,
        compact_ratio: float = COMPACT_RATIO,
        max_attempts: int = MAX_COMPRESSION_ATTEMPTS,
    ):
        self.threshold = compact_threshold
        self.ratio = compact_ratio
        self.max_attempts = max_attempts
        self._compression_attempts = 0  # Tracks attempts within a session

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Estimate total token count for a message list."""
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(str(block.get("text", "")))
                    else:
                        total_chars += len(str(block))
            # Add overhead for role, tool calls, etc.
            total_chars += 20
        return total_chars // _CHARS_PER_TOKEN

    def needs_compaction(
        self, messages: List[Dict[str, Any]], context_limit: int
    ) -> bool:
        """Check if messages exceed the compaction threshold."""
        estimated = self.estimate_tokens(messages)
        return estimated > (context_limit * self.threshold)

    def compact_if_needed(
        self,
        messages: List[Dict[str, Any]],
        context_limit: int,
        summary_fn: Optional[callable] = None,
    ) -> List[Dict[str, Any]]:
        """
        Compact messages if approaching context limit.

        Includes death spiral prevention (Hermes PR #4750):
        - Hard cap on compression attempts (default 3)
        - Verifies compression actually reduces message count
        - Preserves minimum tail messages for continuity

        Args:
            messages: Full conversation history.
            context_limit: Model's context window in tokens.
            summary_fn: Optional async function to generate LLM summary.
                        If None, uses extractive summarization.

        Returns:
            Compacted message list with summary replacing old messages.
        """
        if not self.needs_compaction(messages, context_limit):
            return messages

        # ── Strip-thinking recovery (gemma-gem pattern) ──────────
        # Before full compression, try stripping thinking blocks first.
        # This is cheaper (no summarization) and preserves more context.
        stripped = self._strip_thinking_blocks(messages)
        if not self.needs_compaction(stripped, context_limit):
            _saved_tokens = self.estimate_tokens(messages) - self.estimate_tokens(stripped)
            logger.info(
                "Strip-thinking recovery sufficient: reclaimed ~%d tokens, "
                "skipping full compaction",
                _saved_tokens,
            )
            return stripped
        # If stripping wasn't enough, continue with stripped messages
        # (still helps reduce load before full compaction)
        messages = stripped

        # Death spiral guard: stop after max attempts per session
        if self._compression_attempts >= self.max_attempts:
            logger.error(
                "Context compaction death spiral: %d/%d attempts exhausted. "
                "Context is at %d tokens vs %d limit. "
                "Consider reducing message size or increasing context limit.",
                self._compression_attempts,
                self.max_attempts,
                self.estimate_tokens(messages),
                context_limit,
            )
            return messages

        self._compression_attempts += 1
        original_len = len(messages)
        total = len(messages)
        compact_count = int(total * self.ratio)

        if compact_count < 2:
            return messages

        # Tail protection: always keep at least MIN_TAIL_MESSAGES recent messages
        keep_count = max(total - compact_count, MIN_TAIL_MESSAGES)
        compact_count = total - keep_count
        if compact_count < 2:
            return messages

        # Split into old (to summarize) and recent (to keep)
        old_messages = messages[:compact_count]
        recent_messages = messages[compact_count:]

        # Extract key information from old messages
        summary = self._extractive_summary(old_messages)

        # Build the continuation signal
        summary_message = {
            "role": "system",
            "content": (
                f"[CONTEXT SUMMARY — {compact_count} messages compacted]\n\n"
                f"{summary}\n\n"
                f"[END CONTEXT SUMMARY — conversation continues below]"
            ),
        }

        result = [summary_message] + recent_messages
        new_tokens = self.estimate_tokens(result)
        old_tokens = self.estimate_tokens(messages)

        # Verify compression actually reduced size — prevents no-op loops
        if len(result) >= original_len:
            logger.warning(
                "Compaction produced no reduction (%d → %d messages). "
                "Breaking to prevent death spiral.",
                original_len,
                len(result),
            )
            return messages

        logger.info(
            "Context compacted: %d messages → %d (%d → ~%d tokens, %.0f%% reduction) "
            "[attempt %d/%d]",
            total,
            len(result),
            old_tokens,
            new_tokens,
            (1 - new_tokens / max(old_tokens, 1)) * 100,
            self._compression_attempts,
            self.max_attempts,
        )

        return result

    def reset_compression_counter(self) -> None:
        """Reset the compression attempt counter. Call at session/turn boundaries."""
        self._compression_attempts = 0

    @staticmethod
    def is_context_length_error(error: Exception) -> bool:
        """
        Check if an error is actually a context-length issue in disguise.

        Some providers disconnect instead of returning 413. Hermes PR #4750
        found that RemoteProtocolError/ServerDisconnectedError during large
        payloads are almost always context-length exhaustion.
        """
        err_str = str(error).lower()
        err_type = type(error).__name__

        # Direct context-length errors
        if any(pat in err_str for pat in CONTEXT_LENGTH_ERROR_PATTERNS):
            return True

        # Disconnect errors that happen with large payloads
        if err_type in DISCONNECT_AS_CONTEXT_ERRORS:
            return True

        return False

    # Regex for thinking blocks — matches <think>...</think> and similar
    _THINK_RE = re.compile(
        r'<think>.*?</think>\s*|'
        r'\[Internal reasoning\].*?\[/Internal reasoning\]\s*',
        re.DOTALL | re.IGNORECASE,
    )

    def _strip_thinking_blocks(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Strip thinking/reasoning blocks from message content.

        Cheaper than full summarization — just removes <think>...</think>
        and [Internal reasoning] blocks from assistant messages.
        Returns a new list (does not mutate the original).
        """
        result = []
        for msg in messages:
            content = msg.get("content", "")
            if msg.get("role") == "assistant" and isinstance(content, str):
                stripped = self._THINK_RE.sub("", content).strip()
                if stripped != content:
                    result.append({**msg, "content": stripped})
                    continue
            result.append(msg)
        return result

    def _extractive_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Build a summary by extracting key decisions and tool results."""
        sections = []

        # 1. Extract user requests
        user_requests = []
        for msg in messages:
            if msg.get("role") == "user":
                content = self._get_text(msg)
                if content and len(content) > 10:
                    user_requests.append(content[:150])

        if user_requests:
            sections.append("**User requests:**")
            for i, req in enumerate(user_requests[:5], 1):
                sections.append(f"  {i}. {req}")

        # 2. Extract tool calls and results
        tool_calls = []
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_calls.append(
                                f"- {block.get('name', '?')}: {json.dumps(block.get('input', {}))[:100]}"
                            )

        if tool_calls:
            sections.append("\n**Tool calls executed:**")
            sections.extend(tool_calls[:10])

        # 3. Extract key decisions / assistant conclusions
        assistant_conclusions = []
        for msg in messages:
            if msg.get("role") == "assistant":
                content = self._get_text(msg)
                if content and len(content) > 50:
                    # Take the last 2 sentences as conclusion
                    sentences = content.split(". ")
                    conclusion = ". ".join(sentences[-2:])[:200]
                    assistant_conclusions.append(conclusion)

        if assistant_conclusions:
            sections.append("\n**Key conclusions:**")
            for c in assistant_conclusions[:5]:
                sections.append(f"  - {c}")

        # 4. Extract any errors or blockers
        errors = []
        for msg in messages:
            content = self._get_text(msg)
            if content and any(kw in content.lower() for kw in ["error", "failed", "blocked", "issue"]):
                errors.append(content[:100])

        if errors:
            sections.append("\n**Issues noted:**")
            for e in errors[:3]:
                sections.append(f"  - {e}")

        return "\n".join(sections) if sections else "Previous conversation context (no extractable summary)."

    def _get_text(self, msg: Dict[str, Any]) -> str:
        """Extract text content from a message."""
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block["text"])
                elif isinstance(block, str):
                    parts.append(block)
            return " ".join(parts)
        return str(content)

    def snapshot_hash(self, messages: List[Dict[str, Any]]) -> str:
        """Compute a SHA-256 hash of the current context state (Merkle-style)."""
        serialized = json.dumps(
            [{"role": m.get("role"), "content_hash": hashlib.sha256(
                self._get_text(m).encode()
            ).hexdigest()[:16]} for m in messages],
            sort_keys=True,
        )
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]
