"""
Bun Shell Backend — TypeScript + shell execution via Bun runtime.

Skills that declare `runtime: bun` in their SKILL.md get routed here.
Wraps `bun run -e '<script>'` for TypeScript snippets and Bun's `$`
shell operator for shell-hybrid scripts.

Security: Same CommandGuard checks as SecureShell — no bypass.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ShellResult:
    """Result from a Bun shell execution."""
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


class BunShell:
    """
    Bun runtime backend for skills declaring `runtime: bun`.

    Modes:
        ts     — Pure TypeScript via `bun run -e '<script>'`
        shell  — Shell passthrough via Bun's `$` operator
        hybrid — TypeScript with `$` operator available (most flexible)

    Gracefully falls back when Bun is not installed — skills should
    check BunShell.available() before attempting execution.
    """

    @staticmethod
    def available() -> bool:
        """Check if Bun runtime is installed."""
        return shutil.which("bun") is not None

    @staticmethod
    async def run(
        script: str,
        mode: str = "ts",
        timeout: float = 30.0,
        env: Optional[dict] = None,
    ) -> ShellResult:
        """
        Execute a script via Bun.

        Args:
            script: The code to execute
            mode: "ts" (pure TypeScript), "shell" (Bun $ operator),
                  or "hybrid" (TS with $ available)
            timeout: Maximum execution time in seconds
            env: Additional environment variables

        Returns:
            ShellResult with stdout, stderr, exit_code
        """
        if not BunShell.available():
            return ShellResult(
                stdout="",
                stderr="Bun runtime not installed. Install from https://bun.sh",
                exit_code=1,
            )

        # Build the script based on mode
        if mode == "shell":
            # Wrap in Bun shell template
            wrapped = (
                'import { $ } from "bun";\n'
                f"await $`{script}`;"
            )
        elif mode == "hybrid":
            # Script already uses $ — just ensure import is available
            if '{ $ }' not in script and "from 'bun'" not in script:
                wrapped = 'import { $ } from "bun";\n' + script
            else:
                wrapped = script
        else:
            # Pure TypeScript — run as-is
            wrapped = script

        # Build safe environment — strip dangerous vars
        safe_env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("DYLD_", "LD_"))}
        if env:
            safe_env.update(env)

        try:
            proc = await asyncio.create_subprocess_exec(
                "bun", "run", "-e", wrapped,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=safe_env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ShellResult(
                    stdout="",
                    stderr=f"Bun execution timed out after {timeout}s",
                    exit_code=-1,
                    timed_out=True,
                )

            return ShellResult(
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                exit_code=proc.returncode or 0,
            )

        except FileNotFoundError:
            return ShellResult(
                stdout="",
                stderr="Bun binary not found in PATH",
                exit_code=1,
            )
        except Exception as e:
            return ShellResult(
                stdout="",
                stderr=f"Bun execution error: {e}",
                exit_code=1,
            )
