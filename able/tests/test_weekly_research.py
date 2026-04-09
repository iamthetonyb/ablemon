from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from able.core.evolution.weekly_research import (
    ResearchFinding,
    WeeklyResearchReport,
    WeeklyResearchScout,
)


def test_save_report_writes_operator_latest_files(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    scout = WeeklyResearchScout(report_dir=str(tmp_path / "repo_reports"))

    report = WeeklyResearchReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_findings=2,
        search_queries_run=3,
        findings=[
            ResearchFinding(
                topic="tools_infra",
                source="github",
                title="Ollama release",
                summary="Faster local inference",
                url="https://example.com/ollama",
                relevance="high",
                action="Evaluate for upgrade",
            ),
            ResearchFinding(
                topic="security",
                source="web",
                title="Security note",
                summary="Minor hardening idea",
                url="https://example.com/security",
                relevance="medium",
                action="Review for hardening",
            ),
        ],
        high_priority=[],
    )
    report.high_priority.append(report.findings[0])
    report._action_items = [  # type: ignore[attr-defined]
        {
            "action": "Upgrade Ollama after compatibility check",
            "category": "upgrade",
            "impact": "high",
            "effort": "quick_win",
            "ties_to": "latency",
            "source_title": "Ollama release",
            "url": "https://example.com/ollama",
        }
    ]

    asyncio.run(scout._save_report(report))

    repo_json = list((tmp_path / "repo_reports").glob("research_*.json"))
    operator_json = tmp_path / ".able" / "reports" / "research" / "latest.json"
    operator_md = tmp_path / ".able" / "reports" / "research" / "latest.md"

    assert len(repo_json) == 1
    assert operator_json.exists()
    assert operator_md.exists()

    saved = json.loads(operator_json.read_text(encoding="utf-8"))
    assert saved["total_findings"] == 2
    assert saved["action_items"][0]["action"] == "Upgrade Ollama after compatibility check"

    markdown = operator_md.read_text(encoding="utf-8")
    assert "## Action Items" in markdown
    assert "Upgrade Ollama after compatibility check" in markdown
    assert "## High Priority Findings" in markdown


def test_format_telegram_points_to_operator_latest_path(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    scout = WeeklyResearchScout(report_dir=str(tmp_path / "repo_reports"))
    report = WeeklyResearchReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_findings=1,
        search_queries_run=1,
        findings=[],
        high_priority=[],
    )

    text = scout.format_telegram(report, mode="nightly")

    assert "~/.able/reports/research/latest.md" in text
