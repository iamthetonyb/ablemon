"""
Tool Registry — Declarative tool registration with dispatch and approval.

Replaces hardcoded ATLAS_TOOL_DEFS with a pluggable registry pattern.
Each tool module registers its definitions and handlers at import time.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    """A single registered tool with its definition and handler."""
    name: str
    definition: Dict[str, Any]       # OpenAI function-calling schema
    handler: Callable                 # async (args, context) -> str
    requires_approval: bool = False
    risk_level: str = "low"          # low | medium | high
    category: str = "general"        # github | infra | web | general


@dataclass
class ToolContext:
    """Context passed to tool handlers during dispatch."""
    user_id: str
    client_id: str
    update: Any = None               # Telegram Update object
    msgs: List[Any] = field(default_factory=list)  # Conversation messages
    approval_workflow: Any = None     # ApprovalWorkflow instance
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
        requires_approval: bool = False,
        risk_level: str = "low",
        category: str = "general",
    ):
        """Register a single tool."""
        self._tools[name] = ToolDef(
            name=name,
            definition=definition,
            handler=handler,
            requires_approval=requires_approval,
            risk_level=risk_level,
            category=category,
        )
        logger.debug(f"Registered tool: {name} (approval={requires_approval}, category={category})")

    def register_module(self, module):
        """
        Register all tools from a module.
        Module must have a `register_tools(registry)` function.
        """
        if hasattr(module, "register_tools"):
            module.register_tools(self)
        else:
            logger.warning(f"Module {module.__name__} has no register_tools function")

    def get_definitions(self) -> List[Dict[str, Any]]:
        """Get all tool definitions for LLM function-calling."""
        return [t.definition for t in self._tools.values()]

    def get_tool(self, name: str) -> Optional[ToolDef]:
        """Get a tool by name."""
        return self._tools.get(name)

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

            # Execute the handler
            return await tool.handler(args, context)

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
