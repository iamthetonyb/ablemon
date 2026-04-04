"""
Trust Gate - Multi-stage security pipeline
All inputs pass through here before reaching executor agents
"""

import base64
import re
import hashlib
import json
import unicodedata
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pathlib import Path

class ThreatLevel(Enum):
    SAFE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

class TrustTier(Enum):
    L1_OBSERVE = 1      # Read-only, report only
    L2_SUGGEST = 2      # Can draft, needs approval
    L3_BOUNDED = 3      # Act within strict limits
    L4_AUTONOMOUS = 4   # Full agency with oversight

@dataclass
class SecurityVerdict:
    passed: bool
    threat_level: ThreatLevel
    trust_score: float  # 0.0 - 1.0
    flags: List[str]
    sanitized_input: Optional[str]
    blocked_reason: Optional[str]
    audit_id: str

# Prompt injection patterns - COMPREHENSIVE
INJECTION_PATTERNS = [
    # Direct instruction override
    (r'ignore\s+(all\s+)?(previous\s+)?instructions?', ThreatLevel.CRITICAL),
    (r'disregard\s+(your\s+)?(previous\s+)?instructions?', ThreatLevel.CRITICAL),
    (r'forget\s+(everything|all|your\s+(instructions?|system\s+prompt|rules?|prompt))', ThreatLevel.CRITICAL),
    (r'override\s+(your\s+)?(system|instructions?|rules?)', ThreatLevel.CRITICAL),
    (r'new\s+instructions?:', ThreatLevel.CRITICAL),

    # Identity manipulation
    (r'you\s+are\s+now\s+', ThreatLevel.CRITICAL),
    (r'act\s+as\s+(if\s+you\s+were|a)', ThreatLevel.HIGH),
    (r'pretend\s+(to\s+be|you\'?re)', ThreatLevel.HIGH),
    (r'roleplay\s+as', ThreatLevel.HIGH),
    (r'switch\s+to\s+.+\s+mode', ThreatLevel.HIGH),
    (r'enter\s+.+\s+mode', ThreatLevel.HIGH),
    (r'jailbreak', ThreatLevel.CRITICAL),
    (r'DAN\s+mode', ThreatLevel.CRITICAL),

    # Prompt extraction attempts (flexible — allows filler words between verb and target)
    (r'(show|print|display|reveal|output|repeat)\s+.{0,30}(system\s+)?(prompt|instructions?|config|rules?)', ThreatLevel.CRITICAL),
    (r'what\s+(are|is)\s+your\s+(system\s+)?(prompt|instructions?|config|rules?)', ThreatLevel.HIGH),
    (r'(beginning|start)\s+of\s+(the\s+)?(conversation|prompt)', ThreatLevel.HIGH),
    (r'(dump|leak|extract)\s+.{0,20}(prompt|instructions?|config)', ThreatLevel.CRITICAL),
    (r'verbatim', ThreatLevel.MEDIUM),
    (r'(reveal|expose)\s+.{0,20}(configuration|rules?|directives?)', ThreatLevel.CRITICAL),

    # Delimiter/format attacks
    (r'\[INST\]|\[/INST\]', ThreatLevel.CRITICAL),
    (r'<\|.*?\|>', ThreatLevel.CRITICAL),
    (r'```system', ThreatLevel.CRITICAL),
    (r'<system>', ThreatLevel.CRITICAL),
    (r'Human:|Assistant:|User:|AI:', ThreatLevel.HIGH),
    (r'###\s*(Instruction|System|Human|Assistant)', ThreatLevel.HIGH),

    # Code injection via prompts
    (r'```(bash|python|sh).*?(rm\s+-rf|curl.*\|.*sh|wget.*\|.*sh)', ThreatLevel.CRITICAL),
    (r'eval\s*\(', ThreatLevel.HIGH),
    (r'exec\s*\(', ThreatLevel.HIGH),
    (r'__import__', ThreatLevel.HIGH),
    (r'subprocess', ThreatLevel.MEDIUM),

    # API key extraction
    (r'(api[_-]?key|secret|token|password|credential)', ThreatLevel.MEDIUM),
    (r'(show|print|display)\s+.*(key|secret|token|credential)', ThreatLevel.HIGH),
    (r'env(iron)?\s*\[', ThreatLevel.HIGH),
    (r'process\.env', ThreatLevel.HIGH),
    (r'os\.environ', ThreatLevel.MEDIUM),

    # Indirect injection markers
    (r'IMPORTANT:\s*ignore', ThreatLevel.CRITICAL),
    (r'ADMIN\s*OVERRIDE', ThreatLevel.CRITICAL),
    (r'SYSTEM\s*MESSAGE', ThreatLevel.CRITICAL),
    (r'BEGIN\s*HIDDEN\s*INSTRUCTION', ThreatLevel.CRITICAL),

    # Semantic evasion — paraphrased instruction overrides
    (r'discard\s+.{0,20}(directives?|guidelines?|rules?)', ThreatLevel.HIGH),
    (r'start\s+fresh', ThreatLevel.MEDIUM),
    (r'no\s+prior\s+context', ThreatLevel.MEDIUM),
    (r'previous\s+(conversation\s+)?context\s+is\s+(irrelevant|invalid)', ThreatLevel.HIGH),
    (r'step\s*\d+\s*:\s*(forget|ignore|disregard)', ThreatLevel.CRITICAL),
    (r'begin\s+new\s+task', ThreatLevel.MEDIUM),

    # Base64-encoded instruction detection
    (r'(decode|execute|run|eval)\s+.{0,10}[A-Za-z0-9+/]{20,}={0,2}', ThreatLevel.HIGH),

    # Path traversal
    (r'\.\./\.\./|\.\.\\\.\.\\', ThreatLevel.CRITICAL),
    (r'/etc/(passwd|shadow|hosts)', ThreatLevel.CRITICAL),
    (r'~/\.ssh/', ThreatLevel.CRITICAL),
    (r'%2e%2e%2f', ThreatLevel.HIGH),
    (r'\.\./(\.env|\.secrets?|\.ssh)', ThreatLevel.HIGH),
]

