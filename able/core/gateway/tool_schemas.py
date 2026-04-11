"""
D17 — Semantic Tool Abstraction Layer.

Provides rich semantic descriptions for ABLE's tools beyond basic
name/description pairs. Includes capability categories, I/O examples,
common error patterns, and recovery suggestions.

Forked from InsForge's "backend context engineering" pattern.

Usage:
    registry = ToolSchemaRegistry()
    registry.register(ToolSchema(
        name="read_file",
        description="Read a file from the filesystem",
        semantic_category="filesystem",
        examples=[IOExample(input={"path": "auth.py"}, output="file contents...")],
    ))

    context = registry.generate_tool_context(["read_file", "write_file"])
    # Inject into system prompt for grounded tool usage
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class IOExample:
    """An input/output example for a tool."""
    input: Dict[str, Any]
    output: str
    description: str = ""


@dataclass
class ErrorPattern:
    """A common error pattern for a tool."""
    error_type: str  # "permission", "not_found", "timeout", etc.
    pattern: str     # Regex or substring to match
    description: str
    recovery: str    # What to do when this error occurs


@dataclass
class ToolSchema:
    """Rich semantic description of a tool.

    Goes beyond basic name/description to include categories,
    examples, error patterns, and recovery hints.
    """
    name: str
    description: str
    semantic_category: str  # "filesystem", "network", "shell", "memory", etc.
    examples: List[IOExample] = field(default_factory=list)
    error_patterns: List[ErrorPattern] = field(default_factory=list)
    recovery_hints: List[str] = field(default_factory=list)
    input_schema: Optional[Dict[str, Any]] = None
    output_format: str = "text"  # "text", "json", "binary"
    side_effects: bool = False   # Does this tool modify state?
    requires_approval: bool = False
    max_output_tokens: int = 4000
    tags: List[str] = field(default_factory=list)


# ── Built-in schemas ─────────────────────────────────────────────

BUILTIN_SCHEMAS = [
    ToolSchema(
        name="read_file",
        description="Read file contents from the filesystem",
        semantic_category="filesystem",
        examples=[
            IOExample(
                input={"path": "src/auth.py"},
                output="<file contents>",
                description="Read a Python source file",
            ),
        ],
        error_patterns=[
            ErrorPattern(
                error_type="not_found",
                pattern="FileNotFoundError",
                description="File does not exist",
                recovery="Check the path with list_directory first",
            ),
            ErrorPattern(
                error_type="permission",
                pattern="PermissionError",
                description="Insufficient permissions",
                recovery="Check file permissions or try a different path",
            ),
        ],
        recovery_hints=["If file not found, search for similar names"],
        output_format="text",
        side_effects=False,
    ),
    ToolSchema(
        name="write_file",
        description="Write content to a file on the filesystem",
        semantic_category="filesystem",
        examples=[
            IOExample(
                input={"path": "output.txt", "content": "hello world"},
                output="File written successfully",
                description="Create a simple text file",
            ),
        ],
        error_patterns=[
            ErrorPattern(
                error_type="permission",
                pattern="PermissionError",
                description="Cannot write to this location",
                recovery="Check directory permissions or use a different path",
            ),
        ],
        side_effects=True,
        requires_approval=True,
    ),
    ToolSchema(
        name="shell",
        description="Execute a shell command",
        semantic_category="shell",
        examples=[
            IOExample(
                input={"command": "git status"},
                output="<git status output>",
                description="Check git repository status",
            ),
        ],
        error_patterns=[
            ErrorPattern(
                error_type="timeout",
                pattern="TimeoutError",
                description="Command took too long",
                recovery="Add a timeout flag or break into smaller commands",
            ),
            ErrorPattern(
                error_type="not_found",
                pattern="command not found",
                description="Binary not installed",
                recovery="Check if the tool is installed with 'which <command>'",
            ),
        ],
        side_effects=True,
        requires_approval=True,
    ),
    ToolSchema(
        name="web_search",
        description="Search the web for information",
        semantic_category="network",
        examples=[
            IOExample(
                input={"query": "Python asyncio tutorial"},
                output="<search results>",
                description="Search for documentation",
            ),
        ],
        error_patterns=[
            ErrorPattern(
                error_type="empty",
                pattern="No results",
                description="Search returned no results",
                recovery="Reformulate the query with different keywords",
            ),
        ],
        recovery_hints=["Try broader terms if no results", "Use quotes for exact phrases"],
        output_format="text",
        side_effects=False,
    ),
    ToolSchema(
        name="memory_search",
        description="Search persistent memory for past information",
        semantic_category="memory",
        examples=[
            IOExample(
                input={"query": "user preferences"},
                output="<matching memories>",
                description="Find stored user preferences",
            ),
        ],
        output_format="json",
        side_effects=False,
    ),
]


# ── Registry ─────────────────────────────────────────────────────


class ToolSchemaRegistry:
    """Registry of semantic tool schemas.

    Provides rich context about available tools for system prompt
    injection, reducing hallucinated tool calls by grounding the
    model in actual tool capabilities.
    """

    def __init__(self, load_builtins: bool = True):
        self._schemas: Dict[str, ToolSchema] = {}
        if load_builtins:
            for schema in BUILTIN_SCHEMAS:
                self._schemas[schema.name] = schema

    def register(self, schema: ToolSchema) -> None:
        """Register a tool schema."""
        self._schemas[schema.name] = schema

    def get(self, name: str) -> Optional[ToolSchema]:
        """Get schema by tool name."""
        return self._schemas.get(name)

    def by_category(self, category: str) -> List[ToolSchema]:
        """Get all schemas in a category."""
        return [s for s in self._schemas.values() if s.semantic_category == category]

    def categories(self) -> List[str]:
        """List all unique categories."""
        return sorted(set(s.semantic_category for s in self._schemas.values()))

    def generate_tool_context(
        self,
        tool_names: Optional[List[str]] = None,
        max_tokens: int = 2000,
    ) -> str:
        """Generate a token-efficient capability map for system prompt injection.

        Args:
            tool_names: Specific tools to include. None = all.
            max_tokens: Approximate token budget for the context.

        Returns:
            Formatted string describing available tools.
        """
        schemas = []
        if tool_names:
            schemas = [self._schemas[n] for n in tool_names if n in self._schemas]
        else:
            schemas = list(self._schemas.values())

        if not schemas:
            return ""

        parts = ["Available tools:"]
        chars_budget = max_tokens * 4  # ~4 chars per token
        chars_used = len(parts[0])

        for schema in schemas:
            entry = self._format_schema_entry(schema)
            if chars_used + len(entry) > chars_budget:
                parts.append("... (additional tools available)")
                break
            parts.append(entry)
            chars_used += len(entry)

        return "\n".join(parts)

    def _format_schema_entry(self, schema: ToolSchema) -> str:
        """Format a single schema as a concise tool description."""
        lines = [f"\n[{schema.name}] ({schema.semantic_category}) — {schema.description}"]

        if schema.side_effects:
            lines[0] += " [MODIFIES STATE]"

        if schema.examples:
            ex = schema.examples[0]
            lines.append(f"  Example: {ex.input} → {ex.output[:60]}")

        if schema.error_patterns:
            recoveries = [ep.recovery for ep in schema.error_patterns[:2]]
            lines.append(f"  On error: {'; '.join(recoveries)}")

        if schema.recovery_hints:
            lines.append(f"  Hints: {'; '.join(schema.recovery_hints[:2])}")

        return "\n".join(lines)

    def match_error(self, tool_name: str, error_msg: str) -> Optional[ErrorPattern]:
        """Find a matching error pattern for a tool's error.

        Returns the first matching ErrorPattern, or None.
        """
        schema = self._schemas.get(tool_name)
        if not schema:
            return None

        for ep in schema.error_patterns:
            if ep.pattern.lower() in error_msg.lower():
                return ep
        return None

    def stats(self) -> Dict[str, Any]:
        """Return registry stats."""
        return {
            "total_schemas": len(self._schemas),
            "categories": self.categories(),
            "with_examples": sum(1 for s in self._schemas.values() if s.examples),
            "with_error_patterns": sum(1 for s in self._schemas.values() if s.error_patterns),
            "side_effect_tools": sum(1 for s in self._schemas.values() if s.side_effects),
        }
