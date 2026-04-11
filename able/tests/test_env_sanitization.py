"""Tests for A6 — Environment Variable Sanitization.

Verifies that both subprocess_runner and secure_shell block injection vectors
for Java, Rust, Git, K8s, Node, and linker env vars.
"""

import os
import pytest
from unittest.mock import patch

from able.core.security.subprocess_runner import (
    BLOCKED_ENV_VARS,
    BLOCKED_ENV_PREFIXES,
    _sanitize_env,
)


# ── BLOCKED_ENV_VARS coverage ─────────────────────────────────────

class TestBlockedEnvVars:
    """Verify the blocklist covers all required injection vectors."""

    def test_linker_injection_blocked(self):
        assert "LD_PRELOAD" in BLOCKED_ENV_VARS
        assert "LD_LIBRARY_PATH" in BLOCKED_ENV_VARS
        assert "DYLD_INSERT_LIBRARIES" in BLOCKED_ENV_VARS
        assert "DYLD_LIBRARY_PATH" in BLOCKED_ENV_VARS
        assert "DYLD_FRAMEWORK_PATH" in BLOCKED_ENV_VARS

    def test_python_injection_blocked(self):
        assert "PYTHONPATH" in BLOCKED_ENV_VARS
        assert "PYTHONSTARTUP" in BLOCKED_ENV_VARS

    def test_java_injection_blocked(self):
        assert "JAVA_TOOL_OPTIONS" in BLOCKED_ENV_VARS
        assert "_JAVA_OPTIONS" in BLOCKED_ENV_VARS
        assert "JDK_JAVA_OPTIONS" in BLOCKED_ENV_VARS

    def test_rust_injection_blocked(self):
        assert "RUSTFLAGS" in BLOCKED_ENV_VARS
        assert "RUSTDOCFLAGS" in BLOCKED_ENV_VARS

    def test_git_injection_blocked(self):
        assert "GIT_PROXY_COMMAND" in BLOCKED_ENV_VARS
        assert "GIT_SSH_COMMAND" in BLOCKED_ENV_VARS

    def test_k8s_blocked(self):
        assert "KUBECONFIG" in BLOCKED_ENV_VARS

    def test_node_injection_blocked(self):
        assert "NODE_OPTIONS" in BLOCKED_ENV_VARS

    def test_prefix_patterns(self):
        assert "LD_" in BLOCKED_ENV_PREFIXES
        assert "DYLD_" in BLOCKED_ENV_PREFIXES


# ── _sanitize_env ─────────────────────────────────────────────────

class TestSanitizeEnv:
    """Test the env sanitization function."""

    def test_strips_blocked_var(self):
        env = {"JAVA_TOOL_OPTIONS": "-javaagent:evil.jar", "HOME": "/home/user"}
        result = _sanitize_env(env)
        assert "JAVA_TOOL_OPTIONS" not in result
        assert "HOME" in result

    def test_strips_ld_prefix(self):
        env = {"LD_PRELOAD": "/tmp/evil.so", "PATH": "/usr/bin"}
        result = _sanitize_env(env)
        assert "LD_PRELOAD" not in result
        assert "PATH" in result

    def test_strips_dyld_prefix(self):
        env = {"DYLD_INSERT_LIBRARIES": "/tmp/evil.dylib"}
        result = _sanitize_env(env)
        assert "DYLD_INSERT_LIBRARIES" not in result

    def test_strips_custom_ld_variant(self):
        env = {"LD_AUDIT": "/tmp/audit.so"}
        result = _sanitize_env(env)
        assert "LD_AUDIT" not in result

    def test_allowlist_overrides_block(self):
        env = {"GIT_SSH_COMMAND": "ssh -i ~/.ssh/deploy_key"}
        result = _sanitize_env(env, env_allowlist=["GIT_SSH_COMMAND"])
        assert "GIT_SSH_COMMAND" in result

    def test_allowlist_only_unblocks_specified(self):
        env = {"GIT_SSH_COMMAND": "ssh", "JAVA_TOOL_OPTIONS": "evil"}
        result = _sanitize_env(env, env_allowlist=["GIT_SSH_COMMAND"])
        assert "GIT_SSH_COMMAND" in result
        assert "JAVA_TOOL_OPTIONS" not in result

    def test_clean_env_unchanged(self):
        env = {"HOME": "/home/user", "EDITOR": "vim", "SHELL": "/bin/zsh"}
        result = _sanitize_env(env)
        for k in env:
            assert k in result

    def test_multiple_blocked_vars_all_stripped(self):
        env = {
            "JAVA_TOOL_OPTIONS": "x",
            "RUSTFLAGS": "y",
            "NODE_OPTIONS": "z",
            "KUBECONFIG": "/tmp/k",
            "PATH": "/usr/bin",
        }
        result = _sanitize_env(env)
        assert "JAVA_TOOL_OPTIONS" not in result
        assert "RUSTFLAGS" not in result
        assert "NODE_OPTIONS" not in result
        assert "KUBECONFIG" not in result
        assert "PATH" in result

    def test_none_env_uses_os_environ(self):
        result = _sanitize_env(None)
        # Should be based on os.environ minus blocked vars
        assert isinstance(result, dict)
        assert "JAVA_TOOL_OPTIONS" not in result  # Stripped even if in os.environ


# ── SecureShell env integration ───────────────────────────────────

class TestSecureShellEnvIntegration:
    """Verify secure_shell imports and uses the comprehensive blocklist."""

    def test_import_blocked_vars(self):
        """Verify secure_shell has access to the full blocklist."""
        from able.tools.shell.secure_shell import BLOCKED_ENV_VARS as shell_blocked
        # Should be the same frozenset imported from subprocess_runner
        assert "JAVA_TOOL_OPTIONS" in shell_blocked
        assert "RUSTFLAGS" in shell_blocked
        assert "GIT_PROXY_COMMAND" in shell_blocked
        assert "KUBECONFIG" in shell_blocked
        assert "NODE_OPTIONS" in shell_blocked

    def test_import_blocked_prefixes(self):
        from able.tools.shell.secure_shell import BLOCKED_ENV_PREFIXES as shell_prefixes
        assert "LD_" in shell_prefixes
        assert "DYLD_" in shell_prefixes
