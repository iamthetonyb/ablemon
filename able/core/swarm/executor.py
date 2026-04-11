"""
D3 — Multi-Agent Executor Abstraction.

Provides a uniform interface for spawning coding agent sessions across
different AI CLI tools. Runtime auto-detection discovers which agents
are installed. Workspace isolation via git worktrees per task.

Forked from BloopAI/vibe-kanban StandardCodingAgentExecutor pattern.

Usage:
    registry = ExecutorRegistry()
    registry.discover()  # Auto-detect installed agents
    executor = registry.get("claude-code")
    session = await executor.spawn("Fix the login bug", cwd="/my/project")
    print(session.output)

Design:
- Protocol-based: any executor just needs spawn/normalize_logs
- 4 built-in executors: Claude Code, Codex, Gemini CLI, OpenCode
- Worktree isolation: each task gets its own git branch
- Structured log normalization across agent output formats
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class ExecutorOptions:
    """Capabilities and configuration for an executor."""
    name: str
    version: str = "unknown"
    supports_tools: bool = True
    supports_streaming: bool = False
    supports_worktrees: bool = True
    max_iterations: int = 20
    default_model: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentSession:
    """Result from a single agent execution."""
    executor_name: str
    prompt: str
    output: str
    exit_code: int = 0
    duration_s: float = 0
    model_used: str = ""
    tokens_used: int = 0
    tool_calls: int = 0
    worktree_path: Optional[str] = None
    worktree_branch: Optional[str] = None
    error: Optional[str] = None
    raw_output: str = ""


@dataclass
class StructuredLog:
    """Normalized log from any agent's output."""
    steps: List[Dict[str, str]]  # [{"type": "tool_call"|"thinking"|"output", "content": ...}]
    total_tool_calls: int = 0
    total_tokens: int = 0
    errors: List[str] = field(default_factory=list)
    summary: str = ""


