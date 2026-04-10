"""
PII Redactor — strips personally identifiable information before external calls.

Applied selectively: T1/T2 (external OAuth providers) get redaction.
T4 (Claude) and T5 (local Ollama) skip it — they're trusted or local.

Patterns: email, phone, SSN, credit card, API key prefixes.
Replacements are typed and numbered for reversibility context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class RedactedField:
    """Record of a redacted PII match."""
    field_type: str  # "email", "phone", "ssn", "credit_card", "api_key"
    placeholder: str  # "[REDACTED_EMAIL_1]"
    start: int
    end: int


# ── PII patterns ──────────────────────────────────────────────────

# Email: standard RFC 5322 simplified
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
)

# Phone: US/international formats (10-15 digits with optional separators)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?"
    r"(?:\(?\d{3}\)?[-.\s]?)"
    r"\d{3}[-.\s]?\d{4}(?!\d)"
)

# SSN: XXX-XX-XXXX (with dashes or spaces)
_SSN_RE = re.compile(
    r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"
)

# Credit card: 13-19 digits with optional separators (Luhn not checked — too slow)
_CREDIT_CARD_RE = re.compile(
    r"\b(?:\d{4}[-\s]?){3,4}\d{1,4}\b"
)

# API key prefixes — common formats that leak into prompts
_API_KEY_RE = re.compile(
    r"\b("
    r"sk-[A-Za-z0-9]{20,}"       # OpenAI / Anthropic
    r"|ghp_[A-Za-z0-9]{36}"      # GitHub PAT
    r"|gho_[A-Za-z0-9]{36}"      # GitHub OAuth
    r"|xoxb-[A-Za-z0-9-]+"       # Slack bot
    r"|xoxp-[A-Za-z0-9-]+"       # Slack user
    r"|AKIA[0-9A-Z]{16}"         # AWS access key
    r"|AIza[A-Za-z0-9_-]{35}"    # Google API key
    r"|glpat-[A-Za-z0-9_-]{20,}" # GitLab PAT
    r")\b"
)

# Ordered by specificity — API keys first (most specific), then cards, SSN, etc.
_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("api_key", _API_KEY_RE),
    ("ssn", _SSN_RE),
    ("credit_card", _CREDIT_CARD_RE),
    ("email", _EMAIL_RE),
    ("phone", _PHONE_RE),
]


def redact_pii(text: str) -> Tuple[str, List[RedactedField]]:
    """
    Replace PII in text with typed placeholders.

    Returns (redacted_text, list_of_redactions).
    Placeholders are numbered per type: [REDACTED_EMAIL_1], [REDACTED_EMAIL_2], etc.
    """
    redactions: List[RedactedField] = []
    counters: dict = {}
    result = text

    for pii_type, pattern in _PATTERNS:
        matches = list(pattern.finditer(result))
        if not matches:
            continue

        # Process in reverse order to preserve indices
        for match in reversed(matches):
            counters[pii_type] = counters.get(pii_type, 0) + 1
            placeholder = f"[REDACTED_{pii_type.upper()}_{counters[pii_type]}]"
            redactions.append(RedactedField(
                field_type=pii_type,
                placeholder=placeholder,
                start=match.start(),
                end=match.end(),
            ))
            result = result[:match.start()] + placeholder + result[match.end():]

    # Reverse so redactions are in document order
    redactions.reverse()
    return result, redactions


def has_pii(text: str) -> bool:
    """Quick check — does the text contain any PII patterns?"""
    for _, pattern in _PATTERNS:
        if pattern.search(text):
            return True
    return False
