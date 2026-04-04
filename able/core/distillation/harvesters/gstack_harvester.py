"""
Harvester for gstack learnings and analytics.

Reads learnings from ``~/.gstack/projects/*/learnings.jsonl`` and
analytics from ``~/.gstack/analytics/skill-usage.jsonl``.

Learnings are structured JSONL entries with skill, type, key, insight,
and confidence fields. They represent institutional knowledge accumulated
across sprint skills (/review, /qa, /ship, /cso, etc.) and are valuable
training data for teaching models about code review patterns, QA
processes, and operational insights.

Analytics track skill usage frequency and outcomes — useful for
understanding engineering workflow patterns.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)

logger = logging.getLogger(__name__)

_GSTACK_HOME = Path.home() / ".gstack"


class GstackHarvester(BaseHarvester):
    """Harvest learnings and analytics from gstack sprint skills."""

    source_name = "gstack"

    def __init__(self, gstack_home: str | Path | None = None):
        self.gstack_home = Path(gstack_home) if gstack_home else _GSTACK_HOME

    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        root = Path(source_path) if source_path else self.gstack_home
        if not root.exists():
            logger.debug("gstack home not found at %s", root)
            return []

        results: list[HarvestedConversation] = []

        # Harvest learnings from all projects
        projects_dir = root / "projects"
        if projects_dir.exists():
            for project_dir in sorted(projects_dir.iterdir()):
                if not project_dir.is_dir():
                    continue
                learnings_file = project_dir / "learnings.jsonl"
                if learnings_file.exists():
                    results.extend(
                        self._harvest_learnings(learnings_file, project_dir.name, since)
                    )

        # Harvest analytics (skill usage patterns)
        analytics_file = root / "analytics" / "skill-usage.jsonl"
        if analytics_file.exists():
            results.extend(self._harvest_analytics(analytics_file, since))

        logger.info(
            "gstack harvest: %d conversations from %s",
            len(results), root,
        )
        return results

    def _harvest_learnings(
        self,
        path: Path,
        project_slug: str,
        since: datetime | None,
    ) -> list[HarvestedConversation]:
        """Convert gstack learnings JSONL into training conversations.

        Each learning becomes a user→assistant pair:
        - User: "What did you learn about {key} during {skill}?"
        - Assistant: "{insight} (confidence: {confidence}/10)"
        """
        results: list[HarvestedConversation] = []

        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Parse timestamp
            ts_str = entry.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                ts = datetime.now(timezone.utc)

            if since and ts < since:
                continue

            skill = entry.get("skill", "unknown")
            key = entry.get("key", "")
            insight = entry.get("insight", "")
            confidence = entry.get("confidence", 5)
            entry_type = entry.get("type", "operational")
            source = entry.get("source", "observed")
            files = entry.get("files", [])

            if not insight:
                continue

            # Build a synthetic Q&A pair from the learning
            user_msg = (
                f"During a {skill} session on project '{project_slug}', "
                f"what was learned about '{key}' ({entry_type})?"
            )
            assistant_msg = insight
            if files:
                assistant_msg += f"\n\nRelevant files: {', '.join(files)}"
            assistant_msg += f"\n\nConfidence: {confidence}/10 (source: {source})"

            messages = [
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": assistant_msg},
            ]

            # Clean scaffolding (shouldn't have any, but defensive)
            messages = self._clean_messages(messages)
            if not messages:
                continue

            domain = self._detect_domain(messages) or self._skill_to_domain(skill)

            results.append(
                HarvestedConversation(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"gstack:{project_slug}:{key}:{ts_str}")),
                    source=self.source_name,
                    messages=messages,
                    model="gstack-learning",
                    timestamp=ts,
                    domain=domain,
                    metadata={
                        "project": project_slug,
                        "skill": skill,
                        "type": entry_type,
                        "confidence": confidence,
                        "file": str(path),
                    },
                )
            )

        return results

    def _harvest_analytics(
        self,
        path: Path,
        since: datetime | None,
    ) -> list[HarvestedConversation]:
        """Convert skill usage analytics into workflow pattern training data."""
        results: list[HarvestedConversation] = []

        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts_str = entry.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                ts = datetime.now(timezone.utc)

            if since and ts < since:
                continue

            skill = entry.get("skill", "unknown")
            outcome = entry.get("outcome", "unknown")
            duration = entry.get("duration_s", "0")
            repo = entry.get("repo", "unknown")

            # Only harvest completed sessions with clear outcomes
            if outcome not in ("success", "failure", "partial"):
                continue

            user_msg = f"Run /{skill} on {repo}"
            assistant_msg = (
                f"Completed /{skill} in {duration}s with outcome: {outcome}."
            )

            messages = [
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": assistant_msg},
            ]

            results.append(
                HarvestedConversation(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"gstack-analytics:{skill}:{ts_str}")),
                    source=self.source_name,
                    messages=messages,
                    model="gstack-analytics",
                    timestamp=ts,
                    domain=self._skill_to_domain(skill),
                    metadata={
                        "skill": skill,
                        "outcome": outcome,
                        "duration_s": duration,
                        "repo": repo,
                        "file": str(path),
                    },
                )
            )

        return results

    @staticmethod
    def _skill_to_domain(skill: str) -> str:
        """Map gstack skill names to ABLE training domains."""
        mapping = {
            "review": "coding",
            "qa": "coding",
            "qa-only": "coding",
            "ship": "devops",
            "land-and-deploy": "devops",
            "setup-deploy": "devops",
            "canary": "devops",
            "cso": "security",
            "investigate": "coding",
            "plan-design-review": "coding",
            "design-review": "coding",
            "plan-ceo-review": "research",
            "plan-eng-review": "coding",
            "autoplan": "coding",
            "benchmark": "coding",
            "retro": "research",
            "office-hours": "research",
            "document-release": "copywriting",
            "design-consultation": "coding",
            "design-shotgun": "coding",
        }
        return mapping.get(skill, "coding")
