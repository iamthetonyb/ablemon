"""
Codex Cross-Audit — Independent code review using OpenAI Codex CLI.

Runs `codex` on git diffs to get a second opinion on changes ABLE makes.
Surfaces discrepancies between ABLE's self-assessment and Codex's review.

Inspired by the verification agent pattern from Claude Code:
- Adversarial review (tries to find what the author missed)
- Structured verdicts (PASS/FAIL/PARTIAL)
- Before-FAIL checklist

Usage:
    # Review latest diff
    python -m able.tools.codex_audit

    # Review specific commit range
    python -m able.tools.codex_audit --range HEAD~3..HEAD

    # Wire into /ship skill
    from able.tools.codex_audit import audit_diff
    result = await audit_diff(diff_text)
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AuditFinding:
    """A single finding from the audit."""
    severity: str  # "critical", "warning", "info"
    category: str  # "security", "logic", "style", "perf", "test"
    file: str
    line: Optional[int] = None
    description: str = ""
    suggestion: str = ""


@dataclass
class AuditResult:
    """Full audit result."""
    verdict: str = "UNKNOWN"  # PASS, FAIL, PARTIAL
    findings: List[AuditFinding] = field(default_factory=list)
    summary: str = ""
    diff_size: int = 0
    files_changed: int = 0
    errors: List[str] = field(default_factory=list)
    raw_output: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")


def _find_codex() -> Optional[str]:
    """Find the codex CLI binary."""
    for path in [
        "codex",
        "/usr/local/bin/codex",
        "/opt/homebrew/bin/codex",
        str(Path.home() / ".local" / "bin" / "codex"),
    ]:
        try:
            subprocess.run(
                [path, "--version"],
                capture_output=True,
                timeout=5,
            )
            return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _find_claude() -> Optional[str]:
    """Find the claude CLI binary (fallback reviewer)."""
    for path in [
        "claude",
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]:
        try:
            subprocess.run(
                [path, "--version"],
                capture_output=True,
                timeout=5,
            )
            return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


async def audit_diff(
    diff: Optional[str] = None,
    commit_range: str = "HEAD~1..HEAD",
    cwd: Optional[str] = None,
) -> AuditResult:
    """
    Run an independent code audit on a git diff.

    Tries codex first, falls back to claude CLI, then to self-review.
    """
    from able.core.observability.tracer import trace_operation

    result = AuditResult()
    work_dir = cwd or str(Path(__file__).parent.parent.parent)

    # Get the diff if not provided (None means auto-detect, "" means no diff)
    if diff is None:
        try:
            proc = subprocess.run(
                ["git", "diff", commit_range],
                capture_output=True,
                text=True,
                cwd=work_dir,
                timeout=30,
            )
            diff = proc.stdout
        except Exception as e:
            result.errors.append(f"Git diff failed: {e}")
            return result

    if not diff or not diff.strip():
        result.verdict = "PASS"
        result.summary = "No changes to audit."
        return result

    result.diff_size = len(diff)
    result.files_changed = diff.count("diff --git")

    with trace_operation(
        "codex.audit",
        attributes={
            "audit.diff_size": result.diff_size,
            "audit.files_changed": result.files_changed,
        },
        tracer_name="able.codex",
    ) as span:
        reviewer = "rules"

        # Try codex CLI first
        codex_path = _find_codex()
        if codex_path:
            result = await _run_codex_review(codex_path, diff, work_dir, result)
            reviewer = "codex"

        # If codex didn't produce a verdict, try claude CLI
        if result.verdict == "UNKNOWN":
            claude_path = _find_claude()
            if claude_path:
                result = await _run_claude_review(claude_path, diff, work_dir, result)
                reviewer = "claude"

        # Always run rule-based as supplement / fallback
        rule_result = _rule_based_review(diff, AuditResult())
        if result.verdict == "UNKNOWN":
            # CLI tools didn't produce a verdict — use rule-based as primary
            result = rule_result
            reviewer = "rules"
        elif rule_result.findings:
            # CLI gave a verdict but rules found additional issues — merge
            result.findings.extend(rule_result.findings)
            # Escalate verdict if rules found criticals the CLI missed
            if rule_result.critical_count > 0 and result.verdict == "PASS":
                result.verdict = "PARTIAL"
                result.summary += f" (rules found {rule_result.critical_count} critical)"

        span.set_attribute("audit.reviewer", reviewer)
        span.set_attribute("audit.verdict", result.verdict)
        span.set_attribute("audit.critical_count", result.critical_count)
        span.set_attribute("audit.warning_count", result.warning_count)

    return result


async def _run_codex_review(
    codex_path: str, diff: str, cwd: str, result: AuditResult
) -> AuditResult:
    """Run codex review on a diff."""
    prompt = (
        "Review this git diff for security vulnerabilities, logic errors, "
        "missing error handling, and code quality issues. "
        "For each finding, specify severity (critical/warning/info), "
        "category, file, and a one-line description.\n\n"
        f"```diff\n{diff[:10000]}\n```"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            codex_path, prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        result.raw_output = stdout.decode()
        result = _parse_review_output(result.raw_output, result)
    except asyncio.TimeoutError:
        result.errors.append("Codex review timed out (120s)")
    except Exception as e:
        result.errors.append(f"Codex review failed: {e}")

    return result


async def _run_claude_review(
    claude_path: str, diff: str, cwd: str, result: AuditResult
) -> AuditResult:
    """Run claude CLI review on a diff."""
    prompt = (
        "You are a code reviewer. Analyze this diff for:\n"
        "1. Security vulnerabilities (injection, XSS, secrets)\n"
        "2. Logic errors or edge cases\n"
        "3. Missing error handling\n"
        "4. Performance issues\n\n"
        "Rate each finding as critical/warning/info.\n"
        "End with VERDICT: PASS, FAIL, or PARTIAL.\n\n"
        f"```diff\n{diff[:8000]}\n```"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            claude_path, "--print", "-p", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        result.raw_output = stdout.decode()
        result = _parse_review_output(result.raw_output, result)
    except asyncio.TimeoutError:
        result.errors.append("Claude review timed out (120s)")
    except Exception as e:
        result.errors.append(f"Claude review failed: {e}")

    return result


def _rule_based_review(diff: str, result: AuditResult) -> AuditResult:
    """Fallback: rule-based static analysis on the diff."""
    import re

    lines = diff.split("\n")
    current_file = ""

    for i, line in enumerate(lines):
        if line.startswith("diff --git"):
            parts = line.split(" b/")
            current_file = parts[-1] if len(parts) > 1 else ""
            continue

        if not line.startswith("+") or line.startswith("+++"):
            continue

        added = line[1:]  # Remove the +

        # Security: hardcoded secrets
        if re.search(r'(?:password|secret|token|api_key)\s*=\s*["\'][^"\']{8,}', added, re.I):
            result.findings.append(AuditFinding(
                severity="critical", category="security",
                file=current_file, line=i,
                description="Possible hardcoded secret",
                suggestion="Use environment variable or .secrets/ instead",
            ))

        # Security: SQL injection
        if re.search(r'f".*SELECT.*\{', added) or re.search(r'f".*INSERT.*\{', added):
            result.findings.append(AuditFinding(
                severity="critical", category="security",
                file=current_file, line=i,
                description="Potential SQL injection via f-string",
                suggestion="Use parameterized queries",
            ))

        # Security: eval/exec
        if re.search(r'\beval\s*\(|\bexec\s*\(', added):
            result.findings.append(AuditFinding(
                severity="critical", category="security",
                file=current_file, line=i,
                description="eval()/exec() usage — code injection risk",
            ))

        # Quality: bare except
        if re.search(r'except\s*:', added):
            result.findings.append(AuditFinding(
                severity="warning", category="style",
                file=current_file, line=i,
                description="Bare except clause — catches too broadly",
            ))

        # Quality: TODO/FIXME/HACK
        if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', added):
            result.findings.append(AuditFinding(
                severity="info", category="style",
                file=current_file, line=i,
                description=f"Marker comment in new code: {added.strip()[:60]}",
            ))

    # Set verdict based on findings
    if result.critical_count > 0:
        result.verdict = "FAIL"
        result.summary = f"{result.critical_count} critical, {result.warning_count} warnings"
    elif result.warning_count > 2:
        result.verdict = "PARTIAL"
        result.summary = f"{result.warning_count} warnings"
    else:
        result.verdict = "PASS"
        result.summary = f"Clean — {len(result.findings)} minor findings"

    return result


def _parse_review_output(output: str, result: AuditResult) -> AuditResult:
    """Parse review output from codex/claude into structured findings."""
    import re

    # Look for VERDICT line
    verdict_match = re.search(r"VERDICT:\s*(PASS|FAIL|PARTIAL)", output, re.I)
    if verdict_match:
        result.verdict = verdict_match.group(1).upper()

    # Extract findings by severity markers
    for match in re.finditer(
        r"(?:critical|warning|info)[:\s]+(.+?)(?=\n(?:critical|warning|info|VERDICT|$))",
        output, re.I | re.DOTALL,
    ):
        severity_match = re.match(r"(critical|warning|info)", match.group(0), re.I)
        severity = severity_match.group(1).lower() if severity_match else "info"
        description = match.group(1).strip()[:200]

        result.findings.append(AuditFinding(
            severity=severity,
            category="review",
            file="",
            description=description,
        ))

    if not result.verdict:
        result.verdict = "FAIL" if result.critical_count > 0 else "PASS"
        result.summary = output[:200]

    return result


def format_telegram(result: AuditResult) -> str:
    """Format audit result for Telegram."""
    emoji = {"PASS": "✅", "FAIL": "❌", "PARTIAL": "⚠️"}.get(result.verdict, "❓")
    lines = [
        f"{emoji} *Codex Cross-Audit*",
        f"Verdict: *{result.verdict}*",
        f"Files: {result.files_changed} | Diff: {result.diff_size} chars",
    ]
    if result.findings:
        lines.append(f"\n*Findings* ({len(result.findings)}):")
        for f in result.findings[:5]:
            sev_emoji = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get(f.severity, "")
            lines.append(f"  {sev_emoji} [{f.severity}] {f.description[:80]}")

    if result.summary:
        lines.append(f"\n_{result.summary}_")

    return "\n".join(lines)


async def main():
    """CLI entry point."""
    import sys

    logging.basicConfig(level=logging.INFO)

    commit_range = sys.argv[1] if len(sys.argv) > 1 else "HEAD~1..HEAD"
    result = await audit_diff(commit_range=commit_range)

    print(format_telegram(result))

    if result.raw_output:
        print("\n--- Raw Output ---")
        print(result.raw_output[:2000])


if __name__ == "__main__":
    asyncio.run(main())
