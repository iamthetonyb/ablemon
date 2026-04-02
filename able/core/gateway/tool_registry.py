"""
Tool Registry — Declarative tool registration with dispatch and approval.

Replaces hardcoded ABLE_TOOL_DEFS with a pluggable registry pattern.
Each tool module registers its definitions and handlers at import time.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    """A single registered tool with its definition and handler."""

    name: str
    definition: Dict[str, Any]  # OpenAI function-calling schema
    handler: Callable  # async (args, context) -> str
    display_name: str
    description: str
    requires_approval: bool = False
    risk_level: str = "low"  # low | medium | high
    category: str = "general"
    read_only: bool = True
    concurrent_safe: bool = True
    surface: str = "system"
    artifact_kind: str = "markdown"
    enabled_by_default: bool = True
    tags: List[str] = field(default_factory=list)


@dataclass
class ToolContext:
    """Context passed to tool handlers during dispatch."""

    user_id: str
    client_id: str
    update: Any = None  # Telegram Update object
    msgs: List[Any] = field(default_factory=list)  # Conversation messages
    approval_workflow: Any = None  # ApprovalWorkflow instance
    metadata: Dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """
    Declarative tool registration — OpenClaw-inspired plugin pattern.

    Usage:
        registry = ToolRegistry()
        registry.register_module(github_tools)
        registry.register_module(web_tools)

        # Get all tool definitions for LLM function-calling
        defs = registry.get_definitions()

        # Dispatch a tool call
        result = await registry.dispatch(tool_call, context)
    """

    def __init__(self):
        self._tools: Dict[str, ToolDef] = {}

    def register(
        self,
        name: str,
        definition: Dict[str, Any],
        handler: Callable,
        *,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        requires_approval: bool = False,
        risk_level: str = "low",
        category: str = "general",
        read_only: bool = True,
        concurrent_safe: bool = True,
        surface: str = "system",
        artifact_kind: str = "markdown",
        enabled_by_default: bool = True,
        tags: Optional[List[str]] = None,
    ):
        """Register a single tool."""
        function_meta = definition.get("function", {})
        self._tools[name] = ToolDef(
            name=name,
            definition=definition,
            handler=handler,
            display_name=display_name or function_meta.get("name", name),
            description=description or function_meta.get("description", ""),
            requires_approval=requires_approval,
            risk_level=risk_level,
            category=category,
            read_only=read_only,
            concurrent_safe=concurrent_safe,
            surface=surface,
            artifact_kind=artifact_kind,
            enabled_by_default=enabled_by_default,
            tags=list(tags or []),
        )
        logger.debug(
            "Registered tool: %s (approval=%s, category=%s, read_only=%s)",
            name,
            requires_approval,
            category,
            read_only,
        )

    def register_module(self, module):
        """
        Register all tools from a module.
        Module must have a `register_tools(registry)` function.
        """
        if hasattr(module, "register_tools"):
            module.register_tools(self)
        else:
            logger.warning(f"Module {module.__name__} has no register_tools function")

    def get_definitions(
        self,
        effective_settings: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Get tool definitions for LLM function-calling, filtered by settings."""
        definitions: List[Dict[str, Any]] = []
        for tool in self._tools.values():
            setting = (effective_settings or {}).get(tool.name, {})
            enabled = setting.get("enabled", tool.enabled_by_default)
            if enabled:
                definitions.append(tool.definition)
        return definitions

    def get_tool(self, name: str) -> Optional[ToolDef]:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_catalog(self) -> List[Dict[str, Any]]:
        """Return UI-facing tool metadata sorted for stable rendering."""
        rows: List[Dict[str, Any]] = []
        for tool in self._tools.values():
            function = tool.definition.get("function", {})
            rows.append(
                {
                    "name": tool.name,
                    "display_name": tool.display_name,
                    "description": tool.description,
                    "category": tool.category,
                    "risk_level": tool.risk_level,
                    "requires_approval": tool.requires_approval,
                    "read_only": tool.read_only,
                    "concurrent_safe": tool.concurrent_safe,
                    "surface": tool.surface,
                    "artifact_kind": tool.artifact_kind,
                    "enabled_by_default": tool.enabled_by_default,
                    "tags": tool.tags,
                    "parameters": function.get("parameters", {"type": "object"}),
                }
            )
        return sorted(rows, key=lambda row: (row["category"], row["display_name"]))

    def get_effective_settings(
        self,
        overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Merge registry defaults with persisted overrides."""
        merged: Dict[str, Dict[str, Any]] = {}
        overrides = overrides or {}
        for tool in self._tools.values():
            override = overrides.get(tool.name, {})
            merged[tool.name] = {
                "enabled": override.get("enabled", tool.enabled_by_default),
                "requires_approval": override.get(
                    "requires_approval",
                    tool.requires_approval,
                ),
                "risk_level": override.get("risk_level", tool.risk_level),
                "display_name": tool.display_name,
                "description": tool.description,
                "category": tool.category,
                "read_only": tool.read_only,
                "concurrent_safe": tool.concurrent_safe,
                "surface": tool.surface,
                "artifact_kind": tool.artifact_kind,
                "enabled_by_default": tool.enabled_by_default,
                "tags": tool.tags,
            }
        return merged

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())

    async def dispatch(self, tool_call, context: ToolContext) -> str:
        """
        Dispatch a tool call to its registered handler.
        Handles approval workflow for write tools automatically.
        """
        name = tool_call.name
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}

        logger.info(f"Tool dispatch: {name}({list(args.keys())})")

        # Short-circuit for JSON parse errors
        if "error" in args and "JSONDecodeError" in str(args.get("error", "")):
            return (
                f"⚠️ System Error: The tool parameter JSON was truncated or malformed: "
                f"{args['error']}. If you are trying to output massive code files, "
                f"do not push them all at once. Break them down."
            )

        tool = self._tools.get(name)
        if not tool:
            return f"❓ Unknown tool: {name}"

        try:
            context.metadata.pop("approval_result", None)

            # Handle approval for write tools
            if tool.requires_approval and context.approval_workflow:
                approval = await context.approval_workflow.request_approval(
                    operation=name,
                    details=args,
                    requester_id=context.user_id,
                    risk_level=tool.risk_level,
                    context=self._build_approval_context(name, args),
                )
                if approval.status.value != "approved":
                    return f"❌ Denied ({approval.status.value})"
                context.metadata["approval_result"] = approval

            # Execute the handler
            handler = tool.handler
            params = inspect.signature(handler).parameters
            if len(params) >= 2:
                return await handler(args, context)
            return await handler(**args)

        except Exception as e:
            logger.error(f"Tool call {name} failed: {e}", exc_info=True)
            return f"⚠️ Tool error ({name}): {e}"

    def _build_approval_context(self, name: str, args: Dict) -> str:
        """Build human-readable approval context from tool name and args."""
        contexts = {
            "github_create_repo": lambda a: f"Create {'private' if a.get('private') else 'public'} repo: {a.get('name')}",
            "github_push_files": lambda a: f"Push {len(a.get('files', {}))} files to {a.get('repo')}/{a.get('branch', 'main')}",
            "github_create_pr": lambda a: f"Open PR '{a.get('title')}' in {a.get('repo')}: {a.get('head')} → {a.get('base', 'main')}",
            "github_pages_deploy": lambda a: f"Deploy {len(a.get('files', {}))} files to GitHub Pages",
            "vercel_deploy": lambda a: f"Deploy {len(a.get('files', {}))} files to Vercel '{a.get('project_name')}'",
            "do_create_droplet": lambda a: f"Provision DO droplet: {a.get('name')} ({a.get('region', 'nyc3')}, {a.get('size', 's-1vcpu-1gb')})",
        }
        builder = contexts.get(name)
        if builder:
            try:
                return builder(args)
            except Exception:
                pass
        return f"{name}: {str(args)[:200]}"


def build_default_registry() -> ToolRegistry:
    """Build the canonical runtime registry used by gateway + control plane."""
    registry = ToolRegistry()

    from able.core.gateway.tool_defs import github_tools, infra_tools, tenant_tools, web_tools

    registry.register_module(github_tools)
    registry.register_module(infra_tools)
    registry.register_module(web_tools)
    registry.register_module(tenant_tools)

    try:
        from able.core.gateway.tool_defs import resource_tools

        registry.register_module(resource_tools)
    except Exception as exc:  # pragma: no cover - defensive during partial boots
        logger.warning("Resource tools unavailable: %s", exc)

    return registry
