"""
D2 — RTK Token Compression Wrapper.

Transparently wraps shell commands through RTK for 60-90% token savings
on tool outputs. RTK compresses verbose CLI output into concise summaries
that preserve all actionable information.

Source: rtk-ai/rtk (Rust CLI proxy)
Dependency: `cargo install rtk` (graceful degradation if absent)
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RTKResult:
    """Result of an RTK-wrapped command."""
    command: str
    original_output: str
    compressed_output: str
    savings_pct: float  # 0.0 - 1.0
    rtk_used: bool
    exit_code: int


# Commands that benefit most from RTK compression
_COMPRESSIBLE_COMMANDS = {
    "git status", "git diff", "git log", "git show", "git blame",
    "ls", "find", "tree", "du",
    "npm ls", "pip list", "cargo tree",
    "docker ps", "docker images",
    "kubectl get", "kubectl describe",
    "cat", "head", "tail",
}

_rtk_path: Optional[str] = None
_rtk_checked = False


def _find_rtk() -> Optional[str]:
    """Find the RTK binary, caching the result."""
    global _rtk_path, _rtk_checked
    if _rtk_checked:
        return _rtk_path
    _rtk_checked = True
    _rtk_path = shutil.which("rtk")
    return _rtk_path


def is_available() -> bool:
    """Check if RTK is installed and available."""
    return _find_rtk() is not None


def should_compress(command: str) -> bool:
    """Check if a command would benefit from RTK compression."""
    cmd_lower = command.lower().strip()
    return any(cmd_lower.startswith(prefix) for prefix in _COMPRESSIBLE_COMMANDS)


def wrap_command(command: str) -> str:
    """Wrap a command with RTK if available and beneficial.

    Returns the original command if RTK is unavailable or the command
    wouldn't benefit from compression.
    """
    rtk = _find_rtk()
    if rtk is None:
        return command
    if not should_compress(command):
        return command
    return f"{rtk} {command}"


def compress_output(command: str, output: str,
                    timeout: int = 10) -> RTKResult:
    """Run a command through RTK compression.

    If RTK is unavailable, returns the output unchanged.
    """
    rtk = _find_rtk()

    if rtk is None or not should_compress(command):
        return RTKResult(
            command=command,
            original_output=output,
            compressed_output=output,
            savings_pct=0.0,
            rtk_used=False,
            exit_code=0,
        )

    try:
        result = subprocess.run(
            [rtk, *command.split()],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        compressed = result.stdout
        original_len = len(output)
        compressed_len = len(compressed)
        savings = (
            1.0 - (compressed_len / original_len)
            if original_len > 0
            else 0.0
        )

        return RTKResult(
            command=command,
            original_output=output,
            compressed_output=compressed,
            savings_pct=max(0.0, savings),
            rtk_used=True,
            exit_code=result.returncode,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return RTKResult(
            command=command,
            original_output=output,
            compressed_output=output,
            savings_pct=0.0,
            rtk_used=False,
            exit_code=-1,
        )
