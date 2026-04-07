"""
ABLE v2 Sandbox Module
Secure code execution environment with fallback chain.

Fallback order (configurable via ABLE_SANDBOX_BACKEND env var):
    1. RustPython (if installed) — no subprocess, no filesystem
    2. SecureSandbox (subprocess) — resource-limited subprocess
    3. Reject with explanation
"""

import logging
import os
from typing import Optional

from .executor import SecureSandbox, ExecutionResult, ExecutionStatus

logger = logging.getLogger(__name__)

__all__ = ['SecureSandbox', 'eval_code']


def eval_code(
    code: str,
    timeout: float = 5.0,
    backend: Optional[str] = None,
) -> dict:
    """
    Evaluate Python code using the best available sandbox backend.

    Args:
        code: Python source code to execute
        timeout: Maximum execution time in seconds
        backend: Force a specific backend ("rustpython" or "subprocess").
                 If None, uses ABLE_SANDBOX_BACKEND env var or auto-detects.

    Returns:
        dict with keys: output, error, duration_ms, backend_used
    """
    forced = backend or os.environ.get("ABLE_SANDBOX_BACKEND", "").lower()

    # Try RustPython first (unless forced to subprocess)
    if forced != "subprocess":
        try:
            from .rustpython_sandbox import RustPythonSandbox, SandboxUnavailable

            if forced == "rustpython" or RustPythonSandbox.available():
                sandbox = RustPythonSandbox()
                result = sandbox.eval(code, timeout=timeout)
                return {
                    "output": result.output,
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                    "backend_used": "rustpython",
                }
        except Exception as e:
            if forced == "rustpython":
                return {
                    "output": "",
                    "error": f"RustPython backend requested but failed: {e}",
                    "duration_ms": 0.0,
                    "backend_used": "none",
                }
            logger.debug("RustPython unavailable, falling back: %s", e)

    # Fall back to subprocess sandbox
    if forced != "rustpython":
        try:
            sandbox = SecureSandbox(timeout=int(timeout))
            result = sandbox.execute(code, language="python")
            return {
                "output": result.stdout,
                "error": result.stderr if result.status != ExecutionStatus.SUCCESS else None,
                "duration_ms": result.execution_time * 1000,
                "backend_used": "subprocess",
            }
        except Exception as e:
            logger.warning("Subprocess sandbox failed: %s", e)
            return {
                "output": "",
                "error": f"All sandbox backends failed. Last error: {e}",
                "duration_ms": 0.0,
                "backend_used": "none",
            }

    return {
        "output": "",
        "error": "No sandbox backend available",
        "duration_ms": 0.0,
        "backend_used": "none",
    }
