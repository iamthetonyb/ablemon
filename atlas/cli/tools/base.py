"""
CLITool base class — Claurst-pattern tool definitions for ATLAS CLI agent.

Each tool declares capability flags (read-only, destructive, concurrent-safe)
and provides pre-execution validation via validate_input().
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
from pathlib import Path


@dataclass
class ToolContext:
    """Shared context passed to all tool executions."""
    work_dir: Path
    safe_mode: bool = True
    session_id: str = ""


@dataclass
class CLITool:
    """
    Base class for all CLI agent tools.

    Capability flags follow the Claurst pattern:
      - is_read_only: tool only reads, never mutates state
      - is_destructive: tool can cause data loss (overwrite, delete)
      - is_concurrent_safe: safe to run in parallel with other tools
    """
    name: str
    description: str
    parameters: Dict[str, Any]
    is_read_only: bool = False
    is_destructive: bool = False
    is_concurrent_safe: bool = True

    def validate_input(self, args: dict, ctx: Optional[ToolContext] = None) -> Optional[str]:
        """Pre-execution validation. Returns error string or None if valid."""
        return None

    async def execute(self, args: dict, ctx: Optional[ToolContext] = None) -> str:
        """Execute the tool. Override in subclasses."""
        raise NotImplementedError

    def to_openai_schema(self) -> dict:
        """Convert to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def _resolve_work_dir(self, ctx: Optional[ToolContext]) -> Path:
        """Get work_dir from context, falling back to cwd."""
        if ctx and ctx.work_dir:
            return ctx.work_dir
        return Path.cwd()

    def _is_within_sandbox(self, path: Path, work_dir: Path) -> bool:
        """Check that path is within the sandbox work_dir."""
        try:
            resolved = path.resolve()
            sandbox = work_dir.resolve()
            return str(resolved).startswith(str(sandbox))
        except (OSError, ValueError):
            return False

    def _resolve_path(self, raw: str, work_dir: Path) -> Path:
        """Resolve a file path argument, making relative paths absolute to work_dir."""
        path = Path(raw)
        if not path.is_absolute():
            path = work_dir / path
        return path


def ensure_atlas_on_path() -> str:
    """Add the atlas package root to sys.path if not already present. Returns the root."""
    import sys
    atlas_root = str(Path(__file__).resolve().parent.parent.parent)
    if atlas_root not in sys.path:
        sys.path.insert(0, atlas_root)
    return atlas_root
