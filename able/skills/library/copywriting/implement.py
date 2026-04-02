"""
Copywriting Skill - Implementation

This is a thin wrapper. The main protocol is in SKILL.md which gets
injected into the LLM context when this skill triggers.

This implement.py exists for:
1. Programmatic invocation (batch processing)
2. Structured I/O validation
3. Logging and metrics
"""

import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CopywritingRequest:
    """Input for copywriting skill"""
    audience: str
    objective: str
    context: str = ""
    tone: str = "professional"
    framework: Optional[str] = None  # AIDA, PAS, FAB - auto-select if None


@dataclass
class CopywritingResult:
    """Output from copywriting skill"""
    copy: str
    framework_used: str
    meta_programs: Dict[str, str]
    scrubbed_words: int
    generated_at: datetime


# Load SKILL.md protocol for injection
SKILL_MD_PATH = Path(__file__).parent / "SKILL.md"


def get_protocol() -> str:
    """
    Get the copywriting protocol for context injection.

    This is what makes the skill work - the LLM reads this
    and modifies its behavior accordingly.
    """
    if SKILL_MD_PATH.exists():
        return SKILL_MD_PATH.read_text()
    return ""


def should_trigger(text: str, context: Dict[str, Any] = None) -> bool:
    """
    Check if this skill should auto-trigger based on user input.

    Called by the orchestrator to determine skill activation.
    """
    text_lower = text.lower()

    # Primary triggers
    triggers = [
        "respond", "reply", "email", "write", "draft",
        "message", "copy", "pitch", "follow up", "reach out",
        "get back to", "answer this", "compose",
    ]

    # Context boosters (increase likelihood)
    context_words = [
        "prospect", "client", "customer", "lead", "contact",
        "convert", "sell", "pitch", "close", "deal",
    ]

    has_trigger = any(t in text_lower for t in triggers)
    has_context = any(c in text_lower for c in context_words)

    # Trigger if primary match, or if context + partial match
    return has_trigger or (has_context and any(t[:4] in text_lower for t in triggers))


async def main(
    audience: str,
    objective: str,
    context: str = "",
    tone: str = "professional",
    framework: str = None,
    llm_provider: Any = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Main entry point for skill executor.

    For most use cases, the SKILL.md protocol is injected directly
    into the LLM context and generation happens there. This function
    is for programmatic/batch use cases.
    """
    # Build prompt with protocol
    protocol = get_protocol()

    prompt = f"""{protocol}

---

## CURRENT REQUEST

**Audience:** {audience}
**Objective:** {objective}
**Context:** {context or "None provided"}
**Tone:** {tone}
**Framework:** {framework or "Auto-select based on audience"}

Generate the copy now. Apply ALL rules from the protocol above.
"""

    if llm_provider:
        # Use provided LLM
        response = await llm_provider.generate(prompt=prompt)
        copy = response.get("content", "")
    else:
        # Return structured request for manual processing
        return {
            "success": True,
            "requires_llm": True,
            "prompt": prompt,
            "protocol_loaded": bool(protocol),
        }

    return {
        "success": True,
        "copy": copy,
        "framework_used": framework or "auto",
        "meta_programs": {},  # Would be populated by LLM response parsing
        "scrubbed_words": 0,
        "generated_at": datetime.utcnow().isoformat(),
    }


# For direct module testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(f"Protocol loaded: {len(get_protocol())} chars")
        print(f"Should trigger on '{sys.argv[1]}': {should_trigger(sys.argv[1])}")
    else:
        print("Usage: python implement.py 'test phrase'")
