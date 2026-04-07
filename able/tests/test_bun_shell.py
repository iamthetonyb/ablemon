"""Tests for BunShell backend — TypeScript + shell execution via Bun runtime."""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from able.tools.shell.bun_shell import BunShell, ShellResult


def test_available_returns_bool():
    result = BunShell.available()
    assert isinstance(result, bool)


@patch("shutil.which", return_value=None)
def test_unavailable_when_bun_missing(mock_which):
    assert BunShell.available() is False


@patch("shutil.which", return_value="/usr/local/bin/bun")
def test_available_when_bun_installed(mock_which):
    assert BunShell.available() is True


@pytest.mark.asyncio
@patch("shutil.which", return_value=None)
async def test_run_graceful_fallback_when_unavailable(mock_which):
    result = await BunShell.run("console.log('hi')")
    assert result.exit_code == 1
    assert "not installed" in result.stderr


@pytest.mark.asyncio
@patch("shutil.which", return_value="/usr/local/bin/bun")
async def test_ts_mode_wraps_correctly(mock_which):
    """Verify that ts mode passes script as-is to bun run -e."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"hello\n", b""))
    mock_proc.returncode = 0
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result = await BunShell.run("console.log('hello')", mode="ts")
        assert result.stdout == "hello\n"
        assert result.exit_code == 0
        # Verify bun was called with run -e
        call_args = mock_exec.call_args[0]
        assert call_args[0] == "bun"
        assert call_args[1] == "run"
        assert call_args[2] == "-e"


@pytest.mark.asyncio
@patch("shutil.which", return_value="/usr/local/bin/bun")
async def test_shell_mode_wraps_with_dollar(mock_which):
    """Shell mode should wrap script with Bun's $ import."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await BunShell.run("echo hi", mode="shell")
        script = mock_exec.call_args[0][3]
        assert '{ $ }' in script
        assert "echo hi" in script
