"""
Output Discipline Guardrails (Wove pattern).

Enforces brevity for model responses that occur *between* tool calls.
Between-tool responses are internal narration — they should be minimal.
User-facing final responses are unrestricted.

Does NOT auto-truncate. Returns a warning the model should act on.

Plan item: Module 3 — Output Discipline Guardrails.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default word limit for between-tool narration
_DEFAULT_BETWEEN_TOOLS_LIMIT = 50


@dataclass
class DisciplineContext:
    """Context passed to OutputDiscipline.check_response().

    pending_tool_calls: number of tool calls that have yet to be dispatched
                        in the current turn (> 0 → between-tools mode).
    session_id: optional — used to bucket violation counts.
    """

    pending_tool_calls: int = 0
    session_id: str = "default"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DisciplineResult:
    """Result of a discipline check."""

    ok: bool
    warning: str = ""          # Injected warning text (empty when ok=True)
    word_count: int = 0        # Actual word count of the response
    limit: int = 0             # Limit that was applied (0 = no limit)
    trimmed_text: str = ""     # Original text unchanged (model must self-correct)


def _word_count(text: str) -> int:
    """Count whitespace-separated tokens in *text*."""
    return len(text.split())


class OutputDiscipline:
    """Check and flag over-verbose between-tool responses.

    Usage::

        discipline = OutputDiscipline(between_tools_limit=50)
        ctx = DisciplineContext(pending_tool_calls=1, session_id="abc")
        result = discipline.check_response(model_text, ctx)
        if not result.ok:
            inject(result.warning)   # feed back to model
    """

    def __init__(
        self,
        between_tools_limit: int = _DEFAULT_BETWEEN_TOOLS_LIMIT,
    ) -> None:
        self._limit = between_tools_limit
        # violation_count[session_id] → int
        self._violation_count: dict[str, int] = {}

    # ── public API ───────────────────────────────────────────────────────────

    def check_response(
        self, text: str, context: Optional[DisciplineContext] = None
    ) -> DisciplineResult:
        """Check *text* against discipline rules.

        Returns DisciplineResult. When between-tools mode is active and the
        response exceeds the word limit, ok=False and warning is populated.
        The caller injects the warning — this class never trims automatically.
        """
        ctx = context or DisciplineContext()
        wc = _word_count(text)

        if not self.is_between_tools(ctx):
            # Final / user-facing response — no restriction
            return DisciplineResult(ok=True, word_count=wc, limit=0, trimmed_text=text)

        if wc <= self._limit:
            return DisciplineResult(ok=True, word_count=wc, limit=self._limit, trimmed_text=text)

        # Violation
        sid = ctx.session_id
        self._violation_count[sid] = self._violation_count.get(sid, 0) + 1
        count = self._violation_count[sid]
        warning = self._build_warning(wc, self._limit, count)

        logger.debug(
            "OutputDiscipline: session=%s violation=%d wc=%d limit=%d",
            sid, count, wc, self._limit,
        )

        return DisciplineResult(
            ok=False,
            warning=warning,
            word_count=wc,
            limit=self._limit,
            trimmed_text=text,
        )

    def is_between_tools(self, context: DisciplineContext) -> bool:
        """True if we are mid-execution with pending tool calls."""
        return context.pending_tool_calls > 0

    def violation_count(self, session_id: str = "default") -> int:
        """Return number of violations recorded for this session."""
        return self._violation_count.get(session_id, 0)

    def reset_session(self, session_id: str = "default") -> None:
        """Clear violation counter for a session."""
        self._violation_count.pop(session_id, None)

    @property
    def between_tools_limit(self) -> int:
        return self._limit

    # ── internals ────────────────────────────────────────────────────────────

    def _build_warning(self, wc: int, limit: int, violation_count: int) -> str:
        base = (
            f"[DISCIPLINE] Response too long between tool calls: "
            f"{wc} words (limit {limit}). "
            "Keep between-tool narration under {limit} words. "
            "Proceed directly to the next tool call."
        ).format(limit=limit)
        if violation_count >= 3:
            base += (
                f" (Violation #{violation_count} this session — "
                "further verbose narration will be flagged to the auditor.)"
            )
        return base
