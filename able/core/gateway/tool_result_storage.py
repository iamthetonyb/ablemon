"""
Tool Result Persistence — 3-layer defense against context overflow from large tool outputs.

Forked from Hermes v0.8 PR #5210 + #6085. Adapted for ABLE's gateway.

Layer 1: Tools pre-truncate their own output (handled by each tool)
Layer 2: maybe_persist_tool_result() — if output > threshold, save to disk,
         replace inline with pointer + summary
Layer 3: enforce_turn_budget() — after all tool calls in a turn, if total
         exceeds budget, spill largest to disk

CRITICAL: read_file threshold = float("inf") (pinned) to prevent
          infinite persist→read→persist loops.
"""

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Default thresholds (in characters — ~4 chars per token)
DEFAULT_TOKEN_THRESHOLD = 4000  # ~1000 tokens → persist to disk
TURN_BUDGET_CHARS = 200_000    # ~50K tokens max per turn of tool results

# Tools that should never have their output persisted (prevents loops)
_PERSIST_EXEMPT_TOOLS = frozenset({
    "read_file", "read", "cat", "head", "tail",
    "Read",  # Claude Code naming
})

# Where to store persisted results
_STORAGE_DIR = "data/tool_results"


def _get_storage_dir() -> Path:
    """Get or create the tool result storage directory."""
    # Resolve relative to project root (4 levels up from this file)
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    storage = project_root / _STORAGE_DIR
    storage.mkdir(parents=True, exist_ok=True)
    return storage


def maybe_persist_tool_result(
    tool_name: str,
    tool_use_id: str,
    output: str,
    threshold_chars: int = DEFAULT_TOKEN_THRESHOLD * 4,
) -> Tuple[str, bool]:
    """
    Persist large tool output to disk, return summary pointer.

    Args:
        tool_name: Name of the tool that produced the output.
        tool_use_id: Unique ID for this tool call (used as filename).
        output: The full tool output string.
        threshold_chars: Character threshold above which to persist.

    Returns:
        (output_or_pointer, was_persisted) — either the original output
        (if small enough) or a pointer string with summary.
    """
    if not output or len(output) <= threshold_chars:
        return output, False

    # Never persist read_file output — creates infinite loops
    if tool_name in _PERSIST_EXEMPT_TOOLS:
        return output, False

    try:
        storage = _get_storage_dir()
        # Use tool_use_id as filename (sanitize)
        safe_id = tool_use_id.replace("/", "_").replace("..", "_")[:64]
        filepath = storage / f"{safe_id}.txt"
        filepath.write_text(output, encoding="utf-8")

        # Build summary: first 500 chars + metadata
        est_tokens = len(output) // 4
        summary_lines = output[:500].rstrip()

        pointer = (
            f"[Full output saved to {filepath} — {est_tokens} tokens. "
            f"Key findings: {summary_lines}]"
        )

        logger.info(
            "Tool result persisted: %s (%d chars → %s)",
            tool_name, len(output), filepath,
        )
        return pointer, True

    except Exception as e:
        logger.warning("Failed to persist tool result: %s", e)
        return output, False


def enforce_turn_budget(
    tool_outputs: list,
    budget_chars: int = TURN_BUDGET_CHARS,
) -> list:
    """
    After all tool calls in a turn, enforce total budget.

    If combined output exceeds budget, spill the largest outputs to disk.

    Args:
        tool_outputs: List of dicts with keys: tool_name, tool_use_id, output
        budget_chars: Maximum total characters across all tool outputs.

    Returns:
        Updated list with large outputs replaced by pointers.
    """
    total = sum(len(t.get("output", "")) for t in tool_outputs)
    if total <= budget_chars:
        return tool_outputs

    # Sort by size descending — spill largest first
    sorted_by_size = sorted(
        enumerate(tool_outputs),
        key=lambda x: len(x[1].get("output", "")),
        reverse=True,
    )

    result = list(tool_outputs)  # copy
    remaining = total

    for idx, tool_data in sorted_by_size:
        if remaining <= budget_chars:
            break

        output = tool_data.get("output", "")
        if len(output) <= DEFAULT_TOKEN_THRESHOLD * 4:
            continue  # Don't spill small outputs

        tool_name = tool_data.get("tool_name", "unknown")
        tool_use_id = tool_data.get("tool_use_id", hashlib.md5(output[:100].encode()).hexdigest()[:12])

        pointer, was_persisted = maybe_persist_tool_result(
            tool_name, tool_use_id, output,
        )
        if was_persisted:
            remaining -= len(output) - len(pointer)
            result[idx] = {**tool_data, "output": pointer}

    logger.info(
        "Turn budget enforced: %d → %d chars (%d tools spilled)",
        total, remaining, sum(1 for r, o in zip(result, tool_outputs) if r.get("output") != o.get("output")),
    )
    return result


def cleanup_old_results(max_age_hours: int = 24) -> int:
    """Remove persisted tool results older than max_age_hours."""
    import time
    storage = _get_storage_dir()
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for f in storage.glob("*.txt"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    if removed:
        logger.info("Cleaned up %d old tool result files", removed)
    return removed
