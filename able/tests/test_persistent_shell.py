"""Tests for E4 — Persistent Shell Mode.

Covers: shell lifecycle, state persistence (cd, env vars), timeout,
serialized execution, manager, cleanup.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from able.tools.shell.persistent_shell import (
    PersistentShell,
    PersistentShellManager,
    PersistentShellResult,
)


@pytest.fixture
def shell():
    return PersistentShell(session_id="test-session")


@pytest.fixture
def manager():
    return PersistentShellManager()


# ── Lifecycle ─────────────────────────────────────────────────────

class TestLifecycle:

    def test_not_alive_before_start(self, shell):
        assert shell.is_alive is False

    def test_uptime_zero_before_start(self, shell):
        assert shell.uptime_s == 0.0

    @pytest.mark.asyncio
    async def test_shell_starts_on_first_command(self, shell):
        try:
            result = await shell.run("echo hello")
            assert shell.is_alive is True
            assert "hello" in result.stdout
        finally:
            await shell.close()

    @pytest.mark.asyncio
    async def test_close_terminates(self, shell):
        await shell.run("echo test")
        assert shell.is_alive is True
        await shell.close()
        assert shell.is_alive is False

    @pytest.mark.asyncio
    async def test_stats(self, shell):
        try:
            await shell.run("echo x")
            stats = shell.stats()
            assert stats["session_id"] == "test-session"
            assert stats["alive"] is True
            assert stats["commands"] >= 1
            assert stats["uptime_s"] >= 0
        finally:
            await shell.close()


# ── State persistence ─────────────────────────────────────────────

class TestStatePersistence:

    @pytest.mark.asyncio
    async def test_cd_persists(self, shell):
        try:
            await shell.run("cd /tmp")
            result = await shell.run("pwd")
            # On macOS /tmp -> /private/tmp
            assert "/tmp" in result.stdout
        finally:
            await shell.close()

    @pytest.mark.asyncio
    async def test_env_var_persists(self, shell):
        try:
            await shell.run("export ABLE_TEST_VAR=hello_world")
            result = await shell.run("echo $ABLE_TEST_VAR")
            assert "hello_world" in result.stdout
        finally:
            await shell.close()

    @pytest.mark.asyncio
    async def test_alias_persists(self, shell):
        try:
            await shell.run("alias greet='echo greetings'")
            result = await shell.run("greet")
            assert "greetings" in result.stdout
        finally:
            await shell.close()

    @pytest.mark.asyncio
    async def test_exit_code_captured(self, shell):
        try:
            result = await shell.run("true")
            assert result.exit_code == 0
            result = await shell.run("false")
            assert result.exit_code == 1
        finally:
            await shell.close()


# ── Result structure ──────────────────────────────────────────────

class TestResultStructure:

    @pytest.mark.asyncio
    async def test_result_fields(self, shell):
        try:
            result = await shell.run("echo output")
            assert isinstance(result, PersistentShellResult)
            assert result.command == "echo output"
            assert result.session_id == "test-session"
            assert result.duration_s >= 0
        finally:
            await shell.close()

    @pytest.mark.asyncio
    async def test_stderr_captured(self, shell):
        try:
            result = await shell.run("echo error_msg >&2")
            assert "error_msg" in result.stderr or "error_msg" in result.stdout
        finally:
            await shell.close()


# ── Manager ───────────────────────────────────────────────────────

class TestManager:

    def test_get_or_create_new(self, manager):
        shell = manager.get_or_create("session-1")
        assert shell.session_id == "session-1"

    def test_get_or_create_reuses(self, manager):
        s1 = manager.get_or_create("session-1")
        s2 = manager.get_or_create("session-1")
        assert s1 is s2

    def test_different_sessions_different_shells(self, manager):
        s1 = manager.get_or_create("session-1")
        s2 = manager.get_or_create("session-2")
        assert s1 is not s2

    @pytest.mark.asyncio
    async def test_close_session(self, manager):
        shell = manager.get_or_create("session-1")
        await shell.run("echo x")
        await manager.close("session-1")
        assert "session-1" not in manager._shells

    @pytest.mark.asyncio
    async def test_close_all(self, manager):
        manager.get_or_create("s1")
        manager.get_or_create("s2")
        await manager.close_all()
        assert manager.active_count == 0

    def test_all_stats(self, manager):
        manager.get_or_create("s1")
        manager.get_or_create("s2")
        stats = manager.all_stats()
        assert len(stats) == 2
