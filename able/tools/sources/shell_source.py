"""
Shell source adapter -- wraps SecureShell + CommandGuard as a ToolSourceManager.

Namespace convention: shell:{command_name}

Tools are derived from the CommandGuard allowlists (ALLOWED_COMMANDS +
APPROVAL_REQUIRED) and, when available, the YAML ``tool_permissions.yaml``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..registry import ToolCategory, ToolDefinition, ToolResult, ToolSourceManager

logger = logging.getLogger(__name__)

# Lazy imports -- underlying modules may not be on sys.path yet
_SecureShell = None
_CommandGuard = None
_ALLOWED_COMMANDS = None
_APPROVAL_REQUIRED = None


def _ensure_imports():
    global _SecureShell, _CommandGuard, _ALLOWED_COMMANDS, _APPROVAL_REQUIRED
    if _SecureShell is not None:
        return
    try:
        from able.tools.shell.secure_shell import SecureShell
        from able.core.security.command_guard import (
            ALLOWED_COMMANDS,
            APPROVAL_REQUIRED,
            CommandGuard,
        )
        _SecureShell = SecureShell
        _CommandGuard = CommandGuard
        _ALLOWED_COMMANDS = ALLOWED_COMMANDS
        _APPROVAL_REQUIRED = APPROVAL_REQUIRED
    except ImportError:
        logger.debug("SecureShell / CommandGuard not importable -- shell source unavailable")


def _yaml_permission_lists() -> Dict[str, List[str]]:
    """Load tool_permissions.yaml if it exists, returning the three lists."""
    yaml_path = Path(__file__).parent.parent.parent.parent / "config" / "tool_permissions.yaml"
    if not yaml_path.exists():
        return {}
    try:
        import yaml
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class ShellSource:
    """
    ToolSourceManager adapter for SecureShell.

    The tool catalog is derived from three sources (in priority order):
    1. ``config/tool_permissions.yaml``  (always_allow + ask_before)
    2. ``command_guard.ALLOWED_COMMANDS``
    3. ``command_guard.APPROVAL_REQUIRED``

    Each command is exposed as ``shell:{command_name}``.
    """

    def __init__(self, shell: Optional[Any] = None) -> None:
        """
        Args:
            shell: An existing ``SecureShell`` instance.  If ``None`` a
                   default one will be created on first use.
        """
        _ensure_imports()
        self._shell = shell
        self._tools_cache: List[ToolDefinition] = []

    # -- Protocol properties -----------------------------------------------

    @property
    def name(self) -> str:
        return "shell"

    @property
    def category(self) -> ToolCategory:
        return ToolCategory.SHELL

    # -- Lifecycle ----------------------------------------------------------

    def set_shell(self, shell: Any) -> None:
        """Inject or replace the underlying SecureShell."""
        self._shell = shell

    def _get_shell(self) -> Optional[Any]:
        """Lazy-initialise a SecureShell if none was injected."""
        if self._shell is not None:
            return self._shell
        if _SecureShell is None:
            return None
        try:
            self._shell = _SecureShell()
            return self._shell
        except Exception:
            logger.exception("Failed to create default SecureShell")
            return None

    def is_available(self) -> bool:
        _ensure_imports()
        return _SecureShell is not None

    # -- Tool discovery -----------------------------------------------------

    async def list_tools(self) -> List[ToolDefinition]:
        if self._tools_cache:
            return list(self._tools_cache)
        await self.refresh()
        return list(self._tools_cache)

    async def refresh(self) -> int:
        """Build the tool catalog from allowlist + YAML permissions."""
        _ensure_imports()
        if _ALLOWED_COMMANDS is None:
            self._tools_cache = []
            return 0

        definitions: List[ToolDefinition] = []
        seen: set[str] = set()

        # 1. YAML permissions (higher priority)
        yaml_perms = _yaml_permission_lists()
        for cmd in yaml_perms.get("always_allow", []):
            qname = f"shell:{cmd}"
            if qname in seen:
                continue
            seen.add(qname)
            definitions.append(
                ToolDefinition(
                    name=qname,
                    display_name=cmd,
                    description=f"Shell command: {cmd} (always allowed)",
                    category=ToolCategory.SHELL,
                    source=self.name,
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": f"Full command string starting with '{cmd}'",
                            }
                        },
                        "required": ["command"],
                    },
                    requires_approval=False,
                    trust_level=2,
                    tags=["shell", "always_allow"],
                )
            )

        for cmd in yaml_perms.get("ask_before", []):
            qname = f"shell:{cmd}"
            if qname in seen:
                continue
            seen.add(qname)
            definitions.append(
                ToolDefinition(
                    name=qname,
                    display_name=cmd,
                    description=f"Shell command: {cmd} (requires approval)",
                    category=ToolCategory.SHELL,
                    source=self.name,
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": f"Full command string starting with '{cmd}'",
                            }
                        },
                        "required": ["command"],
                    },
                    requires_approval=True,
                    trust_level=3,
                    tags=["shell", "ask_before"],
                )
            )

        # 2. Hardcoded allowlist
        for cmd, config in (_ALLOWED_COMMANDS or {}).items():
            qname = f"shell:{cmd}"
            if qname in seen:
                continue
            seen.add(qname)
            needs_approval = config.get("requires_approval", False)
            risk = config.get("max_risk", 5)
            definitions.append(
                ToolDefinition(
                    name=qname,
                    display_name=cmd,
                    description=f"Shell command: {cmd}",
                    category=ToolCategory.SHELL,
                    source=self.name,
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": f"Full command string starting with '{cmd}'",
                            }
                        },
                        "required": ["command"],
                    },
                    requires_approval=needs_approval,
                    trust_level=2 if not needs_approval else 3,
                    tags=["shell", "allowlist"],
                    metadata={"max_risk": risk},
                )
            )

        # 3. Approval-required commands
        for cmd_pattern in (_APPROVAL_REQUIRED or set()):
            base_cmd = cmd_pattern.split()[0]
            qname = f"shell:{base_cmd}"
            if qname in seen:
                continue
            seen.add(qname)
            definitions.append(
                ToolDefinition(
                    name=qname,
                    display_name=base_cmd,
                    description=f"Shell command: {base_cmd} (requires approval)",
                    category=ToolCategory.SHELL,
                    source=self.name,
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": f"Full command string starting with '{base_cmd}'",
                            }
                        },
                        "required": ["command"],
                    },
                    requires_approval=True,
                    trust_level=3,
                    tags=["shell", "approval_required"],
                )
            )

        self._tools_cache = definitions
        logger.info("Shell source refreshed: %d commands", len(definitions))
        return len(definitions)

    # -- Execution ----------------------------------------------------------

    async def call_tool(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        """
        Execute a shell command.

        ``tool_name`` is the local name without ``shell:`` prefix.
        ``args`` must contain a ``command`` key with the full command string.
        """
        shell = self._get_shell()
        if shell is None:
            return ToolResult(
                success=False,
                output=None,
                error="SecureShell not available",
            )

        command = args.get("command")
        if not command:
            return ToolResult(
                success=False,
                output=None,
                error="Missing required 'command' argument",
            )

        try:
            shell_result = shell.execute(
                command,
                env=args.get("env"),
                stdin=args.get("stdin"),
            )

            success = shell_result.exit_code == 0 and shell_result.approval_status.value == "approved"
            output = shell_result.stdout or shell_result.stderr

            return ToolResult(
                success=success,
                output=output,
                error=shell_result.stderr if not success else None,
                audit_id=shell_result.audit_id,
            )
        except Exception as exc:
            logger.exception("Shell execution failed: %s", command)
            return ToolResult(
                success=False,
                output=None,
                error=str(exc),
            )
