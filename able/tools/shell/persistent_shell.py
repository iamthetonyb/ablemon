"""
E4 — Persistent Shell Mode.

Maintains a long-running shell process per session. cd, env vars, and aliases
persist across tool calls. Cleanup on session end.

Forked from Hermes v0.3 PR #1067 pattern.

Usage:
    shell = PersistentShell(session_id="user-123")
    result = await shell.run("cd /tmp && export FOO=bar")
    result2 = await shell.run("echo $FOO && pwd")  # Sees /tmp and bar
    await shell.close()

Integration:
    Wire into SecureShell as an optional backend — when persistent_mode=True,
    delegate to PersistentShell instead of subprocess.run().
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Sentinel marker for end-of-output detection
_SENTINEL = "___ABLE_EOC_MARKER___"
_SENTINEL_CMD = f'echo "{_SENTINEL}$?"'


@dataclass
class PersistentShellResult:
    """Result from a persistent shell command."""
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float
    session_id: str


class PersistentShell:
    """Long-running shell process that preserves state across commands.

    Each session gets its own shell subprocess. Working directory, environment
    variables, aliases, and shell functions persist between calls.

    Thread-safe: uses asyncio.Lock to serialize command execution.
    """

    def __init__(
        self,
        session_id: str,
        shell: str = "/bin/zsh",
        timeout: float = 60.0,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ):
        self.session_id = session_id
        self._shell = shell
        self._timeout = timeout
        self._process: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()
        self._env = env
        self._cwd = cwd
        self._started_at: Optional[float] = None
        self._command_count = 0

    @property
    def is_alive(self) -> bool:
        """Check if the shell process is still running."""
        return self._process is not None and self._process.returncode is None

    @property
    def uptime_s(self) -> float:
        """Seconds since shell was started."""
        if self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at

    async def _ensure_started(self) -> None:
        """Start the shell process if not already running."""
        if self.is_alive:
            return

        env = os.environ.copy()
        if self._env:
            env.update(self._env)

        # Strip dangerous env vars
        try:
            from able.core.security.subprocess_runner import (
                BLOCKED_ENV_VARS,
                BLOCKED_ENV_PREFIXES,
            )
            for key in list(env):
                if key in BLOCKED_ENV_VARS:
                    del env[key]
                elif any(key.startswith(p) for p in BLOCKED_ENV_PREFIXES):
                    del env[key]
        except ImportError:
            pass

        self._process = await asyncio.create_subprocess_exec(
            self._shell, "-l",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self._cwd,
        )
        self._started_at = time.monotonic()

        # Disable prompt to avoid noise in output
        await self._write("export PS1='' PS2='' PROMPT_COMMAND=''\n")
        # Read any startup output (motd, etc.)
        await self._drain_startup()

    async def _write(self, text: str) -> None:
        """Write to the shell's stdin."""
        if self._process and self._process.stdin:
            self._process.stdin.write(text.encode())
            await self._process.stdin.drain()

    async def _drain_startup(self) -> None:
        """Consume initial shell startup output."""
        if not self._process or not self._process.stdout:
            return
        try:
            # Give shell a moment to produce startup output
            await asyncio.sleep(0.1)
            # Read whatever is available without blocking
            while True:
                try:
                    await asyncio.wait_for(
                        self._process.stdout.readline(), timeout=0.2
                    )
                except asyncio.TimeoutError:
                    break
        except Exception:
            pass

    async def run(self, command: str) -> PersistentShellResult:
        """Execute a command in the persistent shell.

        The command's working directory, environment, and shell state are
        preserved for the next call. Commands are serialized via an
        asyncio.Lock — concurrent callers block.
        """
        async with self._lock:
            await self._ensure_started()
            start = time.monotonic()
            self._command_count += 1

            if not self._process or not self._process.stdin:
                return PersistentShellResult(
                    command=command,
                    stdout="",
                    stderr="Shell process not available",
                    exit_code=-1,
                    duration_s=0,
                    session_id=self.session_id,
                )

            # Write command + sentinel to detect end of output
            full_cmd = f"{command}\n{_SENTINEL_CMD}\n"
            await self._write(full_cmd)

            # Read stdout until sentinel
            stdout_lines = []
            exit_code = -1
            try:
                while True:
                    line = await asyncio.wait_for(
                        self._process.stdout.readline(),
                        timeout=self._timeout,
                    )
                    decoded = line.decode("utf-8", errors="replace")
                    if _SENTINEL in decoded:
                        # Extract exit code from sentinel line
                        parts = decoded.strip().split(_SENTINEL)
                        if len(parts) > 1:
                            try:
                                exit_code = int(parts[1].strip())
                            except (ValueError, IndexError):
                                exit_code = 0
                        break
                    stdout_lines.append(decoded)
            except asyncio.TimeoutError:
                stdout_lines.append(f"\n[TIMEOUT after {self._timeout}s]")
                exit_code = -1

            # Capture any stderr (non-blocking)
            stderr_text = ""
            if self._process.stderr:
                try:
                    while True:
                        line = await asyncio.wait_for(
                            self._process.stderr.readline(), timeout=0.1
                        )
                        if not line:
                            break
                        stderr_text += line.decode("utf-8", errors="replace")
                except asyncio.TimeoutError:
                    pass

            duration = time.monotonic() - start
            stdout_text = "".join(stdout_lines).rstrip()

            # Truncate large output
            if len(stdout_text) > 50000:
                stdout_text = stdout_text[:50000] + "\n[TRUNCATED]"

            return PersistentShellResult(
                command=command,
                stdout=stdout_text,
                stderr=stderr_text.rstrip(),
                exit_code=exit_code,
                duration_s=duration,
                session_id=self.session_id,
            )

    async def get_env(self, var: str) -> str:
        """Get an environment variable from the shell."""
        result = await self.run(f"echo ${{{var}}}")
        return result.stdout.strip()

    async def get_cwd(self) -> str:
        """Get current working directory."""
        result = await self.run("pwd")
        return result.stdout.strip()

    async def close(self) -> None:
        """Terminate the shell process."""
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
            finally:
                self._process = None
                self._started_at = None
                logger.info(
                    "Persistent shell closed: session=%s, commands=%d",
                    self.session_id, self._command_count,
                )

    def stats(self) -> Dict:
        """Return session stats."""
        return {
            "session_id": self.session_id,
            "alive": self.is_alive,
            "uptime_s": round(self.uptime_s, 1),
            "commands": self._command_count,
        }


