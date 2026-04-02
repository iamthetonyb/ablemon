"""
ABLE Fact Checker - Prevents hallucinations and validates AI outputs.

Sits in the pipeline between Auditor and Executor.
Every AI-generated response or scraped content passes through here
before being returned to users or acted upon.

Architecture:
    AI Output → FactChecker → Verified Response
                    ├── ClaimExtractor
                    ├── ConsistencyChecker (vs memory/context)
                    ├── ConfidenceScorer
                    └── HallucinationDetector

OpenClaw-inspired: "Trust but verify every output"
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VerificationStatus(Enum):
    VERIFIED = "verified"         # Claim confirmed accurate
    UNVERIFIED = "unverified"     # Cannot confirm or deny
    CONTRADICTED = "contradicted" # Claim conflicts with known data
    HALLUCINATION = "hallucination"  # Detected fabrication
    UNCERTAIN = "uncertain"       # Low confidence


class ClaimType(Enum):
    FACTUAL = "factual"         # Verifiable facts (dates, names, numbers)
    SUBJECTIVE = "subjective"   # Opinions/preferences - not checkable
    CODE = "code"               # Code snippets - syntax checkable
    URL = "url"                 # URLs - format checkable
    INSTRUCTION = "instruction" # Commands/actions
    INTERNAL = "internal"       # References to ABLE's own memory


@dataclass
class Claim:
    """A single extracted claim from AI output"""
    text: str
    claim_type: ClaimType
    confidence: float = 1.0
    line_number: int = 0
    metadata: Dict = field(default_factory=dict)


@dataclass
class VerificationResult:
    """Result of verifying a single claim"""
    claim: Claim
    status: VerificationStatus
    confidence: float  # 0.0 - 1.0
    explanation: str
    sources: List[str] = field(default_factory=list)
    corrected: Optional[str] = None  # Suggested correction if wrong


@dataclass
class FactCheckReport:
    """Full fact-check report for a piece of content"""
    original_content: str
    verification_results: List[VerificationResult]
    overall_confidence: float
    hallucination_risk: str  # low / medium / high / critical
    passed: bool
    flags: List[str] = field(default_factory=list)
    sanitized_content: Optional[str] = None  # Content with corrections
    processing_time_ms: float = 0.0

    def summary(self) -> str:
        total = len(self.verification_results)
        verified = sum(1 for r in self.verification_results if r.status == VerificationStatus.VERIFIED)
        contradicted = sum(1 for r in self.verification_results if r.status == VerificationStatus.CONTRADICTED)
        hallucinations = sum(1 for r in self.verification_results if r.status == VerificationStatus.HALLUCINATION)

        return (
            f"FactCheck: {verified}/{total} verified | "
            f"{contradicted} contradictions | "
            f"{hallucinations} hallucinations | "
            f"Risk: {self.hallucination_risk} | "
            f"Confidence: {self.overall_confidence:.0%}"
        )


# ─────────────────────────────────────────────────────────
# Hallucination Detection Patterns
# ─────────────────────────────────────────────────────────

# Phrases that strongly indicate hallucination / fabrication
HALLUCINATION_MARKERS = [
    # Over-confident assertions about things that can't be known
    r"i (can confirm|can verify|have confirmed|have verified) that",
    r"(according to|based on) (my training|my knowledge)",
    r"as of (today|now|this moment|currently)",

    # Fabricated citations
    r"(published|reported|stated) (in|by) .{0,50}(2024|2025|2026)",
    r"(study|research|paper) (by|from) .{0,40} (found|shows|proves)",

    # False memory claims
    r"you (told|said|mentioned|asked) me (earlier|before|previously)",
    r"(earlier|previously|before) in (this|our) (conversation|session)",

    # Invented specifics
    r"\b(exactly|precisely) \d+(\.\d+)? (percent|%)",
    r"the (exact|precise) (number|figure|amount) is \d+",

    # Bogus API/technical details
    r"(endpoint|api|url):\s*https?://[^\s]+\.fake",
    r"(version|v)\d+\.\d+\.\d+ (was released|released) on",
]

# Patterns that indicate low-quality/risky content
QUALITY_RISK_PATTERNS = [
    # Absolute statements that are often wrong
    r"\b(always|never|every|all|none|no one|everyone|everything)\b",

    # Unverified statistics
    r"\b\d{2,3}%\b",

    # Made-up-sounding URLs
    r"https?://(?!github\.com|stackoverflow|docs\.|www\.)[a-z]+\.(io|ai|xyz)/",

    # Version numbers (often hallucinated)
    r"\bv\d+\.\d+(\.\d+)?\b",
]

# Code quality checks
CODE_ISSUES = [
    (r"import (os|sys)\neval\(", "eval with os import"),
    (r"__import__\(['\"]os['\"]\)", "dynamic os import"),
    (r"exec\s*\(", "exec call"),
    (r"subprocess\.call\(.*(shell=True)", "shell=True subprocess"),
    (r"pickle\.loads\(", "unsafe pickle deserialization"),
    (r"yaml\.load\([^,]*\)", "unsafe yaml.load without Loader"),
]


class ClaimExtractor:
    """Extracts verifiable claims from AI-generated text"""

    # Patterns for different claim types
    FACTUAL_PATTERNS = [
        r"\b\d{4}\b",                          # Years
        r"\$\d+[\.,\d]*",                       # Money amounts
        r"\b\d+[\.,\d]*\s*(GB|MB|TB|KB)\b",    # File sizes
        r"https?://\S+",                         # URLs
        r"[\w.-]+@[\w.-]+\.\w+",               # Emails
        r"\b\d+%\b",                            # Percentages
        r"\bv\d+\.\d+",                         # Version numbers
        r"(?:API|SDK|CLI|GUI)\s+\w+",           # Technical terms with qualifiers
    ]

    def extract(self, text: str) -> List[Claim]:
        """Extract claims from text for verification"""
        claims = []
        lines = text.split('\n')

        for line_num, line in enumerate(lines):
            # Extract URLs
            for url in re.findall(r'https?://\S+', line):
                claims.append(Claim(
                    text=url,
                    claim_type=ClaimType.URL,
                    line_number=line_num,
                    metadata={"raw_line": line}
                ))

            # Extract code blocks
            if line.startswith('```') or line.startswith('    '):
                claims.append(Claim(
                    text=line,
                    claim_type=ClaimType.CODE,
                    line_number=line_num
                ))

            # Extract factual assertions (sentences with numbers/dates)
            if re.search(r'\b\d{4}\b|\b\d+%\b|\$[\d,]+', line):
                claims.append(Claim(
                    text=line.strip(),
                    claim_type=ClaimType.FACTUAL,
                    line_number=line_num
                ))

        return claims


class ConsistencyChecker:
    """
    Checks AI output for internal consistency and consistency
    with known facts in memory.
    """

    def __init__(self, memory_store=None):
        self.memory = memory_store
        self._known_facts: Dict[str, str] = {}

    def add_known_fact(self, key: str, value: str):
        """Add a fact to the known facts cache"""
        self._known_facts[key] = value

    def check_internal_consistency(self, claims: List[Claim]) -> List[Tuple[Claim, str]]:
        """Check claims against each other for contradictions"""
        contradictions = []

        # Check for contradictory statements (simple heuristic)
        seen = {}
        for claim in claims:
            if claim.claim_type == ClaimType.FACTUAL:
                # Extract number + context pairs
                nums = re.findall(r'(\w+(?:\s+\w+)?)\s+is\s+(\d+(?:\.\d+)?%?)', claim.text, re.I)
                for subject, value in nums:
                    key = subject.lower().strip()
                    if key in seen and seen[key] != value:
                        contradictions.append((
                            claim,
                            f"Contradicts earlier claim: '{key}' was '{seen[key]}', now '{value}'"
                        ))
                    seen[key] = value

        return contradictions

    async def check_against_memory(self, claims: List[Claim]) -> List[VerificationResult]:
        """Check claims against stored memory"""
        results = []
        if not self.memory:
            return results

        for claim in claims:
            if claim.claim_type not in (ClaimType.FACTUAL, ClaimType.URL):
                continue

            # Search memory for related facts
            try:
                memories = await self.memory.search(claim.text, limit=3)
                if memories:
                    # Simple check: does memory contradict the claim?
                    results.append(VerificationResult(
                        claim=claim,
                        status=VerificationStatus.UNVERIFIED,
                        confidence=0.6,
                        explanation="Found related memories but cannot automatically verify",
                        sources=[f"memory:{m.id}" for m in memories[:2]]
                    ))
            except Exception:
                pass

        return results


class HallucinationDetector:
    """
    Detects common hallucination patterns in AI output.

    Checks for:
    - Fabricated citations
    - Impossible certainty claims
    - False memory references
    - Made-up technical details
    """

    def scan(self, text: str) -> List[Tuple[str, str]]:
        """
        Scan text for hallucination markers.

        Returns list of (matched_text, marker_description) tuples.
        """
        findings = []

        for pattern in HALLUCINATION_MARKERS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                description = pattern.replace(r'\b', '').replace('(', '').replace(')', '')
                for match in matches:
                    if isinstance(match, tuple):
                        match = ' '.join(m for m in match if m)
                    findings.append((
                        str(match)[:100],
                        f"Hallucination marker: {description[:80]}"
                    ))

        return findings

    def score_quality(self, text: str) -> float:
        """
        Score content quality/reliability. 0.0 = unreliable, 1.0 = reliable.
        """
        score = 1.0
        text_lower = text.lower()

        # Deduct for hallucination markers
        hallucinations = self.scan(text)
        score -= min(0.5, len(hallucinations) * 0.1)

        # Deduct for risky patterns
        for pattern in QUALITY_RISK_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                score -= 0.03

        # Boost for hedged language (appropriate uncertainty)
        hedge_words = ['approximately', 'roughly', 'about', 'around', 'estimate',
                       'likely', 'probably', 'may', 'might', 'could', 'suggest']
        hedge_count = sum(1 for w in hedge_words if w in text_lower)
        score += min(0.1, hedge_count * 0.02)

        return max(0.0, min(1.0, score))


class CodeVerifier:
    """
    Verifies code snippets in AI output for safety and correctness.
    """

    def verify_python(self, code: str) -> List[Tuple[str, str]]:
        """Check Python code for security issues"""
        issues = []

        for pattern, description in CODE_ISSUES:
            if re.search(pattern, code, re.IGNORECASE | re.DOTALL):
                issues.append((pattern, f"Security issue: {description}"))

        # Try to syntax-check the code
        try:
            compile(code, '<string>', 'exec')
        except SyntaxError as e:
            issues.append(("syntax", f"Syntax error: {e}"))

        return issues

    def verify_shell(self, command: str) -> List[Tuple[str, str]]:
        """Check shell commands for dangerous patterns"""
        issues = []
        dangerous = [
            (r'rm\s+-rf\s+/', "Recursive delete from root"),
            (r'\|\s*(bash|sh|zsh)', "Pipe to shell"),
            (r'curl.+\|\s*(bash|sh)', "Remote code execution"),
            (r'>\s*/etc/', "Write to system config"),
            (r'chmod\s+777', "Permissive chmod"),
            (r'sudo\b', "Sudo elevation"),
            (r'--force', "Forced operation"),
        ]
        for pattern, desc in dangerous:
            if re.search(pattern, command, re.IGNORECASE):
                issues.append((pattern, desc))

        return issues


class FactChecker:
    """
    Main fact-checking orchestrator.

    Integrates into the ABLE pipeline between Auditor and Executor.
    Every AI-generated response passes through before being acted on.

    Usage:
        checker = FactChecker(memory=hybrid_memory)
        report = await checker.verify(ai_output, context=context)
        if report.passed:
            return report.sanitized_content or ai_output
        else:
            handle_failure(report)
    """

    def __init__(
        self,
        memory_store=None,
        confidence_threshold: float = 0.65,
        block_hallucinations: bool = True,
        strict_mode: bool = False,  # Fails on any unverified claim
    ):
        self.memory = memory_store
        self.confidence_threshold = confidence_threshold
        self.block_hallucinations = block_hallucinations
        self.strict_mode = strict_mode

        self.extractor = ClaimExtractor()
        self.consistency = ConsistencyChecker(memory_store)
        self.hallucination_detector = HallucinationDetector()
        self.code_verifier = CodeVerifier()

        self._check_count = 0
        self._block_count = 0

    async def verify(
        self,
        content: str,
        context: Optional[Dict] = None,
        source: str = "ai_output"
    ) -> FactCheckReport:
        """
        Verify content for accuracy and hallucinations.

        Args:
            content: The text to verify (AI response, scraped content, etc.)
            context: Additional context (conversation history, user query, etc.)
            source: Where this content came from (for logging)

        Returns:
            FactCheckReport with verdict and sanitized content
        """
        start_time = time.perf_counter()
        self._check_count += 1
        flags = []
        all_results: List[VerificationResult] = []

        # ── Stage 1: Hallucination detection ──────────────────────────────
        hallucinations = self.hallucination_detector.scan(content)
        quality_score = self.hallucination_detector.score_quality(content)

        if hallucinations:
            flags.append(f"hallucination_markers: {len(hallucinations)}")
            for matched, description in hallucinations[:5]:  # Cap at 5
                all_results.append(VerificationResult(
                    claim=Claim(text=matched, claim_type=ClaimType.FACTUAL),
                    status=VerificationStatus.HALLUCINATION,
                    confidence=0.9,
                    explanation=description
                ))

        # ── Stage 2: Code verification ─────────────────────────────────────
        code_blocks = re.findall(r'```(?:python|bash|sh)?\n(.*?)```', content, re.DOTALL)
        shell_commands = re.findall(r'(?:^|\n)\s*\$\s+(.+)', content)

        for code in code_blocks:
            if 'python' in content[:content.find(code)].lower() or 'py' in content[:content.find(code)].lower():
                issues = self.code_verifier.verify_python(code)
            else:
                issues = self.code_verifier.verify_shell(code)

            for _, desc in issues:
                flags.append(f"code_issue: {desc}")
                all_results.append(VerificationResult(
                    claim=Claim(text=code[:100], claim_type=ClaimType.CODE),
                    status=VerificationStatus.CONTRADICTED,
                    confidence=0.85,
                    explanation=f"Code safety issue: {desc}"
                ))

        for cmd in shell_commands:
            issues = self.code_verifier.verify_shell(cmd)
            for _, desc in issues:
                flags.append(f"shell_issue: {desc}")
                all_results.append(VerificationResult(
                    claim=Claim(text=cmd[:100], claim_type=ClaimType.INSTRUCTION),
                    status=VerificationStatus.CONTRADICTED,
                    confidence=0.9,
                    explanation=f"Shell command issue: {desc}"
                ))

        # ── Stage 3: Claim extraction and consistency check ────────────────
        claims = self.extractor.extract(content)
        contradictions = self.consistency.check_internal_consistency(claims)

        for claim, explanation in contradictions:
            flags.append("internal_contradiction")
            all_results.append(VerificationResult(
                claim=claim,
                status=VerificationStatus.CONTRADICTED,
                confidence=0.75,
                explanation=explanation
            ))

        # ── Stage 4: Memory consistency check ─────────────────────────────
        if self.memory:
            try:
                memory_results = await self.consistency.check_against_memory(claims)
                all_results.extend(memory_results)
            except Exception as e:
                logger.warning(f"Memory check failed: {e}")

        # ── Stage 5: Context consistency check ────────────────────────────
        if context:
            context_issues = self._check_context_consistency(content, context)
            for issue in context_issues:
                flags.append(f"context_mismatch: {issue}")
                all_results.append(VerificationResult(
                    claim=Claim(text=issue[:100], claim_type=ClaimType.FACTUAL),
                    status=VerificationStatus.CONTRADICTED,
                    confidence=0.7,
                    explanation=f"Contradicts conversation context: {issue}"
                ))

        # ── Calculate overall confidence ───────────────────────────────────
        hallucination_count = sum(
            1 for r in all_results if r.status == VerificationStatus.HALLUCINATION
        )
        contradiction_count = sum(
            1 for r in all_results if r.status == VerificationStatus.CONTRADICTED
        )

        # Weight different factors
        confidence = quality_score
        confidence -= hallucination_count * 0.15
        confidence -= contradiction_count * 0.10
        confidence = max(0.0, min(1.0, confidence))

        # Determine risk level
        if hallucination_count >= 3 or confidence < 0.3:
            hallucination_risk = "critical"
        elif hallucination_count >= 1 or confidence < 0.5:
            hallucination_risk = "high"
        elif contradiction_count >= 1 or confidence < 0.7:
            hallucination_risk = "medium"
        else:
            hallucination_risk = "low"

        # ── Determine pass/fail ────────────────────────────────────────────
        passed = True

        if self.block_hallucinations and hallucination_count > 0:
            passed = False
            self._block_count += 1

        if confidence < self.confidence_threshold:
            passed = False
            self._block_count += 1

        if self.strict_mode and contradiction_count > 0:
            passed = False

        # ── Sanitize content if needed ─────────────────────────────────────
        sanitized = None
        if not passed and content:
            sanitized = self._add_uncertainty_markers(content, all_results)

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        report = FactCheckReport(
            original_content=content,
            verification_results=all_results,
            overall_confidence=confidence,
            hallucination_risk=hallucination_risk,
            passed=passed,
            flags=flags,
            sanitized_content=sanitized,
            processing_time_ms=elapsed_ms
        )

        # Log summary
        emoji = "✅" if passed else "⚠️"
        logger.info(f"{emoji} FactCheck [{source}]: {report.summary()} ({elapsed_ms:.1f}ms)")

        if not passed:
            logger.warning(f"FactCheck FAILED [{source}]: flags={flags}")

        return report

    def _check_context_consistency(
        self, content: str, context: Dict
    ) -> List[str]:
        """Check content against conversation context"""
        issues = []

        # Check if AI refers to things not in context
        if 'user_message' in context:
            user_msg = context['user_message'].lower()

            # Check for false attributions
            if re.search(r"you (asked|said|mentioned) .{0,50}(that|to)", content, re.I):
                # Look for what was "attributed" and verify it's in context
                attr_match = re.search(
                    r"you (?:asked|said|mentioned) (.{5,80})", content, re.I
                )
                if attr_match:
                    attributed = attr_match.group(1).lower()[:50]
                    # Simple check: key words from attribution should be in user message
                    words = [w for w in attributed.split() if len(w) > 4]
                    if words and not any(w in user_msg for w in words[:3]):
                        issues.append(f"False attribution: '{attributed}' not in user message")

        return issues

    def _add_uncertainty_markers(
        self, content: str, results: List[VerificationResult]
    ) -> str:
        """Add uncertainty disclaimers to content with hallucination risk"""
        disclaimer = (
            "\n\n---\n"
            "⚠️ **Fact-Check Notice**: This response contains content that could not be "
            "fully verified. Claims marked above may be inaccurate. Please verify "
            "important facts from authoritative sources.\n"
        )
        return content + disclaimer

    async def verify_skill_output(self, skill_name: str, output: Any) -> FactCheckReport:
        """Verify output from a skill execution"""
        content = json.dumps(output) if not isinstance(output, str) else output
        return await self.verify(content, source=f"skill:{skill_name}")

    async def verify_scraped_content(self, url: str, content: str) -> FactCheckReport:
        """Verify scraped web content"""
        # Scraped content gets slightly more lenient treatment
        return await self.verify(
            content,
            context={"source_url": url},
            source=f"scrape:{url[:50]}"
        )

    def get_stats(self) -> Dict:
        """Get fact-checking statistics"""
        return {
            "total_checks": self._check_count,
            "total_blocked": self._block_count,
            "block_rate": self._block_count / self._check_count if self._check_count > 0 else 0,
            "confidence_threshold": self.confidence_threshold,
        }