# Command patterns that require elevated trust
SENSITIVE_COMMAND_PATTERNS = [
    (r'rm\s+', ThreatLevel.HIGH),
    (r'sudo\s+', ThreatLevel.CRITICAL),
    (r'chmod\s+', ThreatLevel.MEDIUM),
    (r'curl\s+.*\|\s*(bash|sh)', ThreatLevel.CRITICAL),
    (r'wget\s+.*\|\s*(bash|sh)', ThreatLevel.CRITICAL),
    (r'>\s*/etc/', ThreatLevel.CRITICAL),
    (r'dd\s+if=', ThreatLevel.CRITICAL),
    (r'mkfs', ThreatLevel.CRITICAL),
    (r':\(\)\s*\{.*?\}\s*;\s*:', ThreatLevel.CRITICAL),  # Fork bomb
]

class TrustGate:
    """
    Multi-stage trust gate for all agent inputs.
    Implements: Input Validation → Injection Detection → Trust Scoring → Sanitization
    """

    def __init__(self, min_trust_threshold: float = 0.7, audit_dir: str = "audit/logs"):
        self.min_trust_threshold = min_trust_threshold
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log = []

    def _generate_audit_id(self, content: str) -> str:
        """Generate unique audit ID for traceability"""
        timestamp = datetime.now(timezone.utc).isoformat()
        content_str = str(content) if not isinstance(content, str) else content
        hash_input = f"{timestamp}:{content_str[:100]}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    @staticmethod
    def _normalize_unicode(text: str) -> str:
        """Strip unicode tricks before injection detection.

        Handles: zero-width chars, homoglyphs, fullwidth, RTL overrides,
        leet speak (common substitutions).
        """
        # Strip zero-width characters (ZWJ, ZWSP, ZWNJ, word joiner, etc.)
        text = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060\ufeff]', '', text)

        # Normalize fullwidth → ASCII (ｉｇｎｏｒｅ → ignore)
        text = unicodedata.normalize('NFKC', text)

        # Map common Cyrillic/Greek homoglyphs to Latin equivalents
        _homoglyph_map = str.maketrans({
            '\u0430': 'a', '\u0435': 'e', '\u043e': 'o', '\u0440': 'p',
            '\u0441': 'c', '\u0443': 'y', '\u0445': 'x', '\u0456': 'i',
            '\u0410': 'A', '\u0415': 'E', '\u041e': 'O', '\u0420': 'P',
            '\u0421': 'C', '\u0423': 'Y', '\u0425': 'X',
        })
        text = text.translate(_homoglyph_map)

        # Common leet speak: 0→o, 1→i/l, 3→e, 4→a, 5→s, 7→t
        _leet_map = str.maketrans('013457', 'oieast')
        text = text.translate(_leet_map)

        return text

    def _detect_injection(self, text: str) -> tuple[ThreatLevel, List[str]]:
        """Detect prompt injection attempts"""
        max_threat = ThreatLevel.SAFE
        flags = []

        # Pre-normalization checks (patterns that leet/unicode normalization would corrupt)
        # Base64 encoded instructions
        if re.search(r'(decode|execute|run|eval)\s+.{0,20}[A-Za-z0-9+/]{20,}={0,2}', text, re.IGNORECASE):
            flags.append("INJECTION:base64_encoded_payload")
            max_threat = max(max_threat, ThreatLevel.HIGH, key=lambda t: t.value)

        # RTL override characters (used to visually hide reversed text)
        if re.search(r'[\u202a-\u202e]', text):
            flags.append("UNICODE:rtl_override_detected")
            max_threat = max(max_threat, ThreatLevel.MEDIUM, key=lambda t: t.value)

        # Non-Latin script density check (CJK/Arabic injection evasion vector).
        # Legitimate multilingual messages are fine; this flags unexpected high-density
        # foreign-script content mixed into otherwise Latin conversations — the pattern
        # that appeared in the 2026-04-04 lottery-spam injection incident.
        if len(text) >= 20:
            non_latin = sum(
                1 for c in text
                if '\u4e00' <= c <= '\u9fff'   # CJK Unified Ideographs
                or '\u3400' <= c <= '\u4dbf'   # CJK Extension A
                or '\u0600' <= c <= '\u06ff'   # Arabic
                or '\u0900' <= c <= '\u097f'   # Devanagari
            )
            density = non_latin / len(text)
            if non_latin >= 8 and density > 0.15:
                flags.append(f"UNICODE:high_nonlatin_density({density:.0%})")
                if max_threat.value < ThreatLevel.LOW.value:
                    max_threat = ThreatLevel.LOW

        # Normalize unicode tricks before pattern matching
        normalized = self._normalize_unicode(text)
        text_lower = normalized.lower()

        # Flag if normalization changed the text significantly (smuggling attempt)
        if len(text) - len(normalized) > 3:
            flags.append("UNICODE:smuggling_detected")
            if max_threat.value < ThreatLevel.MEDIUM.value:
                max_threat = ThreatLevel.MEDIUM

        for pattern, threat_level in INJECTION_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE | re.DOTALL):
                flags.append(f"INJECTION:{pattern[:30]}")
                if threat_level.value > max_threat.value:
                    max_threat = threat_level

        return max_threat, flags

    def _detect_sensitive_commands(self, text: str) -> tuple[ThreatLevel, List[str]]:
        """Detect potentially dangerous commands"""
        max_threat = ThreatLevel.SAFE
        flags = []

        for pattern, threat_level in SENSITIVE_COMMAND_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                flags.append(f"COMMAND:{pattern[:20]}")
                if threat_level.value > max_threat.value:
                    max_threat = threat_level

        return max_threat, flags

    def _calculate_trust_score(self, text: str, threat_level: ThreatLevel, flags: List[str]) -> float:
        """Calculate trust score based on analysis"""
        base_score = 1.0

        # Deduct for threat level
        threat_deductions = {
            ThreatLevel.SAFE: 0,
            ThreatLevel.LOW: 0.1,
            ThreatLevel.MEDIUM: 0.3,
            ThreatLevel.HIGH: 0.5,
            ThreatLevel.CRITICAL: 0.9
        }
        base_score -= threat_deductions.get(threat_level, 0)

        # Deduct for number of flags
        base_score -= len(flags) * 0.05

        # Deduct for suspicious length (very long inputs more likely to contain hidden instructions)
        if len(text) > 5000:
            base_score -= 0.1
        if len(text) > 10000:
            base_score -= 0.2

        # Deduct for unusual character patterns
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text):
            base_score -= 0.2

        return max(0.0, min(1.0, base_score))

    def _sanitize_input(self, text: str) -> str:
        """Sanitize input by removing/neutralizing dangerous patterns"""
        sanitized = text

        # Remove null bytes and control characters
        sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', sanitized)

        # Strip zero-width and RTL override characters
        sanitized = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060\ufeff]', '', sanitized)

        # Neutralize delimiter attacks by escaping
        sanitized = re.sub(r'\[INST\]', '[_INST_]', sanitized)
        sanitized = re.sub(r'\[/INST\]', '[/_INST_]', sanitized)
        sanitized = re.sub(r'<\|', '<_|', sanitized)
        sanitized = re.sub(r'\|>', '|_>', sanitized)

        return sanitized

    def evaluate(self, text: str, source: str = "unknown", user_trust_tier: TrustTier = TrustTier.L1_OBSERVE) -> SecurityVerdict:
        """
        Main evaluation entry point.
        Returns SecurityVerdict with pass/fail and detailed analysis.
        """
        audit_id = self._generate_audit_id(text)
        all_flags = []

        # Stage 1: Injection detection
        injection_threat, injection_flags = self._detect_injection(text)
        all_flags.extend(injection_flags)

        # Stage 2: Command detection
        command_threat, command_flags = self._detect_sensitive_commands(text)
        all_flags.extend(command_flags)

        # Determine max threat
        max_threat = ThreatLevel(max(injection_threat.value, command_threat.value))

        # Stage 3: Trust scoring
        trust_score = self._calculate_trust_score(text, max_threat, all_flags)

        # Stage 4: Sanitization
        sanitized = self._sanitize_input(text)

        # Determine pass/fail
        passed = trust_score >= self.min_trust_threshold
        blocked_reason = None

        if not passed:
            if max_threat == ThreatLevel.CRITICAL:
                blocked_reason = "CRITICAL threat detected - potential prompt injection or system compromise attempt"
            elif max_threat == ThreatLevel.HIGH:
                blocked_reason = "HIGH threat detected - suspicious patterns require human review"
            else:
                blocked_reason = f"Trust score {trust_score:.2f} below threshold {self.min_trust_threshold}"

        # For lower trust tiers, be more strict
        if user_trust_tier == TrustTier.L1_OBSERVE and max_threat.value >= ThreatLevel.MEDIUM.value:
            passed = False
            blocked_reason = f"L1 users cannot execute content with {max_threat.name} threat level"

        verdict = SecurityVerdict(
            passed=passed,
            threat_level=max_threat,
            trust_score=trust_score,
            flags=all_flags,
            sanitized_input=sanitized,  # always provide; pipeline uses it even when passed
            blocked_reason=blocked_reason,
            audit_id=audit_id
        )

        # Log for audit
        self._log_audit(verdict, source, text[:200])

        return verdict

    def _log_audit(self, verdict: SecurityVerdict, source: str, text_preview: str):
        """Log verdict for audit trail"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "audit_id": verdict.audit_id,
            "source": source,
            "text_preview": text_preview,
            "passed": verdict.passed,
            "threat_level": verdict.threat_level.name,
            "trust_score": verdict.trust_score,
            "flags": verdict.flags,
            "blocked_reason": verdict.blocked_reason
        }
        self.audit_log.append(entry)

        # Also write to file
        audit_file = self.audit_dir / "trust_gate.jsonl"
        with open(audit_file, "a") as f:
            f.write(json.dumps(entry) + "\n")


# Singleton instance
trust_gate = TrustGate(min_trust_threshold=0.7)
