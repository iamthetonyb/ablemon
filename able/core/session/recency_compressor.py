"""
C4 — Recency-Weighted Context Compression.

Assigns different token budgets based on message age:
- Old messages (first 40%): truncated to 180 chars
- Mid messages (next 30%): truncated to 450 chars
- Recent messages (last 30%): kept up to 900 chars

Also deduplicates repeated read_file calls on the same path
(unless the file was written since the last read).

Usage:
    rc = RecencyCompressor()
    compressed = rc.compress(messages)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Default budget per age tier (chars)
OLD_LIMIT = 180
MID_LIMIT = 450
RECENT_LIMIT = 900

# Age tier split points (percentage of message list)
OLD_PCT = 0.40
MID_PCT = 0.30
# RECENT_PCT = 0.30 (implicit remainder)

# Hard cap on total output size (chars). Set to 0 to disable.
DEFAULT_HARD_CAP = 0


@dataclass
class CompressionStats:
    """Stats from a compression pass."""
    input_messages: int = 0
    output_messages: int = 0
    input_chars: int = 0
    output_chars: int = 0
    deduped_reads: int = 0
    truncated_messages: int = 0

    @property
    def compression_ratio(self) -> float:
        if self.input_chars == 0:
            return 0.0
        return 1.0 - self.output_chars / self.input_chars


class RecencyCompressor:
    """Recency-weighted context compressor.

    Messages are split into three tiers by position:
    old (first 40%), mid (next 30%), recent (last 30%).
    Each tier gets a different character budget.

    Repeated read_file tool calls on the same path are deduplicated
    unless a write to that path occurred in between.
    """

    def __init__(
        self,
        old_limit: int = OLD_LIMIT,
        mid_limit: int = MID_LIMIT,
        recent_limit: int = RECENT_LIMIT,
        hard_cap: int = DEFAULT_HARD_CAP,
        dedup_reads: bool = True,
    ):
        self._old_limit = old_limit
        self._mid_limit = mid_limit
        self._recent_limit = recent_limit
        self._hard_cap = hard_cap
        self._dedup_reads = dedup_reads
        self._last_stats: Optional[CompressionStats] = None

    def compress(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compress messages with recency-weighted truncation.

        Args:
            messages: Conversation message list (role + content dicts).

        Returns:
            Compressed message list (new list, original untouched).
        """
        if not messages:
            return []

        stats = CompressionStats(
            input_messages=len(messages),
            input_chars=sum(self._msg_len(m) for m in messages),
        )

        # Step 1: Dedup read_file calls
        if self._dedup_reads:
            messages = self._dedup_read_files(messages, stats)

        # Step 2: Recency-weighted truncation
        n = len(messages)
        old_end = int(n * OLD_PCT)
        mid_end = int(n * (OLD_PCT + MID_PCT))

        result = []
        for i, msg in enumerate(messages):
            if i < old_end:
                limit = self._old_limit
            elif i < mid_end:
                limit = self._mid_limit
            else:
                limit = self._recent_limit

            compressed = self._truncate_message(msg, limit)
            if compressed is not None:
                if self._msg_len(compressed) < self._msg_len(msg):
                    stats.truncated_messages += 1
                result.append(compressed)

        # Step 3: Hard cap (if enabled)
        if self._hard_cap > 0:
            result = self._apply_hard_cap(result, self._hard_cap)

        stats.output_messages = len(result)
        stats.output_chars = sum(self._msg_len(m) for m in result)
        self._last_stats = stats
        return result

    @property
    def last_stats(self) -> Optional[CompressionStats]:
        return self._last_stats

    # ── Deduplication ───────────────────────────────────────────

    def _dedup_read_files(
        self,
        messages: List[Dict[str, Any]],
        stats: CompressionStats,
    ) -> List[Dict[str, Any]]:
        """Remove earlier read_file calls if the same path is read again later.

        Keeps ALL reads if a write to the same path occurred between reads.
        """
        # First pass: collect read/write paths per message index
        read_indices: Dict[str, List[int]] = {}  # path → [indices]
        write_paths: Set[str] = set()
        write_indices: Dict[str, List[int]] = {}  # path → [indices]

        for i, msg in enumerate(messages):
            tool_calls = self._extract_tool_calls(msg)
            for tc in tool_calls:
                name = tc.get("name", "")
                path = (
                    tc.get("input", {}).get("path", "")
                    or tc.get("input", {}).get("file_path", "")
                )
                if not path:
                    continue

                if name == "read_file":
                    read_indices.setdefault(path, []).append(i)
                elif name in ("write_file", "Write", "edit_file", "Edit"):
                    write_paths.add(path)
                    write_indices.setdefault(path, []).append(i)

        # Second pass: mark duplicates for removal
        remove_indices: Set[int] = set()
        for path, indices in read_indices.items():
            if len(indices) < 2:
                continue

            w_indices = write_indices.get(path, [])

            # For each read except the last: remove if no write between it and the next read
            for k in range(len(indices) - 1):
                read_idx = indices[k]
                next_read_idx = indices[k + 1]

                # Check if any write to this path happened between these two reads
                write_between = any(
                    read_idx < wi < next_read_idx for wi in w_indices
                )
                if not write_between:
                    remove_indices.add(read_idx)
                    stats.deduped_reads += 1

        if not remove_indices:
            return messages

        return [m for i, m in enumerate(messages) if i not in remove_indices]

    # ── Truncation ──────────────────────────────────────────────

    def _truncate_message(
        self,
        msg: Dict[str, Any],
        limit: int,
    ) -> Optional[Dict[str, Any]]:
        """Truncate a message's content to the given char limit."""
        content = msg.get("content", "")

        # Handle list-type content (tool_use blocks)
        if isinstance(content, list):
            # For tool blocks, truncate individual text fields
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    text = block["text"]
                    if len(text) > limit:
                        new_blocks.append({
                            **block,
                            "text": text[:limit] + "...",
                        })
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)
            return {**msg, "content": new_blocks}

        if not isinstance(content, str):
            return msg

        if len(content) <= limit:
            return msg

        return {**msg, "content": content[:limit] + "..."}

    # ── Hard cap ────────────────────────────────────────────────

    def _apply_hard_cap(
        self,
        messages: List[Dict[str, Any]],
        cap: int,
    ) -> List[Dict[str, Any]]:
        """Trim from oldest until total chars are under cap."""
        total = sum(self._msg_len(m) for m in messages)
        if total <= cap:
            return messages

        # Remove from the front (oldest) until under cap
        result = list(messages)
        while result and total > cap:
            removed = result.pop(0)
            total -= self._msg_len(removed)

        return result

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _msg_len(msg: Dict[str, Any]) -> int:
        content = msg.get("content", "")
        if isinstance(content, str):
            return len(content)
        if isinstance(content, list):
            return sum(
                len(str(b.get("text", "") if isinstance(b, dict) else b))
                for b in content
            )
        return len(str(content))

    @staticmethod
    def _extract_tool_calls(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract tool call dicts from a message."""
        content = msg.get("content", "")
        if isinstance(content, list):
            return [
                b for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
        return []
