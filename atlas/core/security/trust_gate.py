"""
Trust Gate - Multi-stage security pipeline
All inputs pass through here before reaching executor agents
"""

import re
import hashlib
import json
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime
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
    (r'forget\s+(everything|all|your\s+instructions?)', ThreatLevel.CRITICAL),
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

    # Prompt extraction attempts
    (r'(show|print|display|reveal|output|repeat)\s+(your\s+)?(system\s+)?(prompt|instructions?)', ThreatLevel.CRITICAL),
    (r'what\s+(are|is)\s+your\s+(system\s+)?(prompt|instructions?)', ThreatLevel.HIGH),
    (r'(beginning|start)\s+of\s+(the\s+)?(conversation|prompt)', ThreatLevel.HIGH),
    (r'(dump|leak|extract)\s+(your\s+)?(prompt|instructions?|config)', ThreatLevel.CRITICAL),
    (r'verbatim', ThreatLevel.MEDIUM),

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
    (r':(){.*};:', ThreatLevel.CRITICAL),  # Fork bomb
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
        timestamp = datetime.utcnow().isoformat()
        content_str = str(content) if not isinstance(content, str) else content
        hash_input = f"{timestamp}:{content_str[:100]}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    def _detect_injection(self, text: str) -> tuple[ThreatLevel, List[str]]:
        """Detect prompt injection attempts"""
        max_threat = ThreatLevel.SAFE
        flags = []

        text_lower = text.lower()

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
            sanitized_input=sanitized if passed else None,
            blocked_reason=blocked_reason,
            audit_id=audit_id
        )

        # Log for audit
        self._log_audit(verdict, source, text[:200])

        return verdict

    def _log_audit(self, verdict: SecurityVerdict, source: str, text_preview: str):
        """Log verdict for audit trail"""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
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
