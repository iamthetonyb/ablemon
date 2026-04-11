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
from typing import Any, Callable, Dict, List, Optional

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
        event_callback: Optional[Callable] = None,
    ):
        self.threshold = compact_threshold
        self.ratio = compact_ratio
        self.max_attempts = max_attempts
        self._compression_attempts = 0  # Tracks attempts within a session
        self._event_callback = event_callback  # Called after successful compaction
        self._last_compaction_event: Optional[Dict[str, Any]] = None  # Accumulated event for gateway
        self._first_tokens_before: Optional[int] = None  # Track across multiple compactions

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

    # Recency-weighted content limits (C4 — mini-coding-agent pattern)
    # Graduated compression: oldest messages get heaviest truncation
    _RECENCY_LIMITS = {
        "old": 180,      # Oldest 40% — compressed to 180 chars
        "mid": 500,      # Middle 30% — keep up to 500 chars
        "recent": 900,   # Recent 30% — keep up to 900 chars
    }

    def compact_if_needed(
        self,
        messages: List[Dict[str, Any]],
        context_limit: int,
        summary_fn: Optional[callable] = None,
    ) -> List[Dict[str, Any]]:
        """
        Compact messages if approaching context limit.

        Strategy (layered, cheapest first):
        1. Strip thinking blocks (free — just removes <think> tags)
        2. Dedup repeated read_file calls on same path (C4)
        3. Recency-weighted compression (C4 — graduated, not binary split)
        4. Full extractive summary of oldest chunk (original fallback)

        Includes death spiral prevention (Hermes PR #4750):
        - Hard cap on compression attempts (default 3)
        - Verifies compression actually reduces message count
        - Preserves minimum tail messages for continuity
        """
        if not self.needs_compaction(messages, context_limit):
            return messages

        # ── Layer 1: Strip-thinking recovery (gemma-gem pattern) ──
        stripped = self._strip_thinking_blocks(messages)
        if not self.needs_compaction(stripped, context_limit):
            _saved_tokens = self.estimate_tokens(messages) - self.estimate_tokens(stripped)
            logger.info(
                "Strip-thinking recovery sufficient: reclaimed ~%d tokens, "
                "skipping full compaction",
                _saved_tokens,
            )
            return stripped
        messages = stripped

        # ── Layer 2: Dedup repeated read_file calls (C4) ──────────
        deduped = self._dedup_read_file(messages)
        if not self.needs_compaction(deduped, context_limit):
            _saved = self.estimate_tokens(messages) - self.estimate_tokens(deduped)
            logger.info("read_file dedup sufficient: reclaimed ~%d tokens", _saved)
            return deduped
        messages = deduped

        # ── Layer 3: Recency-weighted compression (C4) ────────────
        recency_compressed = self._recency_compress(messages)
        if not self.needs_compaction(recency_compressed, context_limit):
            _saved = self.estimate_tokens(messages) - self.estimate_tokens(recency_compressed)
            logger.info("Recency compression sufficient: reclaimed ~%d tokens", _saved)
            return recency_compressed
        messages = recency_compressed

        # ── Layer 4: Full extractive summary (original fallback) ──
        # Death spiral guard
        if self._compression_attempts >= self.max_attempts:
            logger.error(
                "Context compaction death spiral: %d/%d attempts exhausted. "
                "Context is at %d tokens vs %d limit.",
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

        # Tail protection
        keep_count = max(total - compact_count, MIN_TAIL_MESSAGES)
        compact_count = total - keep_count
        if compact_count < 2:
            return messages

        old_messages = messages[:compact_count]
        recent_messages = messages[compact_count:]

        # ── E8: Iterative compression ─────────────────────────────
        # If the first old message is already a context summary from a
        # prior compaction, merge the new summary into it rather than
        # re-summarizing from scratch. This preserves more actionable
        # state across multiple compressions.
        existing_summary = self._extract_existing_summary(old_messages)
        new_summary = self._extractive_summary(
            old_messages[1:] if existing_summary else old_messages
        )

        if existing_summary:
            summary = self._merge_summaries(existing_summary, new_summary)
            _total_compacted = compact_count  # Includes the prior summary msg
        else:
            summary = new_summary
            _total_compacted = compact_count

        summary_message = {
            "role": "system",
            "content": (
                f"[CONTEXT SUMMARY — {_total_compacted} messages compacted]\n\n"
                f"{summary}\n\n"
                f"[END CONTEXT SUMMARY — conversation continues below]"
            ),
        }

        result = [summary_message] + recent_messages
        new_tokens = self.estimate_tokens(result)
        old_tokens = self.estimate_tokens(messages)

        if len(result) >= original_len:
            logger.warning(
                "Compaction produced no reduction (%d → %d messages). "
                "Breaking to prevent death spiral.",
                original_len,
                len(result),
            )
            return messages

        reduction_pct = (1 - new_tokens / max(old_tokens, 1)) * 100
        logger.info(
            "Context compacted: %d messages → %d (%d → ~%d tokens, %.0f%% reduction) "
            "[attempt %d/%d]",
            total,
            len(result),
            old_tokens,
            new_tokens,
            reduction_pct,
            self._compression_attempts,
            self.max_attempts,
        )

        # Telemetry event (accumulated across compactions)
        if self._first_tokens_before is None:
            self._first_tokens_before = old_tokens
        _event = {
            "event": "context_compaction",
            "tokens_before": self._first_tokens_before,
            "tokens_after": new_tokens,
            "ratio": round(new_tokens / max(self._first_tokens_before, 1), 4),
            "messages_before": total,
            "messages_after": len(result),
            "reduction_pct": round((1 - new_tokens / max(self._first_tokens_before, 1)) * 100, 1),
            "attempt": self._compression_attempts,
        }
        self._last_compaction_event = _event

        if self._event_callback is not None:
            try:
                self._event_callback(_event)
            except Exception:
                logger.debug("Compression event callback failed", exc_info=True)

        return result

    # ── C4: Dedup repeated read_file calls ────────────────────────

    def _dedup_read_file(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate read_file tool results for the same path.

        If the same file was read multiple times and NOT written between reads,
        keep only the last read. Written files invalidate the dedup since content
        may have changed.
        """
        # Build a set of written file paths
        written_paths: set = set()
        read_indices: Dict[str, List[int]] = {}  # path → [indices of read messages]

        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            path = inp.get("file_path") or inp.get("path", "")
                            if name in ("read_file", "Read") and path:
                                read_indices.setdefault(path, []).append(i)
                            elif name in ("write_file", "Write", "edit_file", "Edit") and path:
                                written_paths.add(path)

        # Find indices to remove: for each path not written, remove all but last read
        remove_indices: set = set()
        for path, indices in read_indices.items():
            if path in written_paths:
                continue  # File was modified — keep all reads
            if len(indices) > 1:
                # Keep only the last read
                for idx in indices[:-1]:
                    remove_indices.add(idx)

        if not remove_indices:
            return messages

        logger.debug("read_file dedup: removing %d duplicate reads", len(remove_indices))
        return [m for i, m in enumerate(messages) if i not in remove_indices]

    # ── C4: Recency-weighted compression ──────────────────────────

    def _recency_compress(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply graduated compression based on message recency.

        Old messages (first 40%): truncate content to 180 chars
        Mid messages (next 30%): truncate to 500 chars
        Recent messages (last 30%): keep up to 900 chars

        More nuanced than binary split — preserves structure while reducing tokens.
        """
        n = len(messages)
        if n < 4:
            return messages

        old_end = int(n * 0.4)
        mid_end = int(n * 0.7)

        result = []
        for i, msg in enumerate(messages):
            if i < old_end:
                limit = self._RECENCY_LIMITS["old"]
            elif i < mid_end:
                limit = self._RECENCY_LIMITS["mid"]
            else:
                limit = self._RECENCY_LIMITS["recent"]

            result.append(self._truncate_message(msg, limit))

        return result

    def _truncate_message(self, msg: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
        """Truncate a message's text content to max_chars."""
        content = msg.get("content", "")
        if isinstance(content, str):
            if len(content) <= max_chars:
                return msg
            return {**msg, "content": content[:max_chars] + "..."}
        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if len(text) > max_chars:
                        new_blocks.append({**block, "text": text[:max_chars] + "..."})
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)
            return {**msg, "content": new_blocks}
        return msg

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

    # ── E8: Iterative compression helpers ───────────────────────

    _SUMMARY_MARKER = "[CONTEXT SUMMARY"

    def _extract_existing_summary(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Check if the first message is a prior context summary.

        Returns the summary text (between markers) or None.
        """
        if not messages:
            return None
        first = messages[0]
        if first.get("role") != "system":
            return None
        content = self._get_text(first)
        if not content.startswith(self._SUMMARY_MARKER):
            return None
        # Extract body between the header and footer markers
        start = content.find("\n\n")
        end = content.rfind("\n\n[END CONTEXT SUMMARY")
        if start == -1 or end == -1 or end <= start:
            return content  # Malformed but still a summary
        return content[start + 2:end]

    def _merge_summaries(self, existing: str, new: str) -> str:
        """Merge an existing context summary with new extractive content.

        Strategy: parse both into section dicts, merge per section
        (deduplicated, capped), produce a single combined summary.
        This preserves historical context while adding new observations.
        """
        existing_sections = self._parse_summary_sections(existing)
        new_sections = self._parse_summary_sections(new)

        # Section merge order and limits
        SECTION_LIMITS = {
            "User requests": 7,
            "Tool calls executed": 12,
            "Key conclusions": 7,
            "Issues noted": 5,
        }

        merged_parts = []
        all_keys = list(dict.fromkeys(list(existing_sections) + list(new_sections)))
        for key in all_keys:
            limit = SECTION_LIMITS.get(key, 8)
            old_items = existing_sections.get(key, [])
            new_items = new_sections.get(key, [])
            # New items first (more recent), then old, deduped
            seen = set()
            combined = []
            for item in new_items + old_items:
                # Strip numbering/bullets before dedup comparison
                _stripped = item.strip().lower()
                _stripped = re.sub(r"^\d+\.\s*", "", _stripped)  # "1. foo" → "foo"
                _stripped = re.sub(r"^-\s*", "", _stripped)      # "- foo" → "foo"
                normalized = _stripped[:80]
                if normalized not in seen:
                    seen.add(normalized)
                    combined.append(item)
            combined = combined[:limit]
            if combined:
                merged_parts.append(f"**{key}:**")
                merged_parts.extend(combined)
                merged_parts.append("")

        return "\n".join(merged_parts).strip() if merged_parts else new or existing

    @staticmethod
    def _parse_summary_sections(text: str) -> Dict[str, List[str]]:
        """Parse a structured summary into {section_name: [items]}.

        Expects format like:
            **Section Name:**
              - item 1
              - item 2
        """
        sections: Dict[str, List[str]] = {}
        current_section = None
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("**") and stripped.endswith(":**"):
                current_section = stripped.strip("*").strip().rstrip(":")
                sections[current_section] = []
            elif current_section is not None and stripped:
                sections[current_section].append(line)
        return sections

    def snapshot_hash(self, messages: List[Dict[str, Any]]) -> str:
        """Compute a SHA-256 hash of the current context state (Merkle-style)."""
        serialized = json.dumps(
            [{"role": m.get("role"), "content_hash": hashlib.sha256(
                self._get_text(m).encode()
            ).hexdigest()[:16]} for m in messages],
            sort_keys=True,
        )
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]
