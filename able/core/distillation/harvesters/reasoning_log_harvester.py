"""
Harvester for ~/.able/logs/reasoning.jsonl — (prompt, thinking, response) triples.

Written by both cli/chat.py and gateway.py after every turn that produces a
reasoning trace. Each record has:
    ts, source, session, message_hash, model, elapsed_s,
    message_preview (≤400 chars), response_preview (≤400 chars),
    thinking (≤8000 chars), [domain, provider]  # gateway adds these

These triples are the highest-signal training data for Qwen 3.5 think-block
fine-tuning — they directly teach the model when and how to reason before
answering.

Source quality is 0.10 (slightly below able_cli at 0.14) because previews
are truncated; full text lives in the session JSONL and interaction DB.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from able.core.distillation.harvesters.base import BaseHarvester, HarvestedConversation

logger = logging.getLogger(__name__)

_LOG_PATH = Path.home() / ".able" / "logs" / "reasoning.jsonl"


class ReasoningLogHarvester(BaseHarvester):
    """Harvest (prompt, thinking, response) triples from reasoning.jsonl."""

    source_name = "reasoning_log"

    def __init__(self, log_path: str | Path | None = None):
        self.log_path = Path(log_path) if log_path else _LOG_PATH

    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        path = Path(source_path) if source_path else self.log_path
        if not path.exists():
            logger.debug("reasoning.jsonl not found at %s", path)
            return []

        results: list[HarvestedConversation] = []
        skipped = 0

        with open(path, encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed line %d in %s", line_no, path)
                    continue

                # Filter by time window
                if since is not None:
                    ts_str = record.get("ts", "")
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts < since:
                            skipped += 1
                            continue
                    except (ValueError, TypeError):
                        pass  # keep if timestamp unparseable

                thinking = record.get("thinking", "").strip()
                if not thinking:
                    # No thinking trace — not useful for think-block fine-tuning
                    skipped += 1
                    continue

                user_msg = record.get("message_preview", "").strip()
                assistant_msg = record.get("response_preview", "").strip()

                if not user_msg or not assistant_msg:
                    skipped += 1
                    continue

                # Short-circuit: skip meta/ack-only turns
                if len(user_msg) < 20 or len(assistant_msg) < 40:
                    skipped += 1
                    continue

                ts_str = record.get("ts", datetime.now(timezone.utc).isoformat())
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    ts = datetime.now(timezone.utc)

                domain = record.get("domain", "")
                if not domain:
                    domain = self._detect_domain(user_msg + " " + assistant_msg)

                conv = HarvestedConversation(
                    id=record.get("message_hash") or str(uuid.uuid4()),
                    source=self.source_name,
                    messages=[
                        {"role": "user", "content": user_msg},
                        {"role": "assistant", "content": assistant_msg},
                    ],
                    model=record.get("model", "unknown"),
                    timestamp=ts,
                    domain=domain,
                    thinking_blocks=[thinking],
                    metadata={
                        "session": record.get("session", ""),
                        "elapsed_s": record.get("elapsed_s", 0.0),
                        "provider": record.get("provider", record.get("source", "cli")),
                        "truncated": True,  # previews are capped — flag for scoring
                    },
                )
                results.append(conv)

        logger.info(
            "ReasoningLogHarvester: %d triples harvested, %d skipped from %s",
            len(results),
            skipped,
            path,
        )
        return results

    @staticmethod
    def _detect_domain(text: str) -> str:
        """Lightweight domain detection (mirrors base class keyword sets)."""
        text_lower = text.lower()
        checks = [
            ("security", ["vulnerability", "exploit", "injection", "xss", "csrf", "encryption", "threat", "audit"]),
            ("coding", ["function", "class", "def ", "import ", "debug", "refactor", "traceback", "async", "git "]),
            ("devops", ["deploy", "docker", "kubernetes", "pipeline", "terraform", "nginx", "container"]),
            ("data", ["sql", "database", "query", "schema", "pandas", "dataframe"]),
            ("research", ["investigate", "analyze", "compare", "evaluate", "findings", "hypothesis"]),
            ("copywriting", ["write", "draft", "email", "headline", "copy", "tone", "audience"]),
        ]
        for domain, keywords in checks:
            if any(kw in text_lower for kw in keywords):
                return domain
        return "general"
