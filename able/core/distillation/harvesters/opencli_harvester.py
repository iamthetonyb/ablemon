"""
OpenCLI harvester — drives multiple AI-platform adapters defined as YAML.

Each adapter YAML file in ``opencli_adapters/`` describes how to extract
conversations from a specific platform (ChatGPT, Codex, Grok, etc.).
"""

from __future__ import annotations

import glob as globmod
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)

logger = logging.getLogger(__name__)


class OpenCLIHarvester(BaseHarvester):
    """
    Uses OpenCLI adapters to harvest from multiple AI platforms.

    Each adapter is a YAML file in ``opencli_adapters/`` that defines:
    - platform name
    - harvest method (``file``, ``command``, or ``browser``)
    - file path patterns or commands
    - output parsing rules
    - model name mapping
    """

    source_name = "opencli"

    def __init__(self, adapters_dir: str | None = None):
        self.adapters_dir = adapters_dir or os.path.join(
            os.path.dirname(__file__), "opencli_adapters"
        )
        self.adapters: dict[str, dict] = self._discover_adapters()

    def _discover_adapters(self) -> dict[str, dict]:
        """Auto-scan ``opencli_adapters/`` for ``.yaml`` files."""
        adapters: dict[str, dict] = {}
        adapters_path = Path(self.adapters_dir)
        if not adapters_path.is_dir():
            logger.warning("Adapters directory not found: %s", self.adapters_dir)
            return adapters

        for yaml_file in sorted(adapters_path.glob("*.yaml")):
            try:
                with open(yaml_file, "r", encoding="utf-8") as fh:
                    cfg = yaml.safe_load(fh) or {}
                platform = cfg.get("platform", yaml_file.stem)
                adapters[platform] = cfg
                adapters[platform]["_path"] = str(yaml_file)
            except Exception:
                logger.warning("Bad adapter YAML: %s", yaml_file, exc_info=True)

        return adapters

    # ── Public API ─────────────────────────────────────────────────

    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        """Run all discovered adapters and aggregate results."""
        results: list[HarvestedConversation] = []
        for platform in self.adapters:
            try:
                results.extend(self.harvest_platform(platform, since=since))
            except Exception:
                logger.warning("Adapter %s failed", platform, exc_info=True)
        return results

    def harvest_platform(
        self,
        platform: str,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        """Harvest from a specific platform using its adapter config."""
        cfg = self.adapters.get(platform)
        if cfg is None:
            logger.warning("No adapter found for platform: %s", platform)
            return []

        method = cfg.get("harvest_method", "file")
        if method == "file":
            return self._harvest_files(cfg, since)
        if method == "command":
            return self._harvest_command(cfg, since)
        # browser method is a placeholder — not implemented yet
        logger.info(
            "Harvest method '%s' for %s is not yet implemented",
            method, platform,
        )
        return []

    def register_adapter(self, adapter_path: str) -> None:
        """Register a new adapter YAML file at runtime."""
        path = Path(adapter_path)
        if not path.exists():
            raise FileNotFoundError(f"Adapter file not found: {adapter_path}")

        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}

        platform = cfg.get("platform", path.stem)
        cfg["_path"] = str(path)
        self.adapters[platform] = cfg

    # ── File-based harvesting ──────────────────────────────────────

    def _harvest_files(
        self, cfg: dict, since: datetime | None
    ) -> list[HarvestedConversation]:
        """Harvest conversations from files matching adapter patterns."""
        patterns = cfg.get("file_patterns", [])
        model_name = cfg.get("model_name", "unknown")
        platform = cfg.get("platform", "unknown")
        role_map = cfg.get("role_mapping", {})
        message_path = cfg.get("message_path", "")
        thinking_field = cfg.get("thinking_field", None)
        payload_key = cfg.get("payload_key", "")

        results: list[HarvestedConversation] = []
        for pattern in patterns:
            expanded = os.path.expanduser(pattern)
            for filepath in globmod.glob(expanded, recursive=True):
                path = Path(filepath)
                if since:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime)
                    # Normalize timezone awareness for comparison
                    if since.tzinfo is not None and mtime.tzinfo is None:
                        from datetime import timezone as _tz
                        mtime = mtime.replace(tzinfo=_tz.utc)
                    if mtime < since:
                        continue
                try:
                    convos = self._parse_export_file(
                        path, platform, model_name, role_map,
                        message_path, thinking_field, payload_key,
                    )
                    results.extend(convos)
                except Exception:
                    logger.warning("Failed to parse %s", path, exc_info=True)

        return results

    def _parse_export_file(
        self,
        path: Path,
        platform: str,
        model_name: str,
        role_map: dict,
        message_path: str,
        thinking_field: str | None,
        payload_key: str = "",
    ) -> list[HarvestedConversation]:
        """Parse a platform export file (JSON or JSONL) into conversations."""
        data = self._load_file(path)
        if not data:
            return []

        # For JSONL files: two modes depending on message_path.
        # - With message_path (e.g. Manus): each line is a conversation
        #   with a nested messages array → extract per-line conversations.
        # - Without message_path (e.g. Codex): each line is a single
        #   event record → collect into one conversation.
        if path.suffix == ".jsonl":
            # Check if any of the first 3 records have a message_path key —
            # if so, this is per-line conversation format (Manus, ChatGPT export)
            has_nested_messages = message_path and any(
                isinstance(r, dict) and message_path in r
                for r in data[:3]
            )
            if has_nested_messages:
                results: list[HarvestedConversation] = []
                file_mtime = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                )
                for idx, item in enumerate(data):
                    try:
                        if not isinstance(item, dict):
                            continue
                        raw_messages = self._extract_messages(item, message_path)
                        messages = self._normalise_roles(raw_messages, role_map)
                        messages = self._clean_messages(messages)
                        if not messages or self._is_meta_conversation(messages):
                            continue
                        thinking: list[str] = []
                        if thinking_field:
                            think_val = item.get(thinking_field, "")
                            if think_val:
                                thinking.append(str(think_val))
                        # Derive ID from session_id if available, else content hash
                        sid = item.get("session_id", item.get("id", ""))
                        convo_id = str(uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{platform}:{sid or f'{path}:{idx}'}",
                        ))
                        results.append(
                            HarvestedConversation(
                                id=convo_id,
                                source=f"opencli:{platform}",
                                messages=messages,
                                model=model_name,
                                timestamp=file_mtime,
                                domain=self._detect_domain(messages),
                                thinking_blocks=thinking,
                                metadata={"file": str(path), "platform": platform,
                                          "title": item.get("title", "")},
                            )
                        )
                    except Exception:
                        logger.debug("Skipped malformed record %d in %s", idx, path)
                return results

            # Flat event-per-line format (Codex, etc.)
            messages, thinking = self._collect_jsonl_session(
                data, role_map, thinking_field, payload_key,
            )
            # Strip scaffolding from all messages (Codex, ChatGPT, etc.)
            messages = self._clean_messages(messages)
            if not messages or self._is_meta_conversation(messages):
                return []
            return [
                HarvestedConversation(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{platform}:{path}")),
                    source=f"opencli:{platform}",
                    messages=messages,
                    model=model_name,
                    timestamp=datetime.fromtimestamp(path.stat().st_mtime),
                    domain=self._detect_domain(messages),
                    thinking_blocks=thinking,
                    metadata={"file": str(path), "platform": platform},
                )
            ]

        # Standard JSON export — may contain one or many conversations
        results: list[HarvestedConversation] = []
        for item in data:
            if not isinstance(item, dict):
                continue

            raw_messages = self._extract_messages(item, message_path)
            messages = self._normalise_roles(raw_messages, role_map)
            # Strip scaffolding from all messages
            messages = self._clean_messages(messages)
            if not messages:
                continue
            if self._is_meta_conversation(messages):
                continue

            thinking: list[str] = []
            if thinking_field:
                think_val = item.get(thinking_field, "")
                if think_val:
                    thinking.append(str(think_val))

            results.append(
                HarvestedConversation(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{platform}:{path}:{len(results)}")),
                    source=f"opencli:{platform}",
                    messages=messages,
                    model=model_name,
                    timestamp=datetime.fromtimestamp(path.stat().st_mtime),
                    domain=self._detect_domain(messages),
                    thinking_blocks=thinking,
                    metadata={"file": str(path), "platform": platform},
                )
            )
        return results

    @staticmethod
    def _load_file(path: Path) -> list[dict]:
        """Load a JSON or JSONL file, returning a list of records."""
        records: list[dict] = []
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            if path.suffix == ".jsonl":
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            else:
                try:
                    data = json.load(fh)
                    if isinstance(data, list):
                        records = data
                    elif isinstance(data, dict):
                        records = [data]
                except json.JSONDecodeError:
                    pass
        return records

    @staticmethod
    def _collect_jsonl_session(
        records: list[dict],
        role_map: dict,
        thinking_field: str | None,
        payload_key: str = "",
    ) -> tuple[list[dict], list[str]]:
        """Collect messages from a JSONL session log (e.g. Codex format).

        Codex records have ``type: "response_item"`` with a ``payload`` dict
        containing ``role`` and ``content``.  Pi records have
        ``type: "message"`` with a ``message`` dict containing the same.
        ``payload_key`` overrides the nested key used as the payload object.
        """
        messages: list[dict] = []
        thinking: list[str] = []

        for record in records:
            # Skip non-message records (session_meta, tool calls, etc.)
            rec_type = record.get("type", "")

            # Payload extraction: explicit key > "payload" > top-level
            if payload_key and payload_key in record:
                payload = record[payload_key]
            else:
                payload = record.get("payload", record)
            if not isinstance(payload, dict):
                continue

            role = payload.get("role", "")
            content = payload.get("content", "")

            if not role:
                continue

            # Map role via adapter config
            mapped_role = role_map.get(role, role)
            if mapped_role not in ("user", "assistant", "system"):
                continue

            # Content can be a list of content blocks or a string
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") in ("input_text", "output_text", "text"):
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "thinking" and thinking_field:
                        thinking.append(block.get("thinking", ""))
                content = "\n".join(text_parts)

            if isinstance(content, str) and content.strip():
                # Skip very short system/permission messages
                if mapped_role == "system" and len(content) < 50:
                    continue
                messages.append({"role": mapped_role, "content": content.strip()})

            # Extract thinking from field if present
            if thinking_field and isinstance(payload.get(thinking_field), str):
                thinking.append(payload[thinking_field])

        return messages, thinking

    @staticmethod
    def _extract_messages(obj: dict, message_path: str) -> list[dict]:
        """Walk a dot-separated ``message_path`` to find message objects.

        Supports ``*`` as a wildcard for iterating over dict values or list
        elements.  Falls back to ``obj.get("messages", [])`` when path is
        empty.
        """
        if not message_path:
            msgs = obj.get("messages", [])
            return msgs if isinstance(msgs, list) else []

        parts = message_path.split(".")
        current: Any = obj
        for part in parts:
            if part == "*":
                if isinstance(current, dict):
                    current = list(current.values())
                elif isinstance(current, list):
                    pass  # already iterable
                else:
                    return []
            else:
                if isinstance(current, dict):
                    current = current.get(part, {})
                elif isinstance(current, list):
                    nested: list = []
                    for item in current:
                        if isinstance(item, dict):
                            val = item.get(part)
                            if val is not None:
                                if isinstance(val, list):
                                    nested.extend(val)
                                else:
                                    nested.append(val)
                    current = nested
                else:
                    return []

        if isinstance(current, list):
            return [m for m in current if isinstance(m, dict)]
        return []

    @staticmethod
    def _normalise_roles(messages: list[dict], role_map: dict) -> list[dict]:
        """Apply the adapter's role mapping to standardise role names."""
        normalised: list[dict] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", msg.get("author", {}).get("role", ""))
            content = msg.get("content", msg.get("text", ""))
            if not role or not content:
                continue
            if isinstance(content, list):
                # Some exports store content as a list of parts
                content = " ".join(
                    p.get("text", str(p)) if isinstance(p, dict) else str(p)
                    for p in content
                )
            mapped_role = role_map.get(role, role)
            if mapped_role not in ("user", "assistant", "system"):
                continue
            normalised.append({"role": mapped_role, "content": str(content)})
        return normalised

    # ── Command-based harvesting (placeholder) ─────────────────────

    def _harvest_command(
        self, cfg: dict, since: datetime | None
    ) -> list[HarvestedConversation]:
        """Run a shell command to extract conversations. Placeholder."""
        logger.info(
            "Command-based harvesting for %s is not yet implemented",
            cfg.get("platform", "unknown"),
        )
        return []
