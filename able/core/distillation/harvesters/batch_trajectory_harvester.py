"""
Batch Trajectory Harvester — plugs synthetic training data into the main
distillation pipeline.

Reads ``data/batch_trajectories.jsonl`` (output from BatchTrajectoryRunner)
and converts each (prompt, response) pair to a HarvestedConversation.  This
means synthetically generated trajectories automatically feed:

  - The nightly harvest → TrainingFormatter → ChatML corpus
  - The DPO builder → chosen/rejected pairs (high-quality batches become
    "chosen"; any failed or low-scoring ones become "rejected" candidates)
  - The H100 fine-tune cycle — effectively a data flywheel

Priority in SOURCE_PRIORITY: 4 (same as Codex — synthetic, curated quality)

The harvester is idempotent: it tracks which records it has already imported
via a cursor file (``data/.batch_traj_harvest_cursor``) so it only processes
new records on each run.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
    _SCAFFOLDING_TAG_RE,
)


def _strip_scaffolding(text: str) -> str:
    """Remove scaffolding tags from text (inline to avoid importing private helper)."""
    return _SCAFFOLDING_TAG_RE.sub("", text).strip()

logger = logging.getLogger(__name__)

_DEFAULT_JSONL = Path("data/batch_trajectories.jsonl")
_CURSOR_FILE = Path("data/.batch_traj_harvest_cursor")

# Minimum response length to be included as a training pair
_MIN_RESPONSE_CHARS = 80


class BatchTrajectoryHarvester(BaseHarvester):
    """
    Harvest (prompt, response) pairs from the batch trajectory runner output.

    Each pair becomes a two-turn HarvestedConversation:
        [{"role": "user", "content": prompt},
         {"role": "assistant", "content": response}]

    Thinking blocks (when the model produced extended reasoning) are preserved
    in thinking_blocks for the distillation formatter.
    """

    source_name = "batch_trajectory"

    def __init__(self, jsonl_path: Optional[Path] = None):
        self.jsonl_path = Path(jsonl_path) if jsonl_path else _DEFAULT_JSONL

    def harvest(
        self,
        source_path=None,
        since: Optional[datetime] = None,
    ) -> list[HarvestedConversation]:
        p = Path(source_path) if source_path else self.jsonl_path
        if not p.exists():
            logger.debug("batch_trajectories.jsonl not found at %s — nothing to harvest", p)
            return []

        # Load cursor (last processed record timestamp)
        cursor_ts: Optional[datetime] = None
        if _CURSOR_FILE.exists():
            try:
                raw = _CURSOR_FILE.read_text().strip()
                if raw:
                    if raw.endswith("Z"):
                        raw = raw[:-1] + "+00:00"
                    cursor_ts = datetime.fromisoformat(raw)
                    if cursor_ts.tzinfo is None:
                        cursor_ts = cursor_ts.replace(tzinfo=timezone.utc)
            except Exception:
                cursor_ts = None

        # Effective cutoff is the later of `since` and the saved cursor
        cutoff: Optional[datetime] = None
        if cursor_ts and since:
            cutoff = max(cursor_ts, since)
        elif cursor_ts:
            cutoff = cursor_ts
        elif since:
            cutoff = since

        results: list[HarvestedConversation] = []
        latest_ts: Optional[datetime] = None

        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Parse timestamp
                ts: Optional[datetime] = None
                ts_str = record.get("timestamp", "")
                if ts_str:
                    try:
                        if ts_str.endswith("Z"):
                            ts_str = ts_str[:-1] + "+00:00"
                        ts = datetime.fromisoformat(ts_str)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                    except Exception:
                        ts = None

                # Skip records before cutoff
                if cutoff and ts and ts <= cutoff:
                    continue

                prompt = record.get("prompt", "").strip()
                response = record.get("response", "").strip()
                thinking = record.get("thinking", "")

                # Basic quality gate
                if not prompt or len(response) < _MIN_RESPONSE_CHARS:
                    continue

                # Strip any scaffolding tags that leaked into the response
                response = _strip_scaffolding(response)
                if len(response) < _MIN_RESPONSE_CHARS:
                    continue

                domain = record.get("domain", "default")
                model = record.get("model", "unknown")
                source_tag = record.get("source", "batch_trajectory")
                content_hash = hashlib.sha256(f"{prompt}::{response}".encode()).hexdigest()[:16]

                conv = HarvestedConversation(
                    id=f"batch_{record.get('id', content_hash)}",
                    source="batch_trajectory",
                    messages=[
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": response},
                    ],
                    model=model,
                    timestamp=ts or datetime.now(timezone.utc),
                    domain=domain,
                    thinking_blocks=[thinking] if thinking else [],
                    metadata={
                        "tier": record.get("tier"),
                        "complexity_score": record.get("complexity_score"),
                        "provider": record.get("provider"),
                        "latency_ms": record.get("latency_ms"),
                        "input_tokens": record.get("input_tokens"),
                        "output_tokens": record.get("output_tokens"),
                        "original_source": source_tag,
                    },
                    content_hash=content_hash,
                )
                results.append(conv)

                if ts and (latest_ts is None or ts > latest_ts):
                    latest_ts = ts

        # Advance cursor to the latest timestamp we processed
        if latest_ts is not None:
            try:
                _CURSOR_FILE.write_text(latest_ts.isoformat())
            except OSError as exc:
                logger.debug("Failed to write harvest cursor: %s", exc)

        logger.info(
            "BatchTrajectoryHarvester: %d conversations harvested from %s",
            len(results),
            p,
        )
        return results
