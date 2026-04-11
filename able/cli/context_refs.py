"""
D14 — @file/@url Context References.

Expands @path/to/file and @https://url references in user messages
by injecting the referenced content inline.

Forked from Hermes v0.4 PR #2343 pattern.

Usage:
    from able.cli.context_refs import expand_references
    expanded = await expand_references("Check @src/main.py for bugs")
    # expanded = "Check [File: src/main.py]\\n```\\n<file contents>\\n```\\n for bugs"

Integration:
    Wire into able/cli/chat.py input processing — before sending to gateway,
    expand any @references in the user's message.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Max file size to inject (in characters)
MAX_FILE_CHARS = 50_000
# Max URL content to inject
MAX_URL_CHARS = 20_000
# Max references per message
MAX_REFS_PER_MESSAGE = 5

# Patterns for @references
_FILE_PATTERN = re.compile(
    r"@((?:[a-zA-Z]:)?(?:[./~]|[a-zA-Z0-9_-]+/)[^\s@,;:!?)\"']*)"
)
_URL_PATTERN = re.compile(
    r"@(https?://[^\s@,;:!?)\"']+)"
)


@dataclass
class ResolvedRef:
    """A resolved @reference."""
    raw: str           # Original text (e.g., "@src/main.py")
    ref_type: str      # "file" or "url"
    path: str          # Resolved path or URL
    content: str       # Injected content
    truncated: bool = False
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class ExpansionResult:
    """Result of expanding @references in a message."""
    original: str
    expanded: str
    refs: List[ResolvedRef] = field(default_factory=list)
    skipped: int = 0  # References skipped (over limit)


def find_references(text: str) -> List[Tuple[str, str, str]]:
    """Find all @references in text.

    Returns list of (raw_match, ref_type, value) tuples.
    URLs are matched before files to avoid ambiguity.
    """
    refs = []
    seen = set()

    # URLs first
    for m in _URL_PATTERN.finditer(text):
        raw = m.group(0)
        if raw not in seen:
            refs.append((raw, "url", m.group(1)))
            seen.add(raw)

    # Files
    for m in _FILE_PATTERN.finditer(text):
        raw = m.group(0)
        # Skip if it was already matched as URL
        if raw in seen:
            continue
        # Skip common false positives
        value = m.group(1)
        if value.startswith("http"):
            continue
        refs.append((raw, "file", value))
        seen.add(raw)

    return refs


def resolve_file_ref(
    path_str: str,
    workspace: Optional[Path] = None,
    max_chars: int = MAX_FILE_CHARS,
) -> ResolvedRef:
    """Resolve a @file reference by reading the file.

    Security: blocks reads outside workspace (no ../ escape).
    """
    raw = f"@{path_str}"

    # Expand ~ and resolve
    try:
        if path_str.startswith("~"):
            resolved = Path(path_str).expanduser().resolve()
        elif path_str.startswith("/"):
            resolved = Path(path_str).resolve()
        else:
            base = workspace or Path.cwd()
            resolved = (base / path_str).resolve()
    except Exception as e:
        return ResolvedRef(raw=raw, ref_type="file", path=path_str,
                          content="", error=f"Invalid path: {e}")

    # Security: block traversal outside workspace
    if workspace:
        try:
            resolved.relative_to(workspace.resolve())
        except ValueError:
            return ResolvedRef(raw=raw, ref_type="file", path=str(resolved),
                              content="", error="Path outside workspace")

    # Block obvious secrets
    name_lower = resolved.name.lower()
    if name_lower in (".env", ".secrets", "credentials.json", "token.json"):
        return ResolvedRef(raw=raw, ref_type="file", path=str(resolved),
                          content="", error="Blocked: potential secrets file")

    if not resolved.exists():
        return ResolvedRef(raw=raw, ref_type="file", path=str(resolved),
                          content="", error="File not found")

    if not resolved.is_file():
        return ResolvedRef(raw=raw, ref_type="file", path=str(resolved),
                          content="", error="Not a file")

    try:
        content = resolved.read_text(errors="replace")
        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars] + "\n[truncated]"
            truncated = True

        return ResolvedRef(
            raw=raw,
            ref_type="file",
            path=str(resolved),
            content=content,
            truncated=truncated,
        )
    except Exception as e:
        return ResolvedRef(raw=raw, ref_type="file", path=str(resolved),
                          content="", error=f"Read error: {e}")


async def resolve_url_ref(
    url: str,
    max_chars: int = MAX_URL_CHARS,
) -> ResolvedRef:
    """Resolve a @url reference by fetching the URL content."""
    raw = f"@{url}"

    try:
        import httpx
    except ImportError:
        return ResolvedRef(raw=raw, ref_type="url", path=url,
                          content="", error="httpx not installed")

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "ABLE/0.4.8 (context-ref)",
            })
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "text" not in content_type and "json" not in content_type:
                return ResolvedRef(raw=raw, ref_type="url", path=url,
                                  content="", error=f"Non-text content: {content_type}")

            content = resp.text
            truncated = False
            if len(content) > max_chars:
                content = content[:max_chars] + "\n[truncated]"
                truncated = True

            return ResolvedRef(
                raw=raw,
                ref_type="url",
                path=url,
                content=content,
                truncated=truncated,
            )
    except Exception as e:
        return ResolvedRef(raw=raw, ref_type="url", path=url,
                          content="", error=str(e))


async def expand_references(
    text: str,
    workspace: Optional[Path] = None,
    max_refs: int = MAX_REFS_PER_MESSAGE,
) -> ExpansionResult:
    """Expand all @references in a message.

    Replaces @path and @url with inline content blocks.
    Respects max_refs limit to prevent abuse.
    """
    refs = find_references(text)
    result = ExpansionResult(original=text, expanded=text)

    if not refs:
        return result

    # Limit references
    if len(refs) > max_refs:
        result.skipped = len(refs) - max_refs
        refs = refs[:max_refs]

    for raw, ref_type, value in refs:
        if ref_type == "file":
            resolved = resolve_file_ref(value, workspace)
        else:
            resolved = await resolve_url_ref(value)

        result.refs.append(resolved)

        if resolved.success:
            # Replace the @reference with injected content
            ext = Path(value).suffix.lstrip(".") if ref_type == "file" else ""
            lang = ext if ext in ("py", "js", "ts", "yaml", "json", "md", "sh", "sql") else ""
            injection = (
                f"\n[{ref_type.title()}: {value}]\n"
                f"```{lang}\n{resolved.content}\n```\n"
            )
            result.expanded = result.expanded.replace(raw, injection, 1)
        else:
            # Replace with error note
            result.expanded = result.expanded.replace(
                raw, f"[{raw}: {resolved.error}]", 1
            )

    return result
