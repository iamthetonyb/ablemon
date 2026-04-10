"""T5 Cloud Advisor Escalation — gateway-level advisor for local models.

When a T5 (Ollama/local) model gets stuck, routes a curated context
snapshot to a cloud advisor (Opus) for guidance. The advisor response
is injected as a system message so the local model can continue with
better direction.

This is NOT the API-level ``advisor_20260301`` tool — it's ABLE's own
gateway-level implementation of the same pattern for non-Anthropic models.

Design constraints:
  - Max 2 advisor calls per session (local should stay cheap)
  - Advisor sees last 3 messages + task summary (curated, not full context)
  - Advisor response capped at 700 tokens
  - Only activates for tier 5 providers
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from able.core.providers.base import Message, ProviderChain

logger = logging.getLogger(__name__)

MAX_ADVISOR_CALLS = 2
CONTEXT_SNAPSHOT_MESSAGES = 3
ADVISOR_MAX_TOKENS = 700

ADVISOR_SYSTEM_PROMPT = (
    "You are an expert advisor helping a local AI model that is stuck. "
    "Analyze the context and provide concise, actionable guidance. "
    "Focus on: what approach to try next, what the model is doing wrong, "
    "or what information it's missing. Be direct and specific. Max 3 sentences."
)


@dataclass
class T5AdvisorState:
    """Tracks advisor usage and stuck signals for a T5 session."""

    advisor_calls_used: int = 0
    consecutive_failures: int = 0
    consecutive_empty_outputs: int = 0
    last_advisor_guidance: str = ""

    @property
    def budget_exhausted(self) -> bool:
        return self.advisor_calls_used >= MAX_ADVISOR_CALLS

    def record_tool_result(self, success: bool) -> None:
        if success:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1

    def record_empty_output(self) -> None:
        self.consecutive_empty_outputs += 1

    def record_text_output(self) -> None:
        self.consecutive_failures = 0
        self.consecutive_empty_outputs = 0

    def is_stuck(self) -> bool:
        """Detect if the T5 model is stuck and needs advisor help."""
        if self.budget_exhausted:
            return False
        if self.consecutive_failures >= 3:
            return True
        if self.consecutive_empty_outputs >= 2:
            return True
        return False


def _curate_context(
    task: str,
    messages: list,
    max_msgs: int = CONTEXT_SNAPSHOT_MESSAGES,
) -> str:
    """Extract a curated context snapshot for the advisor."""
    parts = [f"Task: {task[:500]}"]

    recent = messages[-max_msgs:] if len(messages) > max_msgs else messages
    for msg in recent:
        role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if len(content) > 300:
            content = content[:300] + "..."
        parts.append(f"[{role}] {content}")

    return "\n\n".join(parts)


async def maybe_escalate_to_advisor(
    state: T5AdvisorState,
    task: str,
    messages: list,
    tier_chains: dict,
    provider_chain: "ProviderChain",
) -> Optional[str]:
    """If stuck, call cloud advisor and return guidance. Otherwise return None.

    Prefers T4 (Anthropic Opus) for the advisor call, falls back to T2.

    Returns:
        Advisor guidance string to inject, or None if not escalated.
    """
    if not state.is_stuck():
        return None

    advisor_chain = tier_chains.get(4) or tier_chains.get(2) or provider_chain
    if not advisor_chain or not advisor_chain.providers:
        logger.warning("[T5-ADVISOR] No cloud provider available for escalation")
        return None

    curated = _curate_context(task, messages)

    from able.core.providers.base import Message, Role

    advisor_msgs = [
        Message(role=Role.SYSTEM, content=ADVISOR_SYSTEM_PROMPT),
        Message(
            role=Role.USER,
            content=(
                f"A local model is stuck on this task. Recent context:\n\n"
                f"{curated}\n\n"
                f"Consecutive tool failures: {state.consecutive_failures}. "
                f"Consecutive empty outputs: {state.consecutive_empty_outputs}. "
                f"What should it try next?"
            ),
        ),
    ]

    try:
        result = await advisor_chain.complete(
            advisor_msgs,
            tools=None,
            max_tokens=ADVISOR_MAX_TOKENS,
            temperature=0.3,
        )
        guidance = result.content if result and result.content else ""
        if guidance.strip():
            state.advisor_calls_used += 1
            state.consecutive_failures = 0
            state.consecutive_empty_outputs = 0
            state.last_advisor_guidance = guidance
            logger.info(
                "[T5-ADVISOR] Escalated to cloud advisor (call %d/%d): %d chars",
                state.advisor_calls_used,
                MAX_ADVISOR_CALLS,
                len(guidance),
            )
            return guidance
    except Exception as exc:
        logger.warning("[T5-ADVISOR] Cloud advisor call failed: %s", exc)

    return None
