"""Tests for able.core.security.subprocess_runner — standardized subprocess I/O."""

import os

import pytest

from able.core.security.subprocess_runner import (
    BLOCKED_ENV_PREFIXES,
    BLOCKED_ENV_VARS,
    DEFAULT_MAX_OUTPUT,
    SubprocessResult,
    _sanitize_env,
    _truncate,
    async_run,
    run,
)


# ── SubprocessResult ────────────────────────────────────────────


def test_result_success():
    r = SubprocessResult(stdout="ok", stderr="", exit_code=0)
    assert r.success
    assert r.output == "ok"


def test_result_failure():
    r = SubprocessResult(stdout="", stderr="err", exit_code=1)
    assert not r.success
    assert "err" in r.output


def test_result_timeout_is_failure():
    r = SubprocessResult(stdout="", stderr="", exit_code=0, timed_out=True)
    assert not r.success


def test_result_combined_output():
    r = SubprocessResult(stdout="out", stderr="err", exit_code=0)
    assert r.output == "out\nerr"


def test_result_empty_output():
    r = SubprocessResult(stdout="", stderr="", exit_code=0)
    assert r.output == ""


# ── _truncate ───────────────────────────────────────────────────


def test_truncate_short():
    text, trunc = _truncate("hello", 100)
    assert text == "hello"
    assert not trunc


def test_truncate_exact():
    text, trunc = _truncate("hello", 5)
    assert text == "hello"
    assert not trunc


def test_truncate_long():
    text, trunc = _truncate("a" * 200, 100)
    assert trunc
    assert len(text) > 100  # includes marker
    assert "TRUNCATED" in text
    assert "100 bytes omitted" in text


# ── _sanitize_env ───────────────────────────────────────────────


def test_sanitize_strips_ld_preload():
    env = _sanitize_env({"LD_PRELOAD": "/evil.so"})
    assert "LD_PRELOAD" not in env


def test_sanitize_strips_dyld():
    env = _sanitize_env({"DYLD_INSERT_LIBRARIES": "/evil.dylib"})
    assert "DYLD_INSERT_LIBRARIES" not in env


def test_sanitize_strips_java_injection():
    env = _sanitize_env({"JAVA_TOOL_OPTIONS": "-javaagent:evil.jar"})
    assert "JAVA_TOOL_OPTIONS" not in env


def test_sanitize_strips_node_options():
    env = _sanitize_env({"NODE_OPTIONS": "--require evil"})
    assert "NODE_OPTIONS" not in env


def test_sanitize_strips_git_injection():
    env = _sanitize_env({"GIT_SSH_COMMAND": "evil-proxy"})
    assert "GIT_SSH_COMMAND" not in env


def test_sanitize_strips_rust_flags():
    env = _sanitize_env({"RUSTFLAGS": "-C link-arg=-Wl,-z,execstack"})
    assert "RUSTFLAGS" not in env


def test_sanitize_strips_prefix_patterns():
    env = _sanitize_env({"LD_LIBRARY_PATH": "/bad", "DYLD_FRAMEWORK_PATH": "/bad"})
    assert "LD_LIBRARY_PATH" not in env
    assert "DYLD_FRAMEWORK_PATH" not in env


def test_sanitize_preserves_safe_vars():
    env = _sanitize_env({"HOME": "/home/test", "PATH": "/usr/bin"})
    assert env["HOME"] == "/home/test"
    assert env["PATH"] == "/usr/bin"


def test_sanitize_allowlist_overrides():
    env = _sanitize_env(
        {"GIT_SSH_COMMAND": "ssh -i key"},
        env_allowlist=["GIT_SSH_COMMAND"],
    )
    assert env["GIT_SSH_COMMAND"] == "ssh -i key"


def test_sanitize_merges_extra_env():
    env = _sanitize_env({"MY_CUSTOM_VAR": "value"})
    assert env["MY_CUSTOM_VAR"] == "value"


# ── run() sync ──────────────────────────────────────────────────


def test_run_echo():
    result = run(["echo", "hello"])
    assert result.success
    assert "hello" in result.stdout


def test_run_exit_code():
    result = run(["/bin/sh", "-c", "exit 42"])
    assert not result.success
    assert result.exit_code == 42


def test_run_stderr():
    result = run(["/bin/sh", "-c", "echo err >&2"])
    assert "err" in result.stderr


def test_run_timeout():
    result = run(["sleep", "60"], timeout=1)
    assert result.timed_out
    assert not result.success
    assert result.exit_code == -1


def test_run_command_not_found():
    result = run(["nonexistent_binary_xyz"])
    assert not result.success
    assert result.exit_code == 127


def test_run_output_truncation():
    # Generate 100KB of output, cap at 1KB
    result = run(["/bin/sh", "-c", "dd if=/dev/zero bs=1024 count=100 2>/dev/null | tr '\\0' 'x'"],
                 max_output=1024)
    assert result.truncated
    assert "TRUNCATED" in result.stdout


def test_run_env_sanitized():
    """Verify LD_PRELOAD is not passed to child process."""
    result = run(["/bin/sh", "-c", "echo $LD_PRELOAD"],
                 env={"LD_PRELOAD": "/evil.so"})
    assert result.success
    # LD_PRELOAD should have been stripped
    assert "/evil.so" not in result.stdout


def test_run_cwd():
    result = run(["pwd"], cwd="/tmp")
    assert result.success
    assert "/tmp" in result.stdout.strip()


def test_run_stdin():
    result = run(["/bin/sh", "-c", "cat"], stdin_data="hello from stdin")
    assert result.success
    assert "hello from stdin" in result.stdout


# ── async_run() ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_run_echo():
    result = await async_run(["echo", "async hello"])
    assert result.success
    assert "async hello" in result.stdout


@pytest.mark.asyncio
async def test_async_run_exit_code():
    result = await async_run(["/bin/sh", "-c", "exit 7"])
    assert not result.success
    assert result.exit_code == 7


@pytest.mark.asyncio
async def test_async_run_timeout():
    result = await async_run(["sleep", "60"], timeout=1)
    assert result.timed_out
    assert not result.success


@pytest.mark.asyncio
async def test_async_run_env_sanitized():
    result = await async_run(
        ["/bin/sh", "-c", "echo $JAVA_TOOL_OPTIONS"],
        env={"JAVA_TOOL_OPTIONS": "evil"},
    )
    assert "evil" not in result.stdout


@pytest.mark.asyncio
async def test_async_run_command_not_found():
    result = await async_run(["nonexistent_binary_xyz"])
    assert not result.success
    assert result.exit_code == 127


@pytest.mark.asyncio
async def test_async_run_output_truncation():
    result = await async_run(
        ["/bin/sh", "-c", "dd if=/dev/zero bs=1024 count=100 2>/dev/null | tr '\\0' 'x'"],
        max_output=1024,
    )
    assert result.truncated
    assert "TRUNCATED" in result.stdout


@pytest.mark.asyncio
async def test_async_run_stdin():
    result = await async_run(
        ["/bin/sh", "-c", "cat"],
        stdin_data="async stdin data",
    )
    assert result.success
    assert "async stdin data" in result.stdout
