"""
Tool Argument Sanitizer — validates tool call arguments before dispatch.

TrustGate scans user messages for injection patterns, but tool arguments
bypass it entirely.  This module closes that gap: it runs inside
tool_registry.dispatch() *before* the handler executes.

Per-tool-type rules:
- Shell commands → CommandGuard treatment
- URLs → egress inspection
- File paths → traversal / null-byte checks
- All args → embedded newline / control character stripping
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Dangerous patterns ─────────────────────────────────────────────

# Path traversal: ../../etc/passwd, ..%2f, ..%5c
_TRAVERSAL_RE = re.compile(r"(^|[\\/])\.\.($|[\\/])|\.\.(%2[fF]|%5[cC])")

# Null bytes can truncate filenames at the C level
_NULL_BYTE_RE = re.compile(r"\x00|%00")

# Shell metacharacters that could escape a quoted argument.
# Only checked for tools that don't expect shell syntax.
_SHELL_META_RE = re.compile(r"[;|&`$(){}]|(?<!\w)>\s|(?<!\w)<\s")

# Control characters (C0 except \n \r \t)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Common secret-looking prefixes that should never appear in tool args
_SECRET_PREFIX_RE = re.compile(
    r"^(sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}|xoxb-|AKIA[0-9A-Z]{16})",
)

# Cloud metadata endpoints (SSRF targets)
_METADATA_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
    "100.100.100.200",  # Alibaba
})

# Tools whose arguments ARE expected to contain shell syntax
_SHELL_TOOLS = frozenset({
    "shell_execute", "secure_shell", "bash", "run_command",
    "shell_command", "execute_command",
})

# Tools whose arguments contain file paths
_FILE_TOOLS = frozenset({
    "read_file", "write_file", "edit_file", "file_read", "file_write",
    "github_push_files", "vercel_deploy",
})

# Tools whose arguments contain URLs
_URL_TOOLS = frozenset({
    "web_search", "web_fetch", "web_scrape", "fetch_url",
    "github_pages_deploy",
})


@dataclass
class SanitizeResult:
    """Result of argument sanitization."""
    args: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)
    blocked_fields: List[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return len(self.blocked_fields) > 0


class ToolArgRejected(Exception):
    """Raised when tool arguments fail sanitization."""
    def __init__(self, tool_name: str, fields: List[str], reasons: List[str]):
        self.tool_name = tool_name
        self.fields = fields
        self.reasons = reasons
        super().__init__(
            f"Tool '{tool_name}' args rejected — "
            f"fields {fields}: {'; '.join(reasons)}"
        )


def sanitize_tool_args(tool_name: str, args: Dict[str, Any]) -> SanitizeResult:
    """
    Validate and sanitize tool call arguments.

    Returns SanitizeResult with cleaned args, warnings, and any blocked fields.
    Raises ToolArgRejected if critical violations found.
    """
    warnings: List[str] = []
    blocked: List[str] = []
    reasons: List[str] = []
    cleaned = dict(args)  # shallow copy — we only modify string values

    for key, value in args.items():
        if not isinstance(value, str):
            # Recurse into nested dicts — but only check KEYS (paths),
            # not values (file content naturally has shell syntax).
            if isinstance(value, dict):
                for sub_key in value:
                    if isinstance(sub_key, str):
                        _check_string_value(
                            tool_name, f"{key}.{sub_key}", sub_key,
                            warnings, blocked, reasons,
                        )
            continue

        _check_string_value(tool_name, key, value, warnings, blocked, reasons)

    # Strip control characters from all string values
    cleaned = _strip_control_chars(cleaned)

    if blocked:
        raise ToolArgRejected(tool_name, blocked, reasons)

    result = SanitizeResult(args=cleaned, warnings=warnings, blocked_fields=[])
    if warnings:
        logger.warning(
            "Tool arg warnings for %s: %s", tool_name, "; ".join(warnings)
        )
    return result


def _check_string_value(
    tool_name: str,
    field: str,
    value: str,
    warnings: List[str],
    blocked: List[str],
    reasons: List[str],
) -> None:
    """Check a single string value for dangerous patterns."""

    # ── Universal checks (all tools) ──────────────────────────────

    # Null bytes — always blocked
    if _NULL_BYTE_RE.search(value):
        blocked.append(field)
        reasons.append(f"null byte in '{field}'")
        return

    # Secret leakage — warn (don't block, could be intentional)
    if _SECRET_PREFIX_RE.match(value):
        warnings.append(f"possible secret in '{field}' (starts with API key prefix)")

    # ── Path traversal (file tools + any field named *path*) ──────
    if tool_name in _FILE_TOOLS or "path" in field.lower() or "file" in field.lower():
        if _TRAVERSAL_RE.search(value):
            blocked.append(field)
            reasons.append(f"path traversal in '{field}'")
            return

    # ── Shell metacharacters (non-shell tools only) ───────────────
    if tool_name not in _SHELL_TOOLS:
        if _SHELL_META_RE.search(value):
            # For URL/file tools, block.  For other tools, warn.
            if tool_name in _FILE_TOOLS:
                blocked.append(field)
                reasons.append(f"shell metacharacter in file tool '{field}'")
                return
            else:
                warnings.append(
                    f"shell metacharacter in '{field}' for non-shell tool {tool_name}"
                )

    # ── URL checks (URL tools + any field named *url*) ────────────
    if tool_name in _URL_TOOLS or "url" in field.lower():
        _check_url_value(field, value, warnings, blocked, reasons)


def _check_url_value(
    field: str,
    value: str,
    warnings: List[str],
    blocked: List[str],
    reasons: List[str],
) -> None:
    """Validate URL arguments for SSRF patterns."""
    # Only check values that look like URLs
    if not value.startswith(("http://", "https://", "ftp://")):
        return

    try:
        from urllib.parse import urlparse
        parsed = urlparse(value)
        host = parsed.hostname or ""

        # Block cloud metadata endpoints
        if host in _METADATA_HOSTS:
            blocked.append(field)
            reasons.append(f"cloud metadata endpoint in '{field}': {host}")
            return

        # Block private IPs in URLs (basic check — egress_inspector does full)
        if host:
            import ipaddress
            try:
                addr = ipaddress.ip_address(host)
                if addr.is_private or addr.is_loopback or addr.is_link_local:
                    warnings.append(f"private/loopback IP in URL '{field}': {host}")
            except ValueError:
                pass  # Not an IP — that's fine (it's a hostname)

    except Exception:
        pass  # Malformed URL — let the tool handle the error


def _strip_control_chars(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove C0 control characters (except \\n \\r \\t) from string values."""
    cleaned = {}
    for key, value in args.items():
        if isinstance(value, str):
            cleaned[key] = _CONTROL_CHAR_RE.sub("", value)
        elif isinstance(value, dict):
            cleaned[key] = _strip_control_chars(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _CONTROL_CHAR_RE.sub("", v) if isinstance(v, str) else v
                for v in value
            ]
        else:
            cleaned[key] = value
    return cleaned
