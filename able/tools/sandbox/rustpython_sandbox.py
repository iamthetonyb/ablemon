"""
RustPython Sandbox — optional Python evaluation backend.

Uses RustPython (if installed) for sandboxed code execution with no
subprocess, no filesystem access. Falls back to the existing subprocess
sandbox when RustPython is unavailable.

Fallback chain (managed by eval_code() in __init__.py):
    RustPython → subprocess sandbox → reject with explanation
"""

from __future__ import annotations

import importlib.util
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class SandboxUnavailable(Exception):
    """Raised when the RustPython backend is not installed."""
    pass


@dataclass
class SandboxResult:
    """Result of sandboxed code evaluation."""
    output: str
    error: Optional[str] = None
    duration_ms: float = 0.0


# Builtins that are blocked in the RustPython sandbox
_BLOCKED_BUILTINS = frozenset({
    "open", "exec", "eval", "compile", "__import__",
    "breakpoint", "exit", "quit", "input",
})


def _is_available() -> bool:
    """Check if RustPython VM is installed."""
    return importlib.util.find_spec("rustpython_vm") is not None


class RustPythonSandbox:
    """
    Sandboxed Python evaluation via RustPython.

    No subprocess, no filesystem access. Blocks dangerous builtins.
    Intended for untrusted code snippets from swarm agents and skills.
    """

    def __init__(self):
        if not _is_available():
            raise SandboxUnavailable(
                "rustpython_vm package not installed. "
                "Install with: pip install rustpython"
            )

    @staticmethod
    def available() -> bool:
        """Check if RustPython backend is available."""
        return _is_available()

    def eval(self, code: str, timeout: float = 5.0) -> SandboxResult:
        """
        Evaluate Python code in the RustPython sandbox.

        Args:
            code: Python source code to execute
            timeout: Maximum execution time in seconds

        Returns:
            SandboxResult with output and optional error

        Raises:
            SandboxUnavailable: If RustPython is not installed
        """
        import rustpython_vm as vm  # type: ignore[import-not-found]

        start = time.monotonic()

        # Create a restricted scope — block dangerous builtins
        import builtins as _builtins
        safe_builtins = {
            k: v for k, v in vars(_builtins).items()
            if k not in _BLOCKED_BUILTINS
        }

        # Add a capture mechanism for output
        output_lines: list[str] = []

        def safe_print(*args, **kwargs):
            output_lines.append(
                kwargs.get("sep", " ").join(str(a) for a in args)
            )

        safe_builtins["print"] = safe_print

        try:
            # Execute with restricted globals
            restricted_globals = {"__builtins__": safe_builtins}
            vm.exec(code, restricted_globals, timeout=timeout)

            duration_ms = (time.monotonic() - start) * 1000
            return SandboxResult(
                output="\n".join(output_lines),
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            return SandboxResult(
                output="\n".join(output_lines),
                error=f"Execution timed out after {timeout}s",
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            return SandboxResult(
                output="\n".join(output_lines),
                error=str(e),
                duration_ms=duration_ms,
            )
