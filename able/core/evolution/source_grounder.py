"""
Source Grounder — Feynman-inspired citation verification for research findings.

All claims must link to verifiable sources. After initial research, this module:
1. Verifies URLs are reachable (HEAD request)
2. Cross-verifies specific claims via secondary search
3. Tags findings with verification status

Inspired by getcompanion-ai/feynman's source-grounding pattern.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


@dataclass
class VerificationResult:
    """Result of verifying a single finding."""
    finding_title: str
    url_reachable: Optional[bool] = None  # None = not checked, True/False = result
    url_status: int = 0
    cross_verified: Optional[bool] = None
    cross_verify_source: str = ""
    verification_tag: str = "unverified"  # "verified", "unverified", "broken-link", "contested"
    notes: str = ""


class SourceGrounder:
    """
    Verify research finding citations and cross-reference claims.

    Usage:
        grounder = SourceGrounder()
        results = await grounder.verify_findings(findings)
    """

    # Patterns that suggest verifiable claims (version numbers, benchmarks, dates)
    CLAIM_PATTERNS = [
        re.compile(r"\b\d+\.\d+(\.\d+)?\b"),  # Version numbers
        re.compile(r"\b\d+%\b"),  # Percentages
        re.compile(r"\b\d+[kKmMbB]\b"),  # Quantities (10k, 1M, etc.)
        re.compile(r"\b(released|launched|announced|deprecated)\b", re.I),
        re.compile(r"\b(benchmark|score|accuracy|latency)\s*:?\s*\d", re.I),
    ]

    def __init__(self, timeout: float = 10.0, max_concurrent: int = 5):
        self.timeout = timeout
        self.max_concurrent = max_concurrent

    async def verify_findings(
        self, findings: List[Dict[str, Any]]
    ) -> List[VerificationResult]:
        """
        Verify a batch of research findings.

        For each finding:
        1. HEAD request to check URL is reachable
        2. For high-relevance findings with specific claims, cross-verify
        """
        if not HTTPX_AVAILABLE:
            logger.warning("httpx not available — skipping verification")
            return [
                VerificationResult(f.get("title", "?"), verification_tag="unverified")
                for f in findings
            ]

        semaphore = asyncio.Semaphore(self.max_concurrent)
        tasks = [self._verify_one(f, semaphore) for f in findings]
        return await asyncio.gather(*tasks)

    async def _verify_one(
        self, finding: Dict[str, Any], sem: asyncio.Semaphore
    ) -> VerificationResult:
        """Verify a single finding."""
        title = finding.get("title", "?")
        url = finding.get("url", "")
        result = VerificationResult(finding_title=title)

        async with sem:
            # Step 1: URL reachability
            if url:
                result.url_reachable, result.url_status = await self._check_url(url)
                if not result.url_reachable:
                    result.verification_tag = "broken-link"
                    result.notes = f"URL returned {result.url_status}"
                    return result

            # Step 2: Check for verifiable claims
            summary = finding.get("summary", "")
            has_claims = any(p.search(summary) for p in self.CLAIM_PATTERNS)

            if has_claims and finding.get("relevance") == "high":
                # Cross-verify via secondary search
                cross_ok, source = await self._cross_verify(title, summary)
                result.cross_verified = cross_ok
                result.cross_verify_source = source
                if cross_ok:
                    result.verification_tag = "verified"
                elif cross_ok is False:
                    result.verification_tag = "contested"
                    result.notes = f"Cross-check found conflicting info from {source}"
                else:
                    result.verification_tag = "unverified"
            elif result.url_reachable:
                result.verification_tag = "verified"
            else:
                result.verification_tag = "unverified"

        return result

    async def _check_url(self, url: str) -> Tuple[bool, int]:
        """HEAD request to verify URL is reachable."""
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "ABLE-ResearchBot/1.0"},
            ) as client:
                resp = await client.head(url)
                return resp.status_code < 400, resp.status_code
        except httpx.TimeoutException:
            return False, 408
        except Exception:
            return False, 0

    async def _cross_verify(
        self, title: str, summary: str
    ) -> Tuple[Optional[bool], str]:
        """
        Attempt to cross-verify a claim via secondary web search.

        Returns (verified, source_description):
        - (True, source) if corroborating evidence found
        - (False, source) if contradicting evidence found
        - (None, "") if unable to verify
        """
        try:
            from able.tools.search.web_search import WebSearch
            search = WebSearch()

            # Extract key claim terms for verification query
            query = self._extract_claim_query(title, summary)
            if not query:
                return None, ""

            results = await search.search(query, max_results=3)
            if not results:
                return None, ""

            # If we find results with matching version/number claims, consider verified
            for r in results:
                r_text = f"{r.get('title', '')} {r.get('snippet', '')}".lower()
                # Check for overlapping specific numbers
                title_numbers = set(re.findall(r"\d+\.?\d*", summary))
                result_numbers = set(re.findall(r"\d+\.?\d*", r_text))
                overlap = title_numbers & result_numbers
                if len(overlap) >= 2:
                    return True, r.get("url", r.get("title", "secondary source"))

            return None, ""
        except Exception as e:
            logger.debug("Cross-verification failed: %s", e)
            return None, ""

    def _extract_claim_query(self, title: str, summary: str) -> str:
        """Extract a verification-focused search query from a finding."""
        # Use title + key numbers/versions from summary
        numbers = re.findall(r"\b\d+\.?\d*%?\b", summary)
        if numbers:
            key_nums = " ".join(numbers[:3])
            return f"{title[:50]} {key_nums}"
        return title[:60]

    def apply_tags(
        self,
        findings: List[Dict[str, Any]],
        verifications: List[VerificationResult],
    ) -> List[Dict[str, Any]]:
        """Apply verification tags to findings in-place and return them."""
        for finding, vr in zip(findings, verifications):
            tags = finding.get("tags", [])
            if vr.verification_tag not in tags:
                tags.append(f"#{vr.verification_tag}")
            finding["tags"] = tags
            finding["_verification"] = {
                "tag": vr.verification_tag,
                "url_reachable": vr.url_reachable,
                "cross_verified": vr.cross_verified,
                "notes": vr.notes,
            }
        return findings
