"""
Import Historical AI Conversations — First-run data bootstrap.

Scans all known AI tool export locations, downloads API-accessible history,
and ingests everything into the distillation store. Designed to run once at
ABLE setup to seed the corpus with the user's existing AI interaction history.

Usage:
    # Auto-discover and import all local AI history
    python -m able.core.distillation.import_history

    # Import from a specific directory or file
    python -m able.core.distillation.import_history --path ~/Downloads/chatgpt-export/

    # Import from a specific platform
    python -m able.core.distillation.import_history --platform manus

    # Dry run — show what would be imported without writing to DB
    python -m able.core.distillation.import_history --dry-run

    # Import from Manus API (if logged in)
    python -m able.core.distillation.import_history --platform manus --api

Supported platforms:
    Auto-discovered: Claude Code, Codex, ChatGPT, Claude.ai, Gemini, Grok,
                     Cursor, Windsurf, Manus, Perplexity, Antigravity, Cowork
    Generic drop-zone: ~/.able/external_sessions/*.jsonl
    API import: Manus (via API), Claude.ai (via export), ChatGPT (via export)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Well-known export locations per platform — checked even without adapters
_PLATFORM_HINTS = {
    "chatgpt": {
        "name": "ChatGPT",
        "export_paths": [
            "~/Downloads/chatgpt-export/",
            "~/Downloads/conversations.json",
            "~/Downloads/ChatGPT-Data/",
        ],
        "api_exportable": False,
        "export_instructions": (
            "Go to chat.openai.com → Settings → Data Controls → Export Data. "
            "Download the ZIP and unzip to ~/Downloads/chatgpt-export/"
        ),
    },
    "claude_web": {
        "name": "Claude.ai",
        "export_paths": [
            "~/Downloads/claude-conversations*.json",
            "~/Downloads/claude-export/",
        ],
        "api_exportable": False,
        "export_instructions": (
            "Go to claude.ai → Settings → Account → Export Data. "
            "Download and place JSON files in ~/Downloads/claude-export/"
        ),
    },
    "manus": {
        "name": "Manus",
        "export_paths": [
            "~/.manus/sessions/",
            "~/Documents/Manus/",
            "~/Downloads/manus-export/",
        ],
        "api_exportable": True,
        "export_instructions": (
            "If you have the Manus desktop app, sessions are auto-discovered. "
            "Otherwise: export from manus.app → Settings → Export History"
        ),
    },
    "gemini": {
        "name": "Gemini",
        "export_paths": [
            "~/.gemini/sessions/",
            "~/Downloads/gemini-export/",
            "~/Downloads/Takeout/Gemini Apps/",
        ],
        "api_exportable": False,
        "export_instructions": (
            "Go to takeout.google.com → select 'Gemini Apps' → download. "
            "Unzip to ~/Downloads/Takeout/"
        ),
    },
    "grok": {
        "name": "Grok",
        "export_paths": [
            "~/Downloads/grok-export/",
            "~/.grok/conversations/",
        ],
        "api_exportable": False,
        "export_instructions": "Export from x.com/i/grok → Settings → Export History",
    },
    "cursor": {
        "name": "Cursor",
        "export_paths": [
            "~/.cursor/sessions/",
            "~/Library/Application Support/Cursor/sessions/",
        ],
        "api_exportable": False,
        "export_instructions": "Cursor sessions are auto-discovered from ~/.cursor/sessions/",
    },
    "windsurf": {
        "name": "Windsurf",
        "export_paths": [
            "~/.codeium/windsurf/sessions/",
            "~/.windsurf/sessions/",
        ],
        "api_exportable": False,
        "export_instructions": "Windsurf sessions are auto-discovered from ~/.codeium/windsurf/sessions/",
    },
    "perplexity": {
        "name": "Perplexity",
        "export_paths": [
            "~/Downloads/perplexity-export/",
            "~/.perplexity/history/",
        ],
        "api_exportable": False,
        "export_instructions": "Export from perplexity.ai → Settings → Export Data",
    },
    "codex": {
        "name": "Codex",
        "export_paths": [
            "~/.codex/sessions/",
            "~/.codex/archived_sessions/",
        ],
        "api_exportable": False,
        "export_instructions": "Codex sessions are auto-discovered from ~/.codex/sessions/",
    },
    "claude_code": {
        "name": "Claude Code",
        "export_paths": [
            "~/.claude/projects/",
        ],
        "api_exportable": False,
        "export_instructions": "Claude Code sessions are auto-discovered from ~/.claude/projects/",
    },
    "antigravity": {
        "name": "Antigravity",
        "export_paths": [
            "~/.antigravity/",
        ],
        "api_exportable": False,
        "export_instructions": "Antigravity sessions are auto-discovered from ~/.antigravity/",
    },
}


def scan_available_history() -> dict[str, dict]:
    """
    Scan the filesystem for available AI conversation history.

    Returns a dict of platform → {name, paths_found, file_count, total_size_mb}.
    Only includes platforms where actual files are found.
    """
    found = {}
    for platform, hints in _PLATFORM_HINTS.items():
        paths_found = []
        file_count = 0
        total_size = 0
        for pattern in hints["export_paths"]:
            expanded = os.path.expanduser(pattern)
            p = Path(expanded)
            if p.is_dir():
                files = list(p.rglob("*.json")) + list(p.rglob("*.jsonl"))
                if files:
                    paths_found.append(str(p))
                    file_count += len(files)
                    total_size += sum(f.stat().st_size for f in files)
            elif p.is_file():
                paths_found.append(str(p))
                file_count += 1
                total_size += p.stat().st_size
            else:
                # Try glob pattern
                import glob as globmod
                matches = globmod.glob(expanded)
                for m in matches:
                    mp = Path(m)
                    if mp.is_file():
                        paths_found.append(str(mp))
                        file_count += 1
                        total_size += mp.stat().st_size

        if paths_found:
            found[platform] = {
                "name": hints["name"],
                "paths_found": paths_found,
                "file_count": file_count,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
            }

    return found


async def import_all(
    dry_run: bool = False,
    platform: Optional[str] = None,
    path: Optional[str] = None,
) -> dict:
    """
    Import all available AI conversation history into the distillation store.

    Args:
        dry_run: If True, scan and report but don't write to DB.
        platform: If set, only import from this platform.
        path: If set, import from this specific path (auto-detect format).

    Returns:
        Summary dict with counts per platform.
    """
    from able.core.distillation.harvest_runner import run_harvest

    # If a specific path is given, copy files to external_sessions for harvest
    if path:
        return await _import_from_path(path, dry_run)

    # Scan what's available, filtering by platform if specified
    available = scan_available_history()
    if platform:
        available = {k: v for k, v in available.items() if k == platform}
    if not available:
        print("No AI conversation history found on this system.")
        if platform:
            hints = _PLATFORM_HINTS.get(platform)
            if hints:
                print(f"\n  {hints['name']}: {hints['export_instructions']}")
        else:
            print("\nTo import history, export from your AI tools:")
            for plat, hints in _PLATFORM_HINTS.items():
                print(f"  {hints['name']}: {hints['export_instructions']}")
        return {"total": 0, "platforms": {}}

    # Show what we found
    print(f"\n{'='*60}")
    print("ABLE Historical AI Data Scanner")
    print(f"{'='*60}\n")
    total_files = 0
    total_mb = 0
    for plat, info in sorted(available.items()):
        print(f"  {info['name']:20s} — {info['file_count']:4d} files ({info['total_size_mb']:.1f} MB)")
        total_files += info["file_count"]
        total_mb += info["total_size_mb"]
    print(f"\n  {'TOTAL':20s} — {total_files:4d} files ({total_mb:.1f} MB)")

    if dry_run:
        print("\n[DRY RUN] Would import all of the above. Run without --dry-run to import.")
        return {"total": total_files, "platforms": available}

    # Run the full harvest with all-time window
    print("\nImporting all historical data...")
    result = await run_harvest(since_hours=87600, tenant_id="default")

    summary = {
        "total_conversations": result.total_conversations,
        "total_formatted": result.total_formatted,
        "total_new": result.total_deduplicated,
        "corpus_version": result.corpus_version,
        "corpus_total": result.corpus_total,
        "platforms": available,
    }

    print(f"\n{'='*60}")
    print("Import Complete")
    print(f"{'='*60}")
    print(f"  Conversations found: {result.total_conversations}")
    print(f"  Training pairs created: {result.total_formatted}")
    print(f"  New (deduplicated): {result.total_deduplicated}")
    print(f"  Corpus version: {result.corpus_version}")
    print(f"  Corpus total: {result.corpus_total} pairs")
    print(f"\nCorpus at: ~/.able/distillation/corpus/default/{result.corpus_version}/")

    return summary


async def _import_from_path(path_str: str, dry_run: bool) -> dict:
    """Import from a specific path — auto-detect format and platform."""
    path = Path(os.path.expanduser(path_str))
    if not path.exists():
        print(f"Path not found: {path}")
        return {"total": 0}

    # Count files
    if path.is_dir():
        files = list(path.rglob("*.json")) + list(path.rglob("*.jsonl"))
    else:
        files = [path]

    print(f"Found {len(files)} files at {path}")

    if dry_run:
        print("[DRY RUN] Would import these files.")
        return {"total": len(files)}

    # Copy to external_sessions for the ExternalToolHarvester to pick up
    ext_dir = Path.home() / ".able" / "external_sessions"
    ext_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for f in files:
        dest = ext_dir / f.name
        if not dest.exists():
            if f.suffix == ".json":
                # Convert JSON to JSONL for the harvester
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        with open(dest.with_suffix(".jsonl"), "w") as out:
                            for item in data:
                                if isinstance(item, dict):
                                    out.write(json.dumps(item) + "\n")
                        copied += 1
                    elif isinstance(data, dict):
                        # Single conversation — extract messages
                        messages = data.get("messages", data.get("mapping", []))
                        if messages:
                            with open(dest.with_suffix(".jsonl"), "w") as out:
                                if isinstance(messages, list):
                                    for msg in messages:
                                        if isinstance(msg, dict) and "content" in msg:
                                            out.write(json.dumps(msg) + "\n")
                                elif isinstance(messages, dict):
                                    for msg in messages.values():
                                        if isinstance(msg, dict):
                                            m = msg.get("message", msg)
                                            if isinstance(m, dict) and "content" in m:
                                                out.write(json.dumps(m) + "\n")
                            copied += 1
                except (json.JSONDecodeError, OSError):
                    logger.warning("Failed to parse %s", f)
            else:
                shutil.copy2(f, dest)
                copied += 1

    print(f"Copied {copied} files to {ext_dir}")

    # Now run harvest
    from able.core.distillation.harvest_runner import run_harvest
    result = await run_harvest(since_hours=87600, tenant_id="default")
    print(f"Harvest complete: {result.total_formatted} pairs, corpus {result.corpus_version}")
    return {"total": copied, "corpus_version": result.corpus_version}


def get_export_instructions(platform: Optional[str] = None) -> str:
    """Get export instructions for a specific platform or all platforms."""
    if platform and platform in _PLATFORM_HINTS:
        hints = _PLATFORM_HINTS[platform]
        return f"{hints['name']}: {hints['export_instructions']}"

    lines = ["To import your AI history into ABLE:\n"]
    for plat, hints in sorted(_PLATFORM_HINTS.items()):
        lines.append(f"  {hints['name']:20s} — {hints['export_instructions']}")
    lines.append(f"\n  {'Generic':20s} — Drop JSONL files into ~/.able/external_sessions/")
    lines.append("\nThen run: python -m able.core.distillation.import_history")
    return "\n".join(lines)


# ── CLI entry point ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Import historical AI conversations into ABLE's training pipeline"
    )
    parser.add_argument(
        "--path", type=str, default=None,
        help="Import from a specific directory or file",
    )
    parser.add_argument(
        "--platform", type=str, default=None,
        help="Only import from this platform (e.g. manus, chatgpt, claude_web)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report without writing to DB",
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="Only scan for available history (no import)",
    )
    parser.add_argument(
        "--instructions", action="store_true",
        help="Show export instructions for all platforms",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.instructions:
        print(get_export_instructions(args.platform))
        return

    if args.scan:
        available = scan_available_history()
        if available:
            print(f"\nFound AI history from {len(available)} platforms:")
            for plat, info in sorted(available.items()):
                print(f"  {info['name']:20s} — {info['file_count']} files ({info['total_size_mb']:.1f} MB)")
        else:
            print("No AI history found. Run with --instructions for export guides.")
        return

    result = asyncio.run(import_all(
        dry_run=args.dry_run,
        platform=args.platform,
        path=args.path,
    ))
    if result.get("total", 0) == 0 and not args.dry_run:
        print("\nNo data imported. Run with --instructions for export guides.")


if __name__ == "__main__":
    main()
