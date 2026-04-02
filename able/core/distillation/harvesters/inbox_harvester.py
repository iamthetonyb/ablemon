"""
Harvester for manually-saved conversations in ``~/able-corpus-inbox/``.

Supports ``.jsonl``, ``.json``, and ``.txt`` files.  After a file is
processed it is moved into ``processed/`` so it won't be harvested twice.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)

logger = logging.getLogger(__name__)

_DEFAULT_INBOX = Path.home() / "able-corpus-inbox"


class InboxHarvester(BaseHarvester):
    """Watch an inbox directory for manually-dropped conversation files."""

    source_name = "inbox"

    def __init__(self, inbox_dir: str | Path | None = None):
        self.inbox_dir = Path(inbox_dir) if inbox_dir else _DEFAULT_INBOX

    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        inbox = Path(source_path) if source_path else self.inbox_dir

        # Create inbox + processed dirs if they don't exist
        inbox.mkdir(parents=True, exist_ok=True)
        processed_dir = inbox / "processed"
        processed_dir.mkdir(exist_ok=True)

        results: list[HarvestedConversation] = []
        for path in sorted(inbox.iterdir()):
            if path.is_dir():
                continue
            suffix = path.suffix.lower()
            if suffix not in (".jsonl", ".json", ".txt"):
                continue

            if since:
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                if mtime < since:
                    continue

            try:
                convos = self._process_file(path)
                results.extend(convos)
                # Move to processed
                shutil.move(str(path), str(processed_dir / path.name))
            except Exception:
                logger.warning("Failed to process inbox file %s", path, exc_info=True)

        return results

    # ── Format-specific parsers ────────────────────────────────────

    def _process_file(self, path: Path) -> list[HarvestedConversation]:
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            return self._parse_jsonl(path)
        if suffix == ".json":
            return self._parse_json(path)
        if suffix == ".txt":
            return self._parse_txt(path)
        return []

    def _parse_jsonl(self, path: Path) -> list[HarvestedConversation]:
        """Each line is a JSON object with at least ``messages``."""
        results: list[HarvestedConversation] = []
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                convo = self._obj_to_conversation(obj, path)
                if convo:
                    results.append(convo)
        return results

    def _parse_json(self, path: Path) -> list[HarvestedConversation]:
        """File is either a single conversation object or a list of them."""
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)

        if isinstance(data, list):
            results: list[HarvestedConversation] = []
            for obj in data:
                if isinstance(obj, dict):
                    convo = self._obj_to_conversation(obj, path)
                    if convo:
                        results.append(convo)
            return results

        if isinstance(data, dict):
            convo = self._obj_to_conversation(data, path)
            return [convo] if convo else []

        return []

    def _parse_txt(self, path: Path) -> list[HarvestedConversation]:
        """Plain text: assume alternating user / assistant paragraphs.

        Paragraphs are separated by blank lines.
        """
        text = path.read_text(encoding="utf-8", errors="replace")
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return []

        messages: list[dict] = []
        roles = ["user", "assistant"]
        for i, para in enumerate(paragraphs):
            messages.append({"role": roles[i % 2], "content": para})

        if self._is_meta_conversation(messages):
            return []

        return [
            HarvestedConversation(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, str(path))),
                source=self.source_name,
                messages=messages,
                model="unknown",
                timestamp=datetime.fromtimestamp(path.stat().st_mtime),
                domain=self._detect_domain(messages),
                metadata={"file": str(path)},
            )
        ]

    # ── Helpers ────────────────────────────────────────────────────

    def _obj_to_conversation(
        self, obj: dict, path: Path
    ) -> HarvestedConversation | None:
        """Convert a JSON object into a HarvestedConversation."""
        messages = obj.get("messages", [])
        if not messages or not isinstance(messages, list):
            return None

        if self._is_meta_conversation(messages):
            return None

        ts = obj.get("timestamp", None)
        if isinstance(ts, str):
            try:
                timestamp = datetime.fromisoformat(ts)
            except ValueError:
                timestamp = datetime.fromtimestamp(path.stat().st_mtime)
        else:
            timestamp = datetime.fromtimestamp(path.stat().st_mtime)

        return HarvestedConversation(
            id=obj.get("id", str(uuid.uuid4())),
            source=self.source_name,
            messages=messages,
            model=obj.get("model", "unknown"),
            timestamp=timestamp,
            domain=obj.get("domain", "") or self._detect_domain(messages),
            metadata={"file": str(path)},
        )
