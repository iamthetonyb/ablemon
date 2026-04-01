"""
Search tools — GlobSearch, GrepSearch.

GlobSearch uses pathlib for file pattern matching.
GrepSearch shells out to rg (ripgrep) with grep fallback.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .base import CLITool, ToolContext


class GlobSearch(CLITool):
    """Find files matching a glob pattern, sorted by modification time."""

    def __init__(self, **_kw):
        super().__init__(
            name="glob_search",
            description=(
                "Find files matching a glob pattern. "
                "Returns file paths sorted by most recently modified."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": 'Glob pattern (e.g. "**/*.py", "src/**/*.ts").',
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in. Defaults to work_dir.",
                    },
                },
                "required": ["pattern"],
            },
            is_read_only=True,
            is_concurrent_safe=True,
        )

    def validate_input(self, args: dict, ctx: Optional[ToolContext] = None) -> Optional[str]:
        pattern = args.get("pattern", "").strip()
        if not pattern:
            return "Pattern cannot be empty"
        return None

    async def execute(self, args: dict, ctx: Optional[ToolContext] = None) -> str:
        work_dir = self._resolve_work_dir(ctx)
        pattern = args["pattern"]
        search_path = Path(args["path"]) if args.get("path") else work_dir

        if not search_path.is_dir():
            return f"Directory not found: {search_path}"

        try:
            matches = list(search_path.glob(pattern))
        except (OSError, ValueError) as e:
            return f"Glob error: {e}"

        # Filter to files only and sort by mtime descending
        files = [m for m in matches if m.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        if not files:
            return f"No files matching '{pattern}' in {search_path}"

        return "\n".join(str(f) for f in files[:500])


class GrepSearch(CLITool):
    """Search file contents for a regex pattern using rg or grep."""

    def __init__(self, **_kw):
        super().__init__(
            name="grep_search",
            description=(
                "Search file contents for a regex pattern. Uses ripgrep (rg) "
                "if available, falling back to grep. Returns matches with line numbers."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search. Defaults to work_dir.",
                    },
                    "glob_filter": {
                        "type": "string",
                        "description": 'File glob to filter (e.g. "*.py").',
                    },
                },
                "required": ["pattern"],
            },
            is_read_only=True,
            is_concurrent_safe=True,
        )

    def validate_input(self, args: dict, ctx: Optional[ToolContext] = None) -> Optional[str]:
        pattern = args.get("pattern", "").strip()
        if not pattern:
            return "Pattern cannot be empty"
        return None

    async def execute(self, args: dict, ctx: Optional[ToolContext] = None) -> str:
        work_dir = self._resolve_work_dir(ctx)
        pattern = args["pattern"]
        search_path = str(args.get("path") or work_dir)
        glob_filter = args.get("glob_filter")

        # Prefer ripgrep, fall back to grep
        rg_path = shutil.which("rg")
        if rg_path:
            cmd = [rg_path, "-n", "--no-heading", "--color=never"]
            if glob_filter:
                cmd.extend(["--glob", glob_filter])
            cmd.extend(["--", pattern, search_path])
        else:
            cmd = ["grep", "-rn", "--color=never"]
            if glob_filter:
                cmd.extend(["--include", glob_filter])
            cmd.extend(["--", pattern, search_path])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            output = proc.stdout[:50000]
            if not output and proc.returncode == 1:
                return f"No matches for pattern '{pattern}' in {search_path}"
            if proc.returncode > 1:
                return f"Search error: {proc.stderr[:500]}"

            return output.rstrip()

        except FileNotFoundError:
            return "Neither rg nor grep found on PATH"
        except subprocess.TimeoutExpired:
            return "Search timed out after 30s"
