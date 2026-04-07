#!/usr/bin/env python3
"""
Manus API Conversation Exporter

Exports all conversations from your Manus account to JSONL format
for ABLE distillation pipeline ingestion.

Setup:
  1. Go to https://manus.im → Settings → API / Developer
  2. Generate an API key
  3. Run: python scripts/manus-api-export.py --api-key YOUR_KEY
     OR: export MANUS_API_KEY=YOUR_KEY && python scripts/manus-api-export.py

Output goes to: ~/.able/external_sessions/manus_export.jsonl

Then run:
  python -m able.core.distillation.import_history --platform manus
"""

import argparse
import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

API_BASE = "https://api.manus.im"
OUTPUT_DIR = Path.home() / ".able" / "external_sessions"

# Disable SSL verification (macOS Python sometimes lacks certs)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _api_get(path: str, api_key: str, params: dict | None = None) -> dict:
    """Make an authenticated GET request to the Manus API."""
    query = ""
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        query = f"?{query}" if query else ""

    url = f"{API_BASE}{path}{query}"
    req = urllib.request.Request(
        url,
        headers={
            "x-manus-api-key": api_key,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        return json.loads(resp.read())


def list_all_tasks(api_key: str) -> list[dict]:
    """Enumerate all tasks using cursor-based pagination."""
    tasks = []
    cursor = None
    page = 0

    while True:
        page += 1
        params = {"limit": "50"}
        if cursor:
            params["cursor"] = cursor

        logger.info(f"  Fetching task list page {page}...")
        data = _api_get("/v2/task.list", api_key, params)

        items = data.get("data", [])
        if not items:
            break

        tasks.extend(items)
        logger.info(f"  Found {len(items)} tasks (total: {len(tasks)})")

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break

        time.sleep(0.3)  # Rate limit

    return tasks


def get_task_messages(api_key: str, task_id: str) -> list[dict]:
    """Get all messages for a task using cursor-based pagination."""
    messages = []
    cursor = None

    while True:
        params = {"task_id": task_id, "limit": "100"}
        if cursor:
            params["cursor"] = cursor

        data = _api_get("/v2/task.listMessages", api_key, params)
        items = data.get("messages", [])
        if not items:
            break

        messages.extend(items)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break

        time.sleep(0.2)

    return messages


def export_conversations(api_key: str, output_path: Path) -> int:
    """Export all Manus conversations to JSONL."""
    logger.info("Listing all tasks...")
    tasks = list_all_tasks(api_key)

    if not tasks:
        logger.info("No tasks found.")
        return 0

    logger.info(f"\nFound {len(tasks)} tasks. Fetching messages...")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    exported = 0

    with open(output_path, "w") as f:
        for i, task in enumerate(tasks, 1):
            task_id = task.get("id", "")
            title = task.get("title", task.get("name", "Untitled"))
            status = task.get("status", "unknown")

            logger.info(f"  [{i}/{len(tasks)}] {title[:60]} ({status})")

            try:
                messages = get_task_messages(api_key, task_id)
            except urllib.error.HTTPError as e:
                logger.warning(f"    Failed: HTTP {e.code}")
                continue
            except Exception as e:
                logger.warning(f"    Failed: {e}")
                continue

            if not messages:
                continue

            # Convert Manus message format to ABLE harvest format
            # Manus messages have typed wrappers:
            #   type="user_message"      → user_message.content
            #   type="assistant_message"  → assistant_message.content
            #   type="tool_call"          → tool_call.name + tool_call.input
            #   type="tool_result"        → tool_result.content
            #   type="status_update"      → skip (agent lifecycle noise)
            #   type="error_message"      → skip (error noise)
            able_messages = []
            title_candidates = []

            for msg in messages:
                msg_type = msg.get("type", "")

                if msg_type == "user_message":
                    content = msg.get("user_message", {}).get("content", "")
                    if content:
                        able_messages.append({"role": "user", "content": content})
                        if not title_candidates:
                            title_candidates.append(content[:80])

                elif msg_type == "assistant_message":
                    content = msg.get("assistant_message", {}).get("content", "")
                    if content:
                        able_messages.append({"role": "assistant", "content": content})

                elif msg_type == "tool_call":
                    tc = msg.get("tool_call", {})
                    name = tc.get("name", "unknown_tool")
                    inp = tc.get("input", "")
                    able_messages.append({
                        "role": "assistant",
                        "content": f"[Tool: {name}] {inp}" if inp else f"[Tool: {name}]",
                        "tool_call": True,
                    })

                elif msg_type == "tool_result":
                    tr = msg.get("tool_result", {})
                    content = tr.get("content", "")
                    if content:
                        able_messages.append({
                            "role": "tool",
                            "content": content[:2000],  # Cap tool output
                        })
                # Skip: status_update, error_message, plan_update, etc.

            # Use first user message as title if task had none
            if title == "Untitled" and title_candidates:
                title = title_candidates[0]

            if able_messages:
                record = {
                    "source": "manus",
                    "session_id": task_id,
                    "title": title,
                    "status": status,
                    "messages": able_messages,
                    "created_at": task.get("created_at", task.get("createdAt")),
                    "exported_at": __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ).isoformat(),
                }
                f.write(json.dumps(record) + "\n")
                exported += 1

    logger.info(f"\nExported {exported} conversations to {output_path}")
    return exported


def try_local_token() -> str | None:
    """Try to load token from Manus desktop app localStorage."""
    ls_path = Path.home() / "Library" / "Application Support" / "Manus" / "localStorage.json"
    if ls_path.exists():
        try:
            data = json.loads(ls_path.read_text())
            return data.get("token")
        except Exception:
            pass
    return None


def main():
    parser = argparse.ArgumentParser(description="Export Manus conversations for ABLE distillation")
    parser.add_argument(
        "--api-key", type=str, default=None,
        help="Manus API key (or set MANUS_API_KEY env var)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help=f"Output path (default: {OUTPUT_DIR}/manus_export.jsonl)",
    )
    parser.add_argument(
        "--use-local-token", action="store_true",
        help="Use JWT from Manus desktop app (may not work with v2 API)",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("MANUS_API_KEY")

    if not api_key and args.use_local_token:
        api_key = try_local_token()
        if api_key:
            logger.info("Using token from Manus desktop app (may not work with v2 API)")

    if not api_key:
        logger.error(
            "No API key provided.\n\n"
            "To get a Manus API key:\n"
            "  1. Go to https://manus.im\n"
            "  2. Open Settings → API / Developer\n"
            "  3. Generate an API key\n\n"
            "Then run:\n"
            "  python scripts/manus-api-export.py --api-key YOUR_KEY\n"
            "  OR: export MANUS_API_KEY=YOUR_KEY"
        )
        sys.exit(1)

    output = Path(args.output) if args.output else OUTPUT_DIR / "manus_export.jsonl"

    try:
        count = export_conversations(api_key, output)
        if count > 0:
            logger.info(
                f"\nNext step: run the ABLE import pipeline:\n"
                f"  python -m able.core.distillation.import_history --platform manus"
            )
    except urllib.error.HTTPError as e:
        if e.code == 401:
            body = e.read().decode()
            if "signature" in body:
                logger.error(
                    "API key rejected (invalid signature).\n"
                    "The web JWT doesn't work with the v2 API.\n"
                    "You need a proper API key from: Settings → API / Developer"
                )
            else:
                logger.error(f"Authentication failed: {body}")
        else:
            logger.error(f"API error: HTTP {e.code} — {e.read().decode()[:200]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
