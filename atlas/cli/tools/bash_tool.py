"""
BashExecute tool — Secure shell execution wrapping the existing SecureShell/CommandGuard.

Anti-exfiltration: blocks curl/wget/nc piped with file reads unless safe_mode is off.
"""

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from .base import CLITool, ToolContext

# Lazy imports for CommandGuard (may not be on sys.path in all contexts)
_COMMAND_GUARD = None


def _get_command_guard():
    global _COMMAND_GUARD
    if _COMMAND_GUARD is None:
        try:
            import sys
            atlas_root = str(Path(__file__).resolve().parent.parent.parent)
            if atlas_root not in sys.path:
                sys.path.insert(0, atlas_root)
            from core.security.command_guard import CommandGuard
            _COMMAND_GUARD = CommandGuard
        except ImportError:
            _COMMAND_GUARD = None
    return _COMMAND_GUARD


# Patterns that indicate data exfiltration attempts
_EXFIL_PATTERNS = [
    re.compile(r"(curl|wget|nc|netcat)\b.*<\s*\S+", re.IGNORECASE),
    re.compile(r"cat\b.*\|\s*(curl|wget|nc|netcat)\b", re.IGNORECASE),
    re.compile(r"(curl|wget)\b.*-d\s+@", re.IGNORECASE),
    re.compile(r"(curl|wget)\b.*--data.*@", re.IGNORECASE),
]


class BashExecute(CLITool):
    """Execute a shell command with security checks via CommandGuard."""

    def __init__(self, **_kw):
        super().__init__(
            name="bash_execute",
            description=(
                "Execute a bash command. Commands are checked against the "
                "security allowlist. Destructive/unknown commands may be blocked."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 120).",
                    },
                    "work_dir": {
                        "type": "string",
                        "description": "Working directory for the command.",
                    },
                },
                "required": ["command"],
            },
            is_destructive=False,  # determined dynamically per call
            is_concurrent_safe=False,
        )

    def validate_input(self, args: dict, ctx: Optional[ToolContext] = None) -> Optional[str]:
        command = args.get("command", "").strip()
        if not command:
            return "Command cannot be empty"

        safe_mode = ctx.safe_mode if ctx else True

        # Anti-exfiltration check
        if safe_mode:
            for pat in _EXFIL_PATTERNS:
                if pat.search(command):
                    return "Blocked: potential data exfiltration pattern detected"

        # Run through CommandGuard if available
        GuardCls = _get_command_guard()
        if GuardCls is not None:
            guard = GuardCls(trust_tier=1)
            analysis = guard.analyze(command)
            if analysis.verdict.value == "denied":
                return f"Command denied: {analysis.reason}"

        return None

    async def execute(self, args: dict, ctx: Optional[ToolContext] = None) -> str:
        work_dir = self._resolve_work_dir(ctx)
        command = args["command"]
        timeout = args.get("timeout", 120)
        cmd_work_dir = args.get("work_dir")
        if cmd_work_dir:
            cmd_work_dir = Path(cmd_work_dir)
        else:
            cmd_work_dir = work_dir

        start = datetime.now()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cmd_work_dir),
            )
            elapsed = (datetime.now() - start).total_seconds()

            output_parts = []
            if proc.stdout:
                output_parts.append(proc.stdout[:50000])
            if proc.stderr:
                output_parts.append(proc.stderr[:50000])

            result = "\n".join(output_parts) or "(no output)"

            if proc.returncode != 0:
                result = f"Exit code {proc.returncode}\n{result}"

            return result

        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s"
        except OSError as e:
            return f"Execution error: {e}"
