from __future__ import annotations

import asyncio
from pathlib import Path

from able.core.agi.proactive import LearningInsightCheck
from able.core.agi.self_improvement import SelfImprovementEngine, UpdateType
from able.core.approval.workflow import ApprovalResult, ApprovalStatus
from able.core.evolution.auto_improve import AutoImprover, ImprovementAction
from able.core.evolution.collector import MetricsCollector


class ApprovingWorkflow:
    async def request_approval(self, **kwargs):
        return ApprovalResult(
            request_id="approval-1",
            status=ApprovalStatus.APPROVED,
            approved_by=7,
        )


class DummyMemory:
    async def search(self, *args, **kwargs):
        return ["failure-1", "failure-2", "failure-3"]


class DummyCollector:
    def __init__(self):
        self.submitted = []

    def submit_insight(self, **kwargs):
        self.submitted.append(kwargs)


def test_self_improvement_section_update_applies_after_approval(tmp_path):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text(
        "# Demo Skill\n\n## Auto-Improve Guidance\n\nOld guidance.\n\n## Notes\n\nKeep this.\n",
        encoding="utf-8",
    )
    engine = SelfImprovementEngine(
        v2_path=tmp_path,
        approval_workflow=ApprovingWorkflow(),
    )

    update = asyncio.run(
        engine.propose_update(
            document_path=skill_path,
            content="### Latest Eval Reinforcement\n- Use stricter acceptance criteria.\n",
            update_type=UpdateType.SECTION,
            reason="Refresh guidance from eval failures",
            metadata={"section_heading": "## Auto-Improve Guidance"},
        )
    )

    assert update.applied is True
    content = skill_path.read_text(encoding="utf-8")
    assert "Use stricter acceptance criteria." in content
    assert "Keep this." in content
    assert "Old guidance." not in content


def test_auto_improve_applies_skill_action_to_skill_doc(tmp_path):
    skill_dir = tmp_path / "able" / "skills" / "library" / "security-audit"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("# Security Audit\n\n## Notes\n\nBase content.\n", encoding="utf-8")

    engine = SelfImprovementEngine(
        v2_path=tmp_path,
        approval_workflow=ApprovingWorkflow(),
    )
    improver = AutoImprover(auto_apply=True, self_improvement_engine=engine)
    action = ImprovementAction(
        id="skill-security-gap",
        category="skill",
        target_file=str(skill_path),
        description="Tighten security audit output criteria",
        proposed_change="Add explicit evidence and remediation requirements",
        confidence=0.8,
        source_eval="ABLE Security Regression",
        failure_pattern="T2 produced weak remediation detail",
    )

    applied = asyncio.run(improver._apply_actions([action]))

    assert applied == 1
    assert action.applied is True
    content = skill_path.read_text(encoding="utf-8")
    assert "## Auto-Improve Guidance" in content
    assert "Tighten security audit output criteria" in content


def test_metrics_collector_submit_insight_is_consumed_once():
    collector = MetricsCollector(db_path=":memory:")
    collector._queries = type(
        "Queries",
        (),
        {
            "get_evolution_summary": staticmethod(
                lambda since: {
                    "failures_by_tier": [],
                    "escalation_rate": {"override_rate_pct": 0},
                    "cost_by_tier": [],
                    "wins_by_tier": [],
                    "domain_accuracy": [],
                    "scoring_drift": [],
                    "fallback_frequency": [],
                }
            )
        },
    )()

    collector.submit_insight(
        title="Recurring Failure Pattern Detected",
        description="Three related failure memories observed.",
        source="proactive.learning_insights",
        data={"failure_count": 3},
    )
    first = collector.collect(since="2026-04-01T00:00:00+00:00")
    second = collector.collect(since="2026-04-01T00:00:00+00:00")

    assert len(first["proactive_insights"]) == 1
    assert first["proactive_insights"][0]["data"]["failure_count"] == 3
    assert second["proactive_insights"] == []


def _stub_queries():
    """Reusable stub for LogQueries returning empty metrics."""
    return type(
        "Queries",
        (),
        {
            "get_evolution_summary": staticmethod(
                lambda since: {
                    "failures_by_tier": [],
                    "escalation_rate": {"override_rate_pct": 0},
                    "cost_by_tier": [],
                    "wins_by_tier": [],
                    "domain_accuracy": [{"domain": "security", "accuracy": 0.7}],
                    "scoring_drift": [],
                    "fallback_frequency": [],
                }
            )
        },
    )()


class FakeMemoryEntry:
    def __init__(self, content, memory_type_value):
        self.content = content
        self.memory_type = type("MT", (), {"value": memory_type_value})()


class FakeSearchResult:
    def __init__(self, content, score, mem_type="learning"):
        self.entry = FakeMemoryEntry(content, mem_type)
        self.score = score


class FakeHybridMemory:
    """Fake HybridMemory that returns canned search results."""

    def search(self, query, memory_types=None, limit=5, min_score=0.3):
        return [
            FakeSearchResult("Security prompts tend to under-route at tier 1", 0.85),
            FakeSearchResult("Operator prefers conservative routing for financial domain", 0.72),
        ]


def test_memory_context_enriches_evolution_metrics():
    collector = MetricsCollector(db_path=":memory:", memory=FakeHybridMemory())
    collector._queries = _stub_queries()

    result = collector.collect(since="2026-04-01T00:00:00+00:00")

    ctx = result["memory_context"]
    assert ctx["available"] is True
    assert len(ctx["learnings"]) == 2
    assert "under-route" in ctx["learnings"][0]["content"]
    assert ctx["learnings"][0]["score"] == 0.85
    assert "query" in ctx


def test_memory_context_graceful_without_memory():
    collector = MetricsCollector(db_path=":memory:", memory=None)
    collector._queries = _stub_queries()

    result = collector.collect(since="2026-04-01T00:00:00+00:00")

    assert result["memory_context"]["available"] is False


def test_learning_insight_check_submits_to_collector():
    collector = DummyCollector()
    check = LearningInsightCheck(
        memory=DummyMemory(),
        min_pattern_count=3,
        collector=collector,
    )

    actions = asyncio.run(check.run())

    assert len(actions) == 1
    assert collector.submitted[0]["title"] == "Recurring Failure Pattern Detected"
    assert collector.submitted[0]["data"]["failure_count"] == 3
