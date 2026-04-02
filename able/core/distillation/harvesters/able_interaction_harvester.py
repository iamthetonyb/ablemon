"""
Harvester for the ABLE interaction log (``data/interaction_log.db``).

Reads routed interactions that completed successfully and converts
them into HarvestedConversation objects for the distillation pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data/interaction_log.db"


class ABLEInteractionHarvester(BaseHarvester):
    """Extract high-quality interactions from the ABLE interaction log."""

    source_name = "able"

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _DEFAULT_DB_PATH

    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        db = str(source_path) if source_path else self.db_path
        if not Path(db).exists():
            logger.warning("Interaction log DB not found: %s", db)
            return []

        results: list[HarvestedConversation] = []
        try:
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row

            query = (
                "SELECT * FROM interaction_log "
                "WHERE success = 1 AND message_preview != '' "
            )
            params: list = []
            if since:
                query += "AND timestamp >= ? "
                params.append(since.isoformat())
            query += "ORDER BY timestamp ASC"

            rows = conn.execute(query, params).fetchall()
            for row in rows:
                try:
                    convo = self._row_to_conversation(dict(row))
                    if convo is not None:
                        results.append(convo)
                except Exception:
                    logger.debug(
                        "Skipping malformed row %s", row["id"], exc_info=True
                    )

            conn.close()
        except Exception:
            logger.warning("Failed to read interaction log", exc_info=True)

        return results

    # ── Internal ───────────────────────────────────────────────────

    def _row_to_conversation(self, row: dict) -> HarvestedConversation | None:
        """Convert a single interaction_log row into a HarvestedConversation."""
        preview = row.get("message_preview", "")
        if not preview or len(preview.strip()) < 10:
            return None

        # Build a minimal user/assistant turn pair from what we have.
        # The interaction log stores a preview of the user message;
        # full raw_input/raw_output may or may not exist as columns.
        messages: list[dict] = [{"role": "user", "content": preview}]

        # If the table has raw columns (added by some deployments), use them.
        raw_output = row.get("raw_output", "")
        if raw_output:
            messages.append({"role": "assistant", "content": raw_output})

        # Only apply meta-conversation filter when we have both sides.
        # The interaction log typically only stores a user preview, so a
        # single-message record is expected — not a sign of meta-chatter.
        if len(messages) >= 2 and self._is_meta_conversation(messages):
            return None

        provider = row.get("actual_provider", "") or row.get("selected_provider", "")
        domain = row.get("domain", "") or self._detect_domain(messages)

        ts_str = row.get("timestamp", "")
        try:
            timestamp = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            timestamp = datetime.now()

        return HarvestedConversation(
            id=row.get("id", str(uuid.uuid4())),
            source=self.source_name,
            messages=messages,
            model=provider,
            timestamp=timestamp,
            domain=domain,
            metadata={
                "complexity_score": row.get("complexity_score", 0.0),
                "selected_tier": row.get("selected_tier", 0),
                "channel": row.get("channel", ""),
                "latency_ms": row.get("latency_ms", 0.0),
            },
        )
