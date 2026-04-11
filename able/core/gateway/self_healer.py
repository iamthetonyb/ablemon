"""
D18 — Self-Healing Execution Loop.

Post-tool-call validator that compares tool output against expected
patterns. On mismatch: injects corrective context and retries
(max 1 retry per tool call).

Inspired by InsForge's validate→inspect→adjust closed-loop pattern.

Usage:
    healer = SelfHealer(tool_schemas=schema_registry)
    verdict = healer.validate(tool_name="web_search", output="", args={"query": "test"})
    if not verdict.valid:
        corrective = healer.corrective_context(verdict)
        # Inject corrective into next LLM call
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationVerdict:
    """Result from validating a tool call output."""
    tool_name: str
    valid: bool
    issue: str = ""
    suggested_action: str = ""
    retry_recommended: bool = False
    original_args: Dict[str, Any] = field(default_factory=dict)
    modified_args: Optional[Dict[str, Any]] = None


@dataclass
class HealingStats:
    """Stats from the self-healer."""
    validations: int = 0
    failures_detected: int = 0
    retries_suggested: int = 0
    successful_heals: int = 0


class SelfHealer:
    """Post-tool-call validator with corrective feedback.

    After each tool execution, checks whether the output looks
    correct. If not, generates corrective context for the next
    LLM call, enabling self-correction.
    """

    def __init__(
        self,
        tool_schemas=None,
        max_retries_per_call: int = 1,
    ):
        """
        Args:
            tool_schemas: Optional ToolSchemaRegistry for error pattern matching.
            max_retries_per_call: Max retry suggestions per tool call.
        """
        self._schemas = tool_schemas
        self._max_retries = max_retries_per_call
        self._retry_counts: Dict[str, int] = {}  # tool_call_id → retry count
        self._stats = HealingStats()

    def validate(
        self,
        tool_name: str,
        output: str,
        args: Optional[Dict[str, Any]] = None,
        call_id: str = "",
    ) -> ValidationVerdict:
        """Validate a tool call's output.

        Args:
            tool_name: Name of the tool that was called.
            output: The tool's output string.
            args: The arguments passed to the tool.
            call_id: Unique ID for this call (for retry tracking).

        Returns:
            ValidationVerdict indicating whether the output is valid.
        """
        self._stats.validations += 1
        args = args or {}

        # Check retry budget
        if call_id and self._retry_counts.get(call_id, 0) >= self._max_retries:
            return ValidationVerdict(
                tool_name=tool_name, valid=True,
                issue="Retry budget exhausted — accepting output",
                original_args=args,
            )

        # Run validators
        for check in self._validators():
            verdict = check(tool_name, output, args)
            if verdict and not verdict.valid:
                self._stats.failures_detected += 1
                if verdict.retry_recommended and call_id:
                    self._retry_counts[call_id] = self._retry_counts.get(call_id, 0) + 1
                    self._stats.retries_suggested += 1
                return verdict

        return ValidationVerdict(
            tool_name=tool_name, valid=True, original_args=args,
        )

    def record_successful_heal(self) -> None:
        """Record that a retry produced a successful result."""
        self._stats.successful_heals += 1

    def corrective_context(self, verdict: ValidationVerdict) -> str:
        """Generate corrective context to inject into the next LLM call.

        Args:
            verdict: The failed validation verdict.

        Returns:
            Corrective context string for system message injection.
        """
        parts = [f"[SELF-HEAL] Tool '{verdict.tool_name}' output was invalid."]
        if verdict.issue:
            parts.append(f"Issue: {verdict.issue}")
        if verdict.suggested_action:
            parts.append(f"Suggestion: {verdict.suggested_action}")
        if verdict.modified_args:
            parts.append(f"Try with modified args: {verdict.modified_args}")
        return " ".join(parts)

    def stats(self) -> HealingStats:
        """Return healing stats."""
        return self._stats

    def reset(self) -> None:
        """Reset retry counts for a new turn."""
        self._retry_counts.clear()

    # ── Validators ───────────────────────────────────────────────

    def _validators(self):
        """Return the ordered list of validation functions."""
        return [
            self._check_empty_output,
            self._check_error_output,
            self._check_schema_patterns,
            self._check_blocked_output,
        ]

    @staticmethod
    def _check_empty_output(
        tool_name: str, output: str, args: Dict
    ) -> Optional[ValidationVerdict]:
        """Detect empty or near-empty outputs."""
        if not output or len(output.strip()) < 3:
            # Some tools legitimately return empty (e.g., write_file)
            non_empty_tools = {"web_search", "read_file", "shell", "memory_search"}
            if tool_name in non_empty_tools:
                suggested = ""
                modified = None
                if tool_name == "web_search":
                    query = args.get("query", "")
                    suggested = f"Reformulate query: '{query}' returned no results"
                    modified = {**args, "query": f"site:docs {query}"}
                elif tool_name == "read_file":
                    suggested = "File may be empty or path incorrect — verify with ls"
                return ValidationVerdict(
                    tool_name=tool_name,
                    valid=False,
                    issue="Empty output from tool that should return content",
                    suggested_action=suggested,
                    retry_recommended=True,
                    original_args=args,
                    modified_args=modified,
                )
        return None

    @staticmethod
    def _check_error_output(
        tool_name: str, output: str, args: Dict
    ) -> Optional[ValidationVerdict]:
        """Detect error messages in output."""
        error_indicators = [
            (r"(?i)error:", "Error detected in output"),
            (r"(?i)traceback \(most recent call last\)", "Python traceback in output"),
            (r"(?i)command not found", "Command not found"),
            (r"(?i)permission denied", "Permission denied"),
        ]

        for pattern, description in error_indicators:
            if re.search(pattern, output[:500]):
                return ValidationVerdict(
                    tool_name=tool_name,
                    valid=False,
                    issue=description,
                    suggested_action="Address the error before proceeding",
                    retry_recommended=False,  # Error outputs shouldn't auto-retry
                    original_args=args,
                )
        return None

    def _check_schema_patterns(
        self, tool_name: str, output: str, args: Dict
    ) -> Optional[ValidationVerdict]:
        """Check against known error patterns from tool schemas."""
        if not self._schemas:
            return None

        ep = self._schemas.match_error(tool_name, output)
        if ep:
            return ValidationVerdict(
                tool_name=tool_name,
                valid=False,
                issue=f"{ep.description} ({ep.error_type})",
                suggested_action=ep.recovery,
                retry_recommended=ep.error_type in ("empty", "timeout"),
                original_args=args,
            )
        return None

    @staticmethod
    def _check_blocked_output(
        tool_name: str, output: str, args: Dict
    ) -> Optional[ValidationVerdict]:
        """Detect if the output was blocked by a guard."""
        if "[BLOCKED]" in output:
            return ValidationVerdict(
                tool_name=tool_name,
                valid=False,
                issue="Tool call was blocked by a guard",
                suggested_action="Try a different approach — this tool/args combination is not allowed",
                retry_recommended=False,
                original_args=args,
            )
        return None