@runtime_checkable
class CodingAgentExecutor(Protocol):
    """Protocol for coding agent executors.

    Any CLI agent can be integrated by implementing this interface.
    """

    @property
    def name(self) -> str: ...

    async def spawn(
        self,
        prompt: str,
        cwd: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> AgentSession: ...

    async def spawn_follow_up(
        self,
        session: AgentSession,
        prompt: str,
    ) -> AgentSession: ...

    def normalize_logs(self, raw_output: str) -> StructuredLog: ...

    def discover_options(self) -> ExecutorOptions: ...


# ── Built-in executors ───────────────────────────────────────────


class ClaudeCodeExecutor:
    """Executor for Claude Code CLI (claude)."""

    name = "claude-code"

    def __init__(self, model: str = "sonnet"):
        self._model = model
        self._binary = shutil.which("claude")

    def discover_options(self) -> ExecutorOptions:
        return ExecutorOptions(
            name=self.name,
            version=self._get_version(),
            supports_tools=True,
            supports_streaming=True,
            supports_worktrees=True,
            max_iterations=20,
            default_model=self._model,
        )

    async def spawn(
        self,
        prompt: str,
        cwd: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> AgentSession:
        opts = options or {}
        model = opts.get("model", self._model)
        max_turns = opts.get("max_turns", 20)
        start = time.perf_counter()

        cmd = [
            self._binary or "claude",
            "--print",
            "--model", model,
            "--max-turns", str(max_turns),
            "--output-format", "text",
            prompt,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=opts.get("timeout", 300)
            )
            output = stdout.decode("utf-8", errors="replace")
            error = stderr.decode("utf-8", errors="replace") if proc.returncode != 0 else None

            return AgentSession(
                executor_name=self.name,
                prompt=prompt,
                output=output,
                exit_code=proc.returncode or 0,
                duration_s=time.perf_counter() - start,
                model_used=model,
                raw_output=output,
                error=error,
            )
        except asyncio.TimeoutError:
            return AgentSession(
                executor_name=self.name,
                prompt=prompt,
                output="",
                exit_code=-1,
                duration_s=time.perf_counter() - start,
                error="Timed out",
            )
        except FileNotFoundError:
            return AgentSession(
                executor_name=self.name,
                prompt=prompt,
                output="",
                exit_code=-1,
                duration_s=0,
                error="claude CLI not found",
            )

    async def spawn_follow_up(
        self,
        session: AgentSession,
        prompt: str,
    ) -> AgentSession:
        cwd = session.worktree_path or "."
        return await self.spawn(
            f"Continue from previous work. {prompt}",
            cwd=cwd,
            options={"model": session.model_used},
        )

    def normalize_logs(self, raw_output: str) -> StructuredLog:
        steps = []
        tool_calls = 0
        for line in raw_output.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("[tool:") or stripped.startswith("Tool:"):
                steps.append({"type": "tool_call", "content": stripped})
                tool_calls += 1
            elif stripped.startswith("Thinking:") or stripped.startswith("> "):
                steps.append({"type": "thinking", "content": stripped})
            else:
                steps.append({"type": "output", "content": stripped})
        return StructuredLog(steps=steps, total_tool_calls=tool_calls)

    def _get_version(self) -> str:
        if not self._binary:
            return "not installed"
        try:
            import subprocess
            result = subprocess.run(
                [self._binary, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip()[:50] if result.returncode == 0 else "unknown"
        except Exception:
            return "unknown"


class CodexExecutor:
    """Executor for OpenAI Codex CLI."""

    name = "codex"

    def __init__(self):
        self._binary = shutil.which("codex")

    def discover_options(self) -> ExecutorOptions:
        return ExecutorOptions(
            name=self.name,
            version="installed" if self._binary else "not installed",
            supports_tools=True,
            supports_streaming=False,
            default_model="codex",
        )

    async def spawn(
        self,
        prompt: str,
        cwd: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> AgentSession:
        opts = options or {}
        start = time.perf_counter()

        cmd = [self._binary or "codex", prompt]
        approval = opts.get("approval_mode", "suggest")
        if approval:
            cmd = [self._binary or "codex", f"--approval-mode={approval}", prompt]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=opts.get("timeout", 300)
            )
            output = stdout.decode("utf-8", errors="replace")
            return AgentSession(
                executor_name=self.name,
                prompt=prompt,
                output=output,
                exit_code=proc.returncode or 0,
                duration_s=time.perf_counter() - start,
                raw_output=output,
                error=stderr.decode() if proc.returncode != 0 else None,
            )
        except (asyncio.TimeoutError, FileNotFoundError) as e:
            return AgentSession(
                executor_name=self.name,
                prompt=prompt,
                output="",
                exit_code=-1,
                duration_s=time.perf_counter() - start,
                error=str(e),
            )

    async def spawn_follow_up(self, session: AgentSession, prompt: str) -> AgentSession:
        return await self.spawn(prompt, cwd=session.worktree_path or ".")

    def normalize_logs(self, raw_output: str) -> StructuredLog:
        steps = [{"type": "output", "content": line} for line in raw_output.split("\n") if line.strip()]
        return StructuredLog(steps=steps)


class GeminiExecutor:
    """Executor for Google Gemini CLI."""

    name = "gemini-cli"

    def __init__(self):
        self._binary = shutil.which("gemini")

    def discover_options(self) -> ExecutorOptions:
        return ExecutorOptions(
            name=self.name,
            version="installed" if self._binary else "not installed",
            supports_tools=True,
            supports_streaming=True,
            default_model="gemini-2.5-pro",
        )

    async def spawn(
        self,
        prompt: str,
        cwd: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> AgentSession:
        opts = options or {}
        start = time.perf_counter()

        cmd = [self._binary or "gemini", "-p", prompt]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=opts.get("timeout", 300)
            )
            output = stdout.decode("utf-8", errors="replace")
            return AgentSession(
                executor_name=self.name,
                prompt=prompt,
                output=output,
                exit_code=proc.returncode or 0,
                duration_s=time.perf_counter() - start,
                raw_output=output,
                error=stderr.decode() if proc.returncode != 0 else None,
            )
        except (asyncio.TimeoutError, FileNotFoundError) as e:
            return AgentSession(
                executor_name=self.name,
                prompt=prompt,
                output="",
                exit_code=-1,
                duration_s=time.perf_counter() - start,
                error=str(e),
            )

    async def spawn_follow_up(self, session: AgentSession, prompt: str) -> AgentSession:
        return await self.spawn(prompt, cwd=session.worktree_path or ".")

    def normalize_logs(self, raw_output: str) -> StructuredLog:
        steps = [{"type": "output", "content": line} for line in raw_output.split("\n") if line.strip()]
        return StructuredLog(steps=steps)


class OpenCodeExecutor:
    """Executor for OpenCode CLI."""

    name = "opencode"

    def __init__(self):
        self._binary = shutil.which("opencode")

    def discover_options(self) -> ExecutorOptions:
        return ExecutorOptions(
            name=self.name,
            version="installed" if self._binary else "not installed",
            supports_tools=True,
            supports_streaming=False,
            default_model="opencode",
        )

    async def spawn(
        self,
        prompt: str,
        cwd: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> AgentSession:
        opts = options or {}
        start = time.perf_counter()
        cmd = [self._binary or "opencode", "run", prompt]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=opts.get("timeout", 300)
            )
            output = stdout.decode("utf-8", errors="replace")
            return AgentSession(
                executor_name=self.name,
                prompt=prompt,
                output=output,
                exit_code=proc.returncode or 0,
                duration_s=time.perf_counter() - start,
                raw_output=output,
                error=stderr.decode() if proc.returncode != 0 else None,
            )
        except (asyncio.TimeoutError, FileNotFoundError) as e:
            return AgentSession(
                executor_name=self.name,
                prompt=prompt,
                output="",
                exit_code=-1,
                duration_s=time.perf_counter() - start,
                error=str(e),
            )

    async def spawn_follow_up(self, session: AgentSession, prompt: str) -> AgentSession:
        return await self.spawn(prompt, cwd=session.worktree_path or ".")

    def normalize_logs(self, raw_output: str) -> StructuredLog:
        steps = [{"type": "output", "content": line} for line in raw_output.split("\n") if line.strip()]
        return StructuredLog(steps=steps)


# ── Registry ─────────────────────────────────────────────────────


_BUILTIN_EXECUTORS = [
    ClaudeCodeExecutor,
    CodexExecutor,
    GeminiExecutor,
    OpenCodeExecutor,
]


class ExecutorRegistry:
    """Registry of available coding agent executors.

    Auto-discovers which agents are installed and provides
    a uniform interface for spawning tasks across them.
    """

    def __init__(self):
        self._executors: Dict[str, CodingAgentExecutor] = {}
        self._discovery_done = False

    def discover(self) -> Dict[str, ExecutorOptions]:
        """Auto-detect installed coding agents.

        Returns dict of {name: options} for all discovered agents.
        """
        results = {}
        for cls in _BUILTIN_EXECUTORS:
            try:
                executor = cls()
                opts = executor.discover_options()
                # Only register if the binary exists
                binary = shutil.which(executor.name.replace("-cli", "").replace("-", ""))
                if binary or executor.name == "claude-code":
                    # Claude Code gets special check
                    if executor.name == "claude-code":
                        binary = shutil.which("claude")
                    if binary:
                        self._executors[executor.name] = executor
                        results[executor.name] = opts
                        logger.debug("Discovered executor: %s (%s)", executor.name, opts.version)
            except Exception as e:
                logger.debug("Failed to discover %s: %s", cls.__name__, e)

        self._discovery_done = True
        return results

    def register(self, executor: CodingAgentExecutor) -> None:
        """Manually register an executor."""
        self._executors[executor.name] = executor

    def get(self, name: str) -> Optional[CodingAgentExecutor]:
        """Get executor by name. Returns None if not found."""
        if not self._discovery_done:
            self.discover()
        return self._executors.get(name)

    def available(self) -> List[str]:
        """List names of available executors."""
        if not self._discovery_done:
            self.discover()
        return list(self._executors.keys())

    def best_available(self, prefer: Optional[List[str]] = None) -> Optional[CodingAgentExecutor]:
        """Get the best available executor.

        Args:
            prefer: Ordered preference list. First match wins.

        Returns first available from preference list, or first discovered.
        """
        if not self._discovery_done:
            self.discover()

        if prefer:
            for name in prefer:
                if name in self._executors:
                    return self._executors[name]

        # Default priority: claude-code > codex > gemini > opencode
        for name in ["claude-code", "codex", "gemini-cli", "opencode"]:
            if name in self._executors:
                return self._executors[name]

        return None

    def stats(self) -> Dict[str, Any]:
        """Return registry stats."""
        return {
            "discovered": self._discovery_done,
            "available": list(self._executors.keys()),
            "count": len(self._executors),
        }


# ── Worktree isolation ───────────────────────────────────────────


class WorktreeIsolator:
    """Create and manage git worktrees for isolated task execution."""

    @staticmethod
    async def create_worktree(
        repo_path: str,
        branch_name: str,
        base_branch: str = "main",
    ) -> Optional[str]:
        """Create a git worktree for isolated work.

        Returns the worktree path, or None on failure.
        """
        worktree_dir = Path(repo_path) / ".worktrees" / branch_name
        if worktree_dir.exists():
            return str(worktree_dir)

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "add", "-b", branch_name,
                str(worktree_dir), base_branch,
                cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error("Failed to create worktree: %s", stderr.decode())
                return None
            return str(worktree_dir)
        except Exception as e:
            logger.error("Worktree creation error: %s", e)
            return None

    @staticmethod
    async def remove_worktree(repo_path: str, branch_name: str) -> bool:
        """Remove a git worktree and its branch."""
        worktree_dir = Path(repo_path) / ".worktrees" / branch_name
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "remove", str(worktree_dir), "--force",
                cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    @staticmethod
    async def list_worktrees(repo_path: str) -> List[Dict[str, str]]:
        """List active worktrees."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "list", "--porcelain",
                cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []

            worktrees = []
            current: Dict[str, str] = {}
            for line in stdout.decode().split("\n"):
                if line.startswith("worktree "):
                    if current:
                        worktrees.append(current)
                    current = {"path": line[9:]}
                elif line.startswith("branch "):
                    current["branch"] = line[7:]
                elif line == "":
                    if current:
                        worktrees.append(current)
                    current = {}
            return worktrees
        except Exception:
            return []
