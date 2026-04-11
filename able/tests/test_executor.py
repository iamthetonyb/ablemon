"""Tests for D3 — Multi-Agent Executor Abstraction.

Covers: executor protocol, registry discovery, session structure,
log normalization, worktree isolation, preference ordering.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from able.core.swarm.executor import (
    AgentSession,
    CodingAgentExecutor,
    ClaudeCodeExecutor,
    CodexExecutor,
    ExecutorOptions,
    ExecutorRegistry,
    GeminiExecutor,
    OpenCodeExecutor,
    StructuredLog,
    WorktreeIsolator,
)


# ── Protocol conformance ─────────────────────────────────────────

class TestProtocol:

    def test_claude_code_implements_protocol(self):
        assert isinstance(ClaudeCodeExecutor(), CodingAgentExecutor)

    def test_codex_implements_protocol(self):
        assert isinstance(CodexExecutor(), CodingAgentExecutor)

    def test_gemini_implements_protocol(self):
        assert isinstance(GeminiExecutor(), CodingAgentExecutor)

    def test_opencode_implements_protocol(self):
        assert isinstance(OpenCodeExecutor(), CodingAgentExecutor)


# ── Executor options ─────────────────────────────────────────────

class TestExecutorOptions:

    def test_claude_code_options(self):
        executor = ClaudeCodeExecutor()
        opts = executor.discover_options()
        assert opts.name == "claude-code"
        assert opts.supports_tools is True
        assert opts.supports_streaming is True

    def test_codex_options(self):
        opts = CodexExecutor().discover_options()
        assert opts.name == "codex"

    def test_gemini_options(self):
        opts = GeminiExecutor().discover_options()
        assert opts.name == "gemini-cli"
        assert opts.default_model == "gemini-2.5-pro"

    def test_options_dataclass(self):
        opts = ExecutorOptions(name="test", version="1.0")
        assert opts.max_iterations == 20
        assert opts.extra == {}


# ── Session structure ────────────────────────────────────────────

class TestSessionStructure:

    def test_session_defaults(self):
        session = AgentSession(
            executor_name="test",
            prompt="do something",
            output="done",
        )
        assert session.exit_code == 0
        assert session.error is None
        assert session.worktree_path is None

    def test_session_with_error(self):
        session = AgentSession(
            executor_name="test",
            prompt="fail",
            output="",
            exit_code=1,
            error="something broke",
        )
        assert session.exit_code == 1
        assert session.error == "something broke"


# ── Log normalization ────────────────────────────────────────────

class TestLogNormalization:

    def test_claude_code_tool_calls(self):
        executor = ClaudeCodeExecutor()
        raw = "[tool: read_file] path=auth.py\nThinking: Need to check auth\nResult: file contents"
        log = executor.normalize_logs(raw)
        assert isinstance(log, StructuredLog)
        assert log.total_tool_calls == 1
        assert any(s["type"] == "tool_call" for s in log.steps)
        assert any(s["type"] == "thinking" for s in log.steps)

    def test_codex_normalization(self):
        executor = CodexExecutor()
        raw = "Line 1\nLine 2\nLine 3"
        log = executor.normalize_logs(raw)
        assert len(log.steps) == 3
        assert all(s["type"] == "output" for s in log.steps)

    def test_empty_output(self):
        executor = ClaudeCodeExecutor()
        log = executor.normalize_logs("")
        assert log.steps == []
        assert log.total_tool_calls == 0


# ── Spawn with mock process ─────────────────────────────────────

class TestSpawn:

    @pytest.mark.asyncio
    async def test_claude_code_spawn_success(self):
        executor = ClaudeCodeExecutor()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output text", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            session = await executor.spawn("fix bug", cwd="/tmp")
            assert session.output == "output text"
            assert session.exit_code == 0
            assert session.error is None

    @pytest.mark.asyncio
    async def test_spawn_timeout(self):
        executor = ClaudeCodeExecutor()

        async def _slow(*args, **kwargs):
            mock = AsyncMock()
            mock.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
            return mock

        with patch("asyncio.create_subprocess_exec", side_effect=_slow):
            session = await executor.spawn("slow task", cwd="/tmp", options={"timeout": 1})
            assert session.exit_code == -1
            assert "Timed out" in session.error

    @pytest.mark.asyncio
    async def test_spawn_binary_not_found(self):
        executor = ClaudeCodeExecutor()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            session = await executor.spawn("test", cwd="/tmp")
            assert session.exit_code == -1
            assert "not found" in session.error

    @pytest.mark.asyncio
    async def test_spawn_with_error_output(self):
        executor = CodexExecutor()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error msg"))
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            session = await executor.spawn("fail task", cwd="/tmp")
            assert session.exit_code == 1
            assert session.error == "error msg"


# ── Registry ─────────────────────────────────────────────────────

class TestRegistry:

    def test_empty_registry(self):
        reg = ExecutorRegistry()
        assert reg.stats()["count"] == 0

    def test_manual_register(self):
        reg = ExecutorRegistry()
        reg._discovery_done = True  # Prevent auto-discover overwriting
        executor = ClaudeCodeExecutor()
        reg.register(executor)
        assert reg.get("claude-code") is executor

    def test_available_list(self):
        reg = ExecutorRegistry()
        reg._discovery_done = True
        reg.register(ClaudeCodeExecutor())
        reg.register(CodexExecutor())
        available = reg.available()
        assert "claude-code" in available
        assert "codex" in available

    def test_get_nonexistent(self):
        reg = ExecutorRegistry()
        reg._discovery_done = True  # Prevent auto-discover
        assert reg.get("nonexistent") is None

    def test_best_available_with_preference(self):
        reg = ExecutorRegistry()
        reg._discovery_done = True
        reg.register(ClaudeCodeExecutor())
        reg.register(CodexExecutor())
        best = reg.best_available(prefer=["codex", "claude-code"])
        assert best.name == "codex"

    def test_best_available_default_priority(self):
        reg = ExecutorRegistry()
        reg._discovery_done = True
        reg.register(CodexExecutor())
        reg.register(ClaudeCodeExecutor())
        best = reg.best_available()
        assert best.name == "claude-code"  # Higher priority

    def test_best_available_empty(self):
        reg = ExecutorRegistry()
        reg._discovery_done = True
        assert reg.best_available() is None

    def test_stats(self):
        reg = ExecutorRegistry()
        reg._discovery_done = True
        reg.register(ClaudeCodeExecutor())
        stats = reg.stats()
        assert stats["count"] == 1
        assert "claude-code" in stats["available"]

    def test_discover_with_mock(self):
        reg = ExecutorRegistry()
        with patch("shutil.which", return_value="/usr/bin/claude"):
            results = reg.discover()
        # Should find at least claude-code
        assert reg._discovery_done is True


# ── Worktree isolation ───────────────────────────────────────────

class TestWorktreeIsolator:

    @pytest.mark.asyncio
    async def test_create_worktree_success(self, tmp_path):
        """Test worktree creation with a real git repo."""
        # Init a git repo
        proc = await asyncio.create_subprocess_exec(
            "git", "init", str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # Create initial commit
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(tmp_path), "add", ".",
            stdout=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(tmp_path), "commit", "-m", "init",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # Create worktree
        path = await WorktreeIsolator.create_worktree(
            str(tmp_path), "test-branch"
        )
        assert path is not None
        assert "test-branch" in path

        # Cleanup
        await WorktreeIsolator.remove_worktree(str(tmp_path), "test-branch")

    @pytest.mark.asyncio
    async def test_list_worktrees(self, tmp_path):
        # Init repo
        proc = await asyncio.create_subprocess_exec(
            "git", "init", str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        worktrees = await WorktreeIsolator.list_worktrees(str(tmp_path))
        assert isinstance(worktrees, list)

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, tmp_path):
        result = await WorktreeIsolator.remove_worktree(str(tmp_path), "nope")
        assert result is False
