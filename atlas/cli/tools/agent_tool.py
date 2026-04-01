"""
SpawnAgent tool — creates a sub-agent with its own message context.

Depth-limited to 3 nested levels to prevent runaway recursion.
Each subagent inherits the parent's tier, permissions, and work_dir.
"""

import asyncio
from pathlib import Path
from typing import Optional

from .base import CLITool, ToolContext

# Track agent depth per-task to enforce the nesting limit
_MAX_DEPTH = 3


class SpawnAgent(CLITool):
    """Spawn a sub-agent to handle a scoped task with its own context."""

    def __init__(self, **_kw):
        super().__init__(
            name="spawn_agent",
            description=(
                "Spawn a sub-agent to handle a focused task. The sub-agent "
                "gets its own message context but inherits permissions and work_dir. "
                "Max 3 levels of nesting."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Short description of the sub-agent's task.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The full prompt/instructions for the sub-agent.",
                    },
                },
                "required": ["description", "prompt"],
            },
            is_read_only=False,
            is_concurrent_safe=True,
        )
        self._depth = 0

    def validate_input(self, args: dict, ctx: Optional[ToolContext] = None) -> Optional[str]:
        if not args.get("description", "").strip():
            return "description cannot be empty"
        if not args.get("prompt", "").strip():
            return "prompt cannot be empty"
        if self._depth >= _MAX_DEPTH:
            return f"Agent nesting depth limit reached ({_MAX_DEPTH})"
        return None

    async def execute(self, args: dict, ctx: Optional[ToolContext] = None) -> str:
        description = args["description"]
        prompt = args["prompt"]

        # Placeholder: in the real system this would create an ATLASRepl sub-agent
        # with its own message history and tool access, running the prompt to completion.
        # For now, return a structured marker so the orchestrator can handle dispatch.
        return (
            f"[AGENT SPAWNED] depth={self._depth + 1}/{_MAX_DEPTH}\n"
            f"Task: {description}\n"
            f"Prompt length: {len(prompt)} chars\n"
            f"Status: ready for orchestrator dispatch"
        )

    def with_depth(self, depth: int) -> "SpawnAgent":
        """Return a copy of this tool configured at the given nesting depth."""
        clone = SpawnAgent()
        clone._depth = depth
        return clone
