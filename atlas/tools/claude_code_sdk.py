"""
Claude Code SDK Integration — Uses Claude Max subscription for agent tasks.

Wraps the `claude` CLI for ATLAS automation tasks:
- Research with web browsing (computer use)
- Deep analysis tasks
- Code review and improvement suggestions

Uses the flat-rate Max subscription ($100/mo) — zero marginal cost per request.
Requires `claude` CLI installed and authenticated.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ClaudeCodeResult:
    """Result from a Claude Code CLI invocation."""
    content: str = ""
    success: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    session_id: str = ""
    error: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class ClaudeCodeSDK:
    """
    Wraps the Claude Code CLI for programmatic use.

    This is NOT the Anthropic API — it uses the `claude` CLI binary
    which authenticates via your Claude Max subscription (OAuth).
    Zero API cost per invocation.

    Usage:
        sdk = ClaudeCodeSDK()
        result = await sdk.research("What are the latest Qwen model releases?")
        result = await sdk.analyze("Review this code for security issues", context=code)
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        timeout: float = 300.0,
        max_turns: int = 5,
    ):
        self.model = model
        self.timeout = timeout
        self.max_turns = max_turns
        self._cli_path = self._find_cli()

    def _find_cli(self) -> str:
        """Locate the claude CLI binary."""
        # Check common paths
        for path in [
            os.path.expanduser("~/.local/bin/claude"),
            "/usr/local/bin/claude",
            "/opt/homebrew/bin/claude",
        ]:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path

        # Fall back to PATH
        import shutil
        found = shutil.which("claude")
        if found:
            return found

        logger.warning("Claude CLI not found — install via: npm install -g @anthropic-ai/claude-code")
        return "claude"  # Hope it's on PATH

    async def prompt(
        self,
        prompt: str,
        system_prompt: str = "",
        allowed_tools: List[str] = None,
        cwd: str = None,
    ) -> ClaudeCodeResult:
        """
        Run a single prompt through Claude Code CLI.

        Args:
            prompt: The user prompt
            system_prompt: Optional system prompt override
            allowed_tools: List of tool patterns to allow (e.g., ["WebSearch", "Read"])
            cwd: Working directory for the CLI
        """
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--output-format", "json",
            "--model", self.model,
            "--max-turns", str(self.max_turns),
        ]

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        if allowed_tools:
            for tool in allowed_tools:
                cmd.extend(["--allowedTools", tool])

        # Remove ANTHROPIC_API_KEY to force OAuth (Max subscription)
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                limit=1024 * 1024 * 10,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )

            if process.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                return ClaudeCodeResult(
                    success=False,
                    error=f"CLI exit {process.returncode}: {err[:500]}",
                )

            # Parse JSON output (take last valid JSON object)
            out_text = stdout.decode("utf-8", errors="replace").strip()
            parsed = None
            for line in reversed(out_text.splitlines()):
                try:
                    parsed = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

            if not parsed:
                return ClaudeCodeResult(
                    success=False,
                    error="Unparseable CLI output",
                    content=out_text[:1000],
                )

            if parsed.get("is_error"):
                return ClaudeCodeResult(
                    success=False,
                    error=parsed.get("result", "Unknown error"),
                    raw=parsed,
                )

            usage = parsed.get("usage", {})
            return ClaudeCodeResult(
                content=str(parsed.get("result", "")),
                success=True,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                model=parsed.get("model", self.model),
                session_id=parsed.get("session_id", ""),
                raw=parsed,
            )

        except asyncio.TimeoutError:
            return ClaudeCodeResult(
                success=False,
                error=f"Timeout after {self.timeout}s",
            )
        except FileNotFoundError:
            return ClaudeCodeResult(
                success=False,
                error="Claude CLI not found — install: npm install -g @anthropic-ai/claude-code",
            )
        except Exception as e:
            return ClaudeCodeResult(
                success=False,
                error=str(e),
            )

    async def research(
        self,
        query: str,
        deep: bool = False,
    ) -> ClaudeCodeResult:
        """
        Research a topic using Claude Code with web access.

        Uses Claude's built-in web search and fetch capabilities
        via the Max subscription.

        Args:
            query: Research question
            deep: If True, uses more turns and deeper analysis
        """
        system = (
            "You are a research assistant for ATLAS, an autonomous AI agent system. "
            "Search the web for the latest information on the given topic. "
            "Focus on: recent releases, patches, updates, breaking changes, "
            "new techniques, and actionable improvements. "
            "Be specific — include version numbers, dates, and URLs. "
            "Format as a concise research brief with key findings."
        )

        max_turns = 10 if deep else 5
        old_max = self.max_turns
        self.max_turns = max_turns

        result = await self.prompt(
            prompt=query,
            system_prompt=system,
            allowed_tools=["WebSearch", "WebFetch", "Read"],
        )

        self.max_turns = old_max
        return result

    async def analyze_for_improvements(
        self,
        topic: str,
        context: str = "",
    ) -> ClaudeCodeResult:
        """
        Analyze a topic specifically for ATLAS improvements.

        Args:
            topic: What to analyze
            context: Current ATLAS state/context to inform the analysis
        """
        prompt = (
            f"Research and analyze: {topic}\n\n"
            f"Context about our system (ATLAS):\n{context}\n\n"
            "Provide:\n"
            "1. What's new or changed\n"
            "2. How ATLAS could benefit from this\n"
            "3. Specific implementation suggestions\n"
            "4. Any breaking changes or risks\n"
            "5. Priority: high/medium/low"
        )

        return await self.prompt(
            prompt=prompt,
            allowed_tools=["WebSearch", "WebFetch", "Read"],
        )

    async def browse_and_extract(
        self,
        url: str,
        extract_instructions: str = "Summarize the key content",
    ) -> ClaudeCodeResult:
        """
        Browse a URL and extract information.

        Args:
            url: URL to browse
            extract_instructions: What to extract from the page
        """
        prompt = (
            f"Fetch and read the content at: {url}\n\n"
            f"Then: {extract_instructions}\n\n"
            "If the URL is inaccessible, search the web for the same information."
        )

        return await self.prompt(
            prompt=prompt,
            allowed_tools=["WebFetch", "WebSearch", "Read"],
        )

    @staticmethod
    def is_available() -> bool:
        """Check if Claude Code CLI is installed and accessible."""
        import shutil
        return shutil.which("claude") is not None
