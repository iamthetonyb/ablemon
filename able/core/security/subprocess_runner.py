"""Standardized subprocess execution with security guardrails.

Provides consistent I/O patterns for all subprocess calls in ABLE:
- Timeout enforcement (default 30s)
- Output capture + truncation (default 50KB)
- Exit code checking
- Environment sanitization (blocks injection vectors)
- Both sync and async execution paths

Replaces ad-hoc subprocess.run / asyncio.create_subprocess_* calls.
Migration is phased — new code should use this; existing code migrates gradually.
"""

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

# Env vars that enable code injection via language runtimes or linkers.
# These are blocked unless explicitly allowlisted per invocation.
BLOCKED_ENV_VARS = frozenset({
    # Linker injection
    "LD_PRELOAD", "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH",
    # Python injection
    "PYTHONPATH", "PYTHONSTARTUP",
    # Java injection
    "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS",
    # Rust injection
    "RUSTFLAGS", "RUSTDOCFLAGS",
    # Git injection
    "GIT_PROXY_COMMAND", "GIT_SSH_COMMAND",
    # K8s
    "KUBECONFIG",
    # Node injection
    "NODE_OPTIONS",
})

# Prefix patterns: any env var starting with these is blocked
BLOCKED_ENV_PREFIXES = ("LD_", "DYLD_")

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_OUTPUT = 50_000  # 50KB
TRUNCATION_MARKER = "\n[TRUNCATED — {} bytes omitted]"


@dataclass
class SubprocessResult:
    """Standardized result from subprocess execution."""
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    truncated: bool = False
    command: Union[str, List[str]] = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """Combined stdout + stderr for convenience."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts)


def _sanitize_env(
    env: Optional[Dict[str, str]] = None,
    env_allowlist: Optional[Sequence[str]] = None,
) -> Dict[str, str]:
    """Build a sanitized environment dict.

    Starts from the current process environment, merges *env* overrides,
    then strips all blocked variables except those in *env_allowlist*.
    """
    result = os.environ.copy()
    if env:
        result.update(env)

    allowed = set(env_allowlist or [])
    to_remove = []
    for key in result:
        if key in allowed:
            continue
        if key in BLOCKED_ENV_VARS:
            to_remove.append(key)
        elif any(key.startswith(prefix) for prefix in BLOCKED_ENV_PREFIXES):
            to_remove.append(key)

    for key in to_remove:
        del result[key]
        logger.debug("[SubprocessRunner] Stripped env var: %s", key)

    return result


def _truncate(text: str, max_bytes: int) -> tuple:
    """Truncate text to max_bytes, returning (result, was_truncated)."""
    if len(text) <= max_bytes:
        return text, False
    omitted = len(text) - max_bytes
    marker = TRUNCATION_MARKER.format(omitted)
    return text[:max_bytes] + marker, True


def run(
    cmd: Union[str, List[str]],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
    env: Optional[Dict[str, str]] = None,
    env_allowlist: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
    stdin_data: Optional[str] = None,
    shell: bool = False,
) -> SubprocessResult:
    """Run a subprocess synchronously with guardrails.

    Args:
        cmd: Command as string (requires shell=True) or list of args.
        timeout: Max execution time in seconds.
        max_output: Max bytes for stdout/stderr each.
        env: Extra environment variables to set.
        env_allowlist: Env vars from BLOCKED_ENV_VARS to keep.
        cwd: Working directory.
        stdin_data: Data to pipe to stdin.
        shell: Use shell execution (avoid when possible).
    """
    safe_env = _sanitize_env(env, env_allowlist)

    try:
        proc = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=safe_env,
            input=stdin_data,
        )
        stdout, stdout_trunc = _truncate(proc.stdout or "", max_output)
        stderr, stderr_trunc = _truncate(proc.stderr or "", max_output)

        return SubprocessResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            truncated=stdout_trunc or stderr_trunc,
            command=cmd,
        )
    except subprocess.TimeoutExpired:
        return SubprocessResult(
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            exit_code=-1,
            timed_out=True,
            command=cmd,
        )
    except FileNotFoundError as exc:
        return SubprocessResult(
            stdout="",
            stderr=f"Command not found: {exc}",
            exit_code=127,
            command=cmd,
        )
    except PermissionError as exc:
        return SubprocessResult(
            stdout="",
            stderr=f"Permission denied: {exc}",
            exit_code=126,
            command=cmd,
        )


async def async_run(
    cmd: Union[str, List[str]],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
    env: Optional[Dict[str, str]] = None,
    env_allowlist: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
    stdin_data: Optional[str] = None,
) -> SubprocessResult:
    """Run a subprocess asynchronously with guardrails.

    Always uses exec (not shell). For shell syntax, pass
    ``["/bin/sh", "-c", "your command"]`` as *cmd*.
    """
    safe_env = _sanitize_env(env, env_allowlist)

    if isinstance(cmd, str):
        cmd_list = cmd.split()
    else:
        cmd_list = list(cmd)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_list,
            stdin=asyncio.subprocess.PIPE if stdin_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=safe_env,
        )

        if stdin_data:
            raw_stdout, raw_stderr = await asyncio.wait_for(
                proc.communicate(stdin_data.encode()),
                timeout=timeout,
            )
        else:
            raw_stdout, raw_stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

        stdout_str = (raw_stdout or b"").decode(errors="replace")
        stderr_str = (raw_stderr or b"").decode(errors="replace")
        stdout, stdout_trunc = _truncate(stdout_str, max_output)
        stderr, stderr_trunc = _truncate(stderr_str, max_output)

        return SubprocessResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode or 0,
            truncated=stdout_trunc or stderr_trunc,
            command=cmd,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return SubprocessResult(
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            exit_code=-1,
            timed_out=True,
            command=cmd,
        )
    except FileNotFoundError as exc:
        return SubprocessResult(
            stdout="",
            stderr=f"Command not found: {exc}",
            exit_code=127,
            command=cmd,
        )
