"""Tests for able.core.security.arg_sanitizer — tool argument validation."""

import pytest
from able.core.security.arg_sanitizer import (
    sanitize_tool_args,
    SanitizeResult,
    ToolArgRejected,
)


# ── Clean passthrough ─────────────────────────────────────────────

def test_clean_args_pass_through():
    result = sanitize_tool_args("web_search", {"query": "python tutorial"})
    assert result.args == {"query": "python tutorial"}
    assert not result.blocked
    assert not result.warnings


def test_empty_args():
    result = sanitize_tool_args("some_tool", {})
    assert result.args == {}
    assert not result.blocked


def test_non_string_values_pass():
    result = sanitize_tool_args("some_tool", {"count": 5, "flag": True})
    assert result.args == {"count": 5, "flag": True}
    assert not result.blocked


# ── Null bytes ────────────────────────────────────────────────────

def test_null_byte_blocked():
    with pytest.raises(ToolArgRejected) as exc:
        sanitize_tool_args("some_tool", {"input": "hello\x00world"})
    assert "null byte" in str(exc.value)


def test_url_encoded_null_blocked():
    with pytest.raises(ToolArgRejected):
        sanitize_tool_args("some_tool", {"input": "hello%00world"})


# ── Path traversal ────────────────────────────────────────────────

def test_path_traversal_in_file_tool():
    with pytest.raises(ToolArgRejected) as exc:
        sanitize_tool_args("read_file", {"path": "../../etc/passwd"})
    assert "path traversal" in str(exc.value)


def test_path_traversal_url_encoded():
    with pytest.raises(ToolArgRejected):
        sanitize_tool_args("write_file", {"path": "..%2fetc%2fpasswd"})


def test_path_traversal_in_path_named_field():
    with pytest.raises(ToolArgRejected):
        sanitize_tool_args("custom_tool", {"file_path": "../../../secret"})


def test_safe_relative_path_allowed():
    result = sanitize_tool_args("read_file", {"path": "src/main.py"})
    assert not result.blocked


def test_single_dot_path_allowed():
    result = sanitize_tool_args("read_file", {"path": "./src/main.py"})
    assert not result.blocked


# ── Shell metacharacters ──────────────────────────────────────────

def test_shell_meta_blocked_in_file_tool():
    with pytest.raises(ToolArgRejected):
        sanitize_tool_args("write_file", {"path": "file; rm -rf /"})


def test_shell_meta_allowed_in_shell_tool():
    result = sanitize_tool_args("shell_execute", {"command": "ls | grep py"})
    assert not result.blocked


def test_shell_meta_warns_in_other_tool():
    result = sanitize_tool_args("web_search", {"query": "test; echo hacked"})
    assert len(result.warnings) > 0
    assert not result.blocked


# ── URL / SSRF checks ────────────────────────────────────────────

def test_metadata_endpoint_blocked():
    with pytest.raises(ToolArgRejected) as exc:
        sanitize_tool_args("web_fetch", {"url": "http://169.254.169.254/latest/meta-data/"})
    assert "metadata" in str(exc.value)


def test_google_metadata_blocked():
    with pytest.raises(ToolArgRejected):
        sanitize_tool_args("web_fetch", {"url": "http://metadata.google.internal/computeMetadata/v1/"})


def test_normal_url_allowed():
    result = sanitize_tool_args("web_fetch", {"url": "https://example.com/api"})
    assert not result.blocked


def test_private_ip_warns():
    result = sanitize_tool_args("web_fetch", {"url": "http://192.168.1.1/admin"})
    assert any("private" in w for w in result.warnings)


# ── Secret detection ──────────────────────────────────────────────

def test_api_key_warns():
    result = sanitize_tool_args("some_tool", {"token": "sk-abcdefghijklmnopqrstuvwxyz1234"})
    assert any("secret" in w.lower() or "api key" in w.lower() for w in result.warnings)


def test_github_token_warns():
    result = sanitize_tool_args("some_tool", {"auth": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"})
    assert any("secret" in w.lower() or "api key" in w.lower() for w in result.warnings)


# ── Control character stripping ───────────────────────────────────

def test_control_chars_stripped():
    result = sanitize_tool_args("some_tool", {"text": "hello\x08\x7fworld"})
    assert result.args["text"] == "helloworld"


def test_newline_and_tab_preserved():
    result = sanitize_tool_args("some_tool", {"text": "line1\nline2\ttab"})
    assert result.args["text"] == "line1\nline2\ttab"


# ── Nested dicts ──────────────────────────────────────────────────

def test_nested_dict_path_traversal():
    with pytest.raises(ToolArgRejected):
        sanitize_tool_args("github_push_files", {
            "repo": "test",
            "files": {"../../evil.py": "import os; os.system('rm -rf /')"},
        })


def test_nested_dict_clean():
    result = sanitize_tool_args("github_push_files", {
        "repo": "myrepo",
        "files": {"src/main.py": "print('hello')"},
    })
    assert not result.blocked
