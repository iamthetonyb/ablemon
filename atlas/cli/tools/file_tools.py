"""
File tools — ReadFile, WriteFile, EditFile.

Claude Code-equivalent file operations with sandbox enforcement.
All paths must resolve within the ToolContext work_dir.
"""

from pathlib import Path
from typing import Optional

from .base import CLITool, ToolContext


class ReadFile(CLITool):
    """Read a file with optional offset/limit, returning cat -n style output."""

    def __init__(self, **_kw):
        super().__init__(
            name="read_file",
            description=(
                "Read a file from the filesystem. Returns content with line numbers. "
                "Use offset/limit to read specific ranges of large files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to read.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-based).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read.",
                    },
                },
                "required": ["file_path"],
            },
            is_read_only=True,
            is_concurrent_safe=True,
        )

    def validate_input(self, args: dict, ctx: Optional[ToolContext] = None) -> Optional[str]:
        work_dir = self._resolve_work_dir(ctx)
        path = Path(args.get("file_path", ""))

        if not path.is_absolute():
            path = work_dir / path

        if not self._is_within_sandbox(path, work_dir):
            return f"Path {path} is outside the sandbox ({work_dir})"

        if not path.exists():
            return f"File not found: {path}"

        if path.is_dir():
            return f"Path is a directory, not a file: {path}"

        return None

    async def execute(self, args: dict, ctx: Optional[ToolContext] = None) -> str:
        work_dir = self._resolve_work_dir(ctx)
        path = Path(args["file_path"])
        if not path.is_absolute():
            path = work_dir / path

        offset = args.get("offset", 1)
        limit = args.get("limit", 2000)

        # Handle binary files
        try:
            raw = path.read_bytes()
        except OSError as e:
            return f"Error reading file: {e}"

        if b"\x00" in raw[:8192]:
            return f"Binary file, {len(raw)} bytes"

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return f"Binary file, {len(raw)} bytes"

        lines = text.splitlines(keepends=True)
        start = max(0, offset - 1)
        end = start + limit
        selected = lines[start:end]

        # Format with line numbers (cat -n style)
        out_lines = []
        for i, line in enumerate(selected, start=start + 1):
            out_lines.append(f"{i:>6}\t{line.rstrip()}")

        return "\n".join(out_lines)


class WriteFile(CLITool):
    """Write content to a file, creating parent directories as needed."""

    def __init__(self, **_kw):
        super().__init__(
            name="write_file",
            description=(
                "Write content to a file. Creates parent directories if needed. "
                "Overwrites existing files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file.",
                    },
                },
                "required": ["file_path", "content"],
            },
            is_destructive=True,
            is_concurrent_safe=False,
        )

    def validate_input(self, args: dict, ctx: Optional[ToolContext] = None) -> Optional[str]:
        work_dir = self._resolve_work_dir(ctx)
        path = Path(args.get("file_path", ""))

        if not path.is_absolute():
            path = work_dir / path

        if not self._is_within_sandbox(path, work_dir):
            return f"Path {path} is outside the sandbox ({work_dir})"

        if path.exists() and path.is_dir():
            return f"Path is a directory: {path}"

        if path.exists():
            return f"WARNING: File exists and will be overwritten: {path}"

        return None

    async def execute(self, args: dict, ctx: Optional[ToolContext] = None) -> str:
        work_dir = self._resolve_work_dir(ctx)
        path = Path(args["file_path"])
        if not path.is_absolute():
            path = work_dir / path

        content = args["content"]

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} bytes to {path}"
        except OSError as e:
            return f"Error writing file: {e}"


class EditFile(CLITool):
    """Find-and-replace edit: locate old_string and replace with new_string."""

    def __init__(self, **_kw):
        super().__init__(
            name="edit_file",
            description=(
                "Edit a file by replacing old_string with new_string. "
                "old_string must appear exactly once in the file for safety."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to edit.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact text to find and replace.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement text.",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
            is_destructive=False,
            is_concurrent_safe=False,
        )

    def validate_input(self, args: dict, ctx: Optional[ToolContext] = None) -> Optional[str]:
        work_dir = self._resolve_work_dir(ctx)
        path = Path(args.get("file_path", ""))

        if not path.is_absolute():
            path = work_dir / path

        if not self._is_within_sandbox(path, work_dir):
            return f"Path {path} is outside the sandbox ({work_dir})"

        if not path.exists():
            return f"File not found: {path}"

        if not path.is_file():
            return f"Path is not a file: {path}"

        old_string = args.get("old_string", "")
        if not old_string:
            return "old_string cannot be empty"

        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return f"Cannot read file: {e}"

        count = content.count(old_string)
        if count == 0:
            return "old_string not found in file"
        if count > 1:
            return f"old_string appears {count} times — must be unique. Add more context."

        return None

    async def execute(self, args: dict, ctx: Optional[ToolContext] = None) -> str:
        work_dir = self._resolve_work_dir(ctx)
        path = Path(args["file_path"])
        if not path.is_absolute():
            path = work_dir / path

        old_string = args["old_string"]
        new_string = args["new_string"]

        try:
            content = path.read_text(encoding="utf-8")
            new_content = content.replace(old_string, new_string, 1)
            path.write_text(new_content, encoding="utf-8")

            # Report a compact diff summary
            old_lines = old_string.count("\n") + 1
            new_lines = new_string.count("\n") + 1
            return (
                f"Replaced {old_lines} line(s) with {new_lines} line(s) in {path}"
            )
        except OSError as e:
            return f"Error editing file: {e}"