class PersistentShellManager:
    """Manages persistent shell instances per session.

    Provides session-level shell lifecycle:
    - get_or_create(session_id) → PersistentShell
    - close(session_id) → cleanup
    - close_all() → cleanup all on gateway shutdown
    """

    def __init__(self, default_timeout: float = 60.0):
        self._shells: Dict[str, PersistentShell] = {}
        self._default_timeout = default_timeout

    def get_or_create(
        self,
        session_id: str,
        **kwargs,
    ) -> PersistentShell:
        """Get existing shell for session or create new one."""
        if session_id in self._shells:
            return self._shells[session_id]

        shell = PersistentShell(
            session_id=session_id,
            timeout=kwargs.get("timeout", self._default_timeout),
            env=kwargs.get("env"),
            cwd=kwargs.get("cwd"),
        )
        self._shells[session_id] = shell
        return shell

    async def close(self, session_id: str) -> None:
        """Close shell for a session."""
        shell = self._shells.pop(session_id, None)
        if shell:
            await shell.close()

    async def close_all(self) -> None:
        """Close all persistent shells (gateway shutdown)."""
        for sid in list(self._shells):
            await self.close(sid)

    @property
    def active_count(self) -> int:
        return sum(1 for s in self._shells.values() if s.is_alive)

    def all_stats(self) -> list:
        return [s.stats() for s in self._shells.values()]
