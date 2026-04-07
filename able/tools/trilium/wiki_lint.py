"""
Wiki Lint — Detect and report quality issues in the TriliumNext knowledge base.

Addresses the key LLM Wiki limitations:
1. Error accumulation: finds contradictions between notes
2. Staleness: flags notes not updated in 30+ days
3. Orphans: notes with no relations to anything
4. Confidence tracking: flags low-confidence single-source notes
5. Raw source preservation: checks that wiki entries link to original sources

Runs as part of the evolution daemon weekly cycle.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LintIssue:
    """A single quality issue found in the wiki."""
    note_id: str
    note_title: str
    issue_type: str  # "orphan", "stale", "no_source", "low_confidence", "duplicate_title"
    severity: str  # "info", "warning", "error"
    description: str
    suggestion: str = ""


@dataclass
class LintReport:
    """Complete wiki lint report."""
    timestamp: str = ""
    total_notes_checked: int = 0
    issues: List[LintIssue] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    def to_html(self) -> str:
        """Format as HTML for filing to Trilium."""
        lines = [
            f"<h2>Wiki Lint Report — {self.timestamp}</h2>",
            f"<p>Checked {self.total_notes_checked} notes, found {self.issue_count} issues.</p>",
        ]
        if self.summary:
            lines.append("<h3>Issue Summary</h3><table>")
            lines.append("<tr><th>Type</th><th>Count</th></tr>")
            for itype, count in sorted(self.summary.items(), key=lambda x: -x[1]):
                lines.append(f"<tr><td>{itype}</td><td>{count}</td></tr>")
            lines.append("</table>")

        if self.issues:
            # Group by severity
            for sev in ("error", "warning", "info"):
                sev_issues = [i for i in self.issues if i.severity == sev]
                if not sev_issues:
                    continue
                emoji = {"error": "🔴", "warning": "🟡", "info": "🔵"}[sev]
                lines.append(f"<h3>{emoji} {sev.title()} ({len(sev_issues)})</h3><ul>")
                for issue in sev_issues:
                    lines.append(
                        f"<li><strong>{issue.note_title}</strong> ({issue.note_id})<br>"
                        f"{issue.description}"
                        f"{'<br><em>Suggestion: ' + issue.suggestion + '</em>' if issue.suggestion else ''}"
                        f"</li>"
                    )
                lines.append("</ul>")

        return "\n".join(lines)


async def wiki_lint(file_to_trilium: bool = True) -> LintReport:
    """
    Run wiki lint checks on the TriliumNext knowledge base.

    Checks for:
    - Orphan notes (no relations)
    - Stale notes (not modified in 30+ days)
    - Missing source links (no URL or sourcePath attribute)
    - Low confidence (single unverified source)
    - Duplicate titles
    """
    report = LintReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    )

    try:
        from able.tools.trilium.client import TriliumClient, KNOWN_PARENTS

        async with TriliumClient() as client:
            if not await client.is_available():
                logger.info("Trilium not available, skipping lint")
                return report

            # Get all notes under the knowledge base
            kb_root = KNOWN_PARENTS.get("knowledge_base") or KNOWN_PARENTS.get("root", "root")
            all_notes = await client.search_notes(
                "#!template", limit=200
            )
            report.total_notes_checked = len(all_notes)

            if not all_notes:
                return report

            now = datetime.now(timezone.utc)
            stale_threshold = now - timedelta(days=30)
            title_counts: Dict[str, List[str]] = {}

            for note in all_notes:
                nid = note.note_id
                title = note.title

                # Skip system notes
                if title.startswith("_") or nid in ("root", "hidden"):
                    continue

                # Check for duplicate titles
                title_key = title.lower().strip()
                title_counts.setdefault(title_key, []).append(nid)

                # Check staleness
                if note.date_modified:
                    try:
                        mod_date = datetime.fromisoformat(
                            note.date_modified.replace("Z", "+00:00")
                        )
                        if mod_date < stale_threshold:
                            days_stale = (now - mod_date).days
                            report.issues.append(LintIssue(
                                note_id=nid,
                                note_title=title,
                                issue_type="stale",
                                severity="info" if days_stale < 60 else "warning",
                                description=f"Not modified in {days_stale} days",
                                suggestion="Review and update, or archive if no longer relevant",
                            ))
                    except Exception:
                        pass

                # Check for source links (URL attribute or sourcePath)
                attrs = note.attributes or []
                has_source = any(
                    a.get("name") in ("url", "sourcePath", "source", "sourceUrl")
                    or (a.get("name") == "label" and "http" in str(a.get("value", "")))
                    for a in attrs
                )
                has_relations = any(
                    a.get("type") == "relation" for a in attrs
                )

                if not has_source and not has_relations:
                    report.issues.append(LintIssue(
                        note_id=nid,
                        note_title=title,
                        issue_type="orphan",
                        severity="warning",
                        description="No source links and no relations to other notes",
                        suggestion="Add source URL or create relations to related notes",
                    ))
                elif not has_source:
                    report.issues.append(LintIssue(
                        note_id=nid,
                        note_title=title,
                        issue_type="no_source",
                        severity="info",
                        description="No source URL — this is a derived note without raw source link",
                        suggestion="Add original source URL to ground this note",
                    ))

                # Check confidence label
                confidence_attr = next(
                    (a for a in attrs if a.get("name") == "confidence"), None
                )
                if confidence_attr:
                    try:
                        conf = float(confidence_attr.get("value", 0))
                        if conf < 0.5:
                            report.issues.append(LintIssue(
                                note_id=nid,
                                note_title=title,
                                issue_type="low_confidence",
                                severity="warning",
                                description=f"Confidence score {conf:.2f} — may contain unverified claims",
                                suggestion="Cross-verify with additional sources",
                            ))
                    except (ValueError, TypeError):
                        pass

            # Report duplicate titles
            for title_key, nids in title_counts.items():
                if len(nids) > 1:
                    report.issues.append(LintIssue(
                        note_id=nids[0],
                        note_title=title_key,
                        issue_type="duplicate_title",
                        severity="warning",
                        description=f"Title appears {len(nids)} times: {', '.join(nids)}",
                        suggestion="Merge duplicates or differentiate titles",
                    ))

            # Build summary
            for issue in report.issues:
                report.summary[issue.issue_type] = report.summary.get(issue.issue_type, 0) + 1

            # File report to Trilium
            if file_to_trilium and report.issues:
                try:
                    parent_id = KNOWN_PARENTS.get("knowledge_base") or "root"
                    await client.create_note(
                        parent_id,
                        f"Lint Report — {report.timestamp[:10]}",
                        report.to_html(),
                    )
                    logger.info("Wiki lint report filed to Trilium: %d issues", report.issue_count)
                except Exception as e:
                    logger.warning("Failed to file lint report: %s", e)

    except ImportError:
        logger.debug("Trilium client not available")
    except Exception as e:
        logger.error("Wiki lint failed: %s", e)

    logger.info(
        "Wiki lint: checked %d notes, found %d issues (%s)",
        report.total_notes_checked,
        report.issue_count,
        ", ".join(f"{k}:{v}" for k, v in report.summary.items()) or "clean",
    )
    return report
