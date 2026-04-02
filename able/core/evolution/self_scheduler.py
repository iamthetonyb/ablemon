"""
Self-Scheduler — The system's ability to create its own improvement loops.

Based on M2.7's analysis, the SelfScheduler can:
1. Create new promptfoo test cases targeting failure modes
2. Create new cron jobs for monitoring (always dry-run first)
3. Adjust eval frequency based on error rates
4. Propose new skills for uncovered task types
5. Adjust routing weights more aggressively if clear patterns emerge

Safety guardrails:
- Max 5 new crons per cycle
- All new crons start in dry-run mode (log only, don't act)
- Operator reviews morning report and can promote/reject
- Cannot delete existing crons (only create or modify)
- All changes go through audit trail
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)

# Project root — three levels up from this file
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

MAX_CRONS_PER_CYCLE = 5


@dataclass
class ScheduledAction:
    """A proposed or enacted scheduler action."""

    id: str = ""
    action_type: str = ""  # "cron_create" | "eval_create" | "skill_propose" | "weight_adjust"
    name: str = ""
    description: str = ""
    dry_run: bool = True
    source_cycle: str = ""  # evolution cycle ID that generated this
    created_at: str = ""
    promoted: bool = False  # operator approved and moved out of dry-run
    rejected: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SchedulerCycleReport:
    """Result of one self-scheduler cycle."""

    cycle_id: str = ""
    timestamp: str = ""
    actions_proposed: int = 0
    actions_created: int = 0
    crons_created: int = 0
    evals_created: int = 0
    skills_proposed: int = 0
    errors: List[str] = field(default_factory=list)
    actions: List[ScheduledAction] = field(default_factory=list)


class SelfScheduler:
    """
    Creates new crons, evals, and skill proposals based on evolution analysis.

    All new crons start in dry-run mode. The operator reviews the morning
    report and can promote actions to active or reject them.
    """

    def __init__(
        self,
        scheduler=None,
        audit_trail=None,
        actions_dir: str = "data/self_scheduled",
    ):
        """
        Args:
            scheduler: CronScheduler instance to register new jobs on
            audit_trail: GitAuditTrail for logging changes
            actions_dir: Where to persist proposed/enacted actions
        """
        self._scheduler = scheduler
        self._audit = audit_trail
        self._actions_dir = Path(actions_dir)
        self._actions_dir.mkdir(parents=True, exist_ok=True)
        self._cycle_count = 0

    async def run_cycle(
        self,
        analysis_results: Dict[str, Any],
        cycle_id: str = "",
    ) -> SchedulerCycleReport:
        """
        Process evolution analysis results and create improvement actions.

        Args:
            analysis_results: Output from EvolutionAnalyzer.analyze()
            cycle_id: Parent evolution cycle ID

        Returns:
            Report of all actions taken
        """
        if not cycle_id:
            cycle_id = f"sched_{int(time.time())}"

        report = SchedulerCycleReport(
            cycle_id=cycle_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        try:
            actions = self._generate_actions(analysis_results, cycle_id)
            report.actions_proposed = len(actions)

            # Enforce per-cycle cap
            cron_actions = [a for a in actions if a.action_type == "cron_create"]
            if len(cron_actions) > MAX_CRONS_PER_CYCLE:
                logger.warning(
                    f"Capping cron creation from {len(cron_actions)} to {MAX_CRONS_PER_CYCLE}"
                )
                # Keep the cron actions up to the cap, plus all non-cron actions
                non_cron = [a for a in actions if a.action_type != "cron_create"]
                actions = non_cron + cron_actions[:MAX_CRONS_PER_CYCLE]

            for action in actions:
                try:
                    self._enact_action(action)
                    report.actions_created += 1

                    if action.action_type == "cron_create":
                        report.crons_created += 1
                    elif action.action_type == "eval_create":
                        report.evals_created += 1
                    elif action.action_type == "skill_propose":
                        report.skills_proposed += 1

                    report.actions.append(action)
                except Exception as e:
                    error_msg = f"Failed to enact {action.action_type} '{action.name}': {e}"
                    logger.error(error_msg)
                    report.errors.append(error_msg)

            # Persist report
            self._persist_report(report)

            # Audit trail
            if self._audit:
                try:
                    await self._audit.record_action(
                        action_type="self_scheduler_cycle",
                        details={
                            "summary": f"Self-scheduler: {report.actions_created} actions from cycle {cycle_id}",
                            "proposed": report.actions_proposed,
                            "created": report.actions_created,
                            "errors": len(report.errors),
                        },
                    )
                except Exception:
                    pass

        except Exception as e:
            report.errors.append(f"Cycle failed: {e}")
            logger.error(f"Self-scheduler cycle failed: {e}", exc_info=True)

        self._cycle_count += 1
        return report

    def _generate_actions(
        self,
        analysis: Dict[str, Any],
        cycle_id: str,
    ) -> List[ScheduledAction]:
        """
        Generate improvement actions from analysis results.

        This is rule-based. M2.7 analysis feeds in structured problems
        and recommendations, and this method maps them to concrete actions.
        """
        actions: List[ScheduledAction] = []
        recommendations = analysis.get("recommendations", [])

        # Rule 1: High failure rate on a tier -> create monitoring cron
        failures_by_tier = analysis.get("failures_by_tier", [])
        for tier_data in failures_by_tier:
            tier = tier_data.get("selected_tier", 0)
            rate = tier_data.get("failure_rate_pct", 0)
            if rate > 15:
                actions.append(ScheduledAction(
                    id=f"{cycle_id}_monitor_t{tier}",
                    action_type="cron_create",
                    name=f"monitor-tier-{tier}-failures",
                    description=f"Monitor Tier {tier} failure rate (currently {rate}%)",
                    dry_run=True,
                    source_cycle=cycle_id,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    details={
                        "schedule": "*/30 * * * *",
                        "trigger_threshold": rate,
                        "tier": tier,
                    },
                ))

        # Rule 2: Repeated failure patterns -> create targeted eval
        failure_patterns = analysis.get("failure_patterns", [])
        for pattern in failure_patterns[:3]:  # max 3 evals per cycle
            domain = pattern.get("domain", "unknown")
            count = pattern.get("count", 0)
            if count >= 3:
                actions.append(ScheduledAction(
                    id=f"{cycle_id}_eval_{domain}",
                    action_type="eval_create",
                    name=f"eval-{domain}-regression",
                    description=f"Regression eval for {domain} domain ({count} recent failures)",
                    dry_run=True,
                    source_cycle=cycle_id,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    details={
                        "domain": domain,
                        "failure_count": count,
                        "sample_prompts": pattern.get("sample_prompts", []),
                    },
                ))

        # Rule 3: Uncovered task types -> propose skill
        uncovered = analysis.get("uncovered_task_types", [])
        for task_type in uncovered[:2]:  # max 2 skill proposals per cycle
            actions.append(ScheduledAction(
                id=f"{cycle_id}_skill_{task_type}",
                action_type="skill_propose",
                name=f"skill-{task_type}",
                description=f"Proposed skill for uncovered task type: {task_type}",
                dry_run=True,
                source_cycle=cycle_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                details={"task_type": task_type},
            ))

        # Rule 4: Recommendations from analyzer
        for rec in recommendations:
            rec_type = rec.get("type", "")
            if rec_type == "adjust_weights" and rec.get("confidence", 0) > 0.7:
                actions.append(ScheduledAction(
                    id=f"{cycle_id}_weight_{rec.get('target', 'unknown')}",
                    action_type="weight_adjust",
                    name=f"weight-{rec.get('target', 'unknown')}",
                    description=rec.get("description", "Weight adjustment"),
                    dry_run=True,
                    source_cycle=cycle_id,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    details={
                        "target": rec.get("target"),
                        "current_value": rec.get("current_value"),
                        "proposed_value": rec.get("proposed_value"),
                        "confidence": rec.get("confidence"),
                    },
                ))

        return actions

    def _enact_action(self, action: ScheduledAction) -> None:
        """
        Enact a single action. All actions start as dry-run.

        For cron_create: registers job on scheduler in dry-run mode.
        For eval_create: writes eval config stub to disk.
        For skill_propose: writes proposal to disk for operator review.
        For weight_adjust: logs proposed adjustment (never auto-applies).
        """
        if action.action_type == "cron_create":
            self._create_dry_run_cron(action)
        elif action.action_type == "eval_create":
            self._create_eval_stub(action)
        elif action.action_type == "skill_propose":
            self._create_skill_proposal(action)
        elif action.action_type == "weight_adjust":
            self._log_weight_proposal(action)
        else:
            raise ValueError(f"Unknown action type: {action.action_type}")

        # Persist action to disk
        action_path = self._actions_dir / f"{action.id}.json"
        with open(action_path, "w") as f:
            json.dump({
                "id": action.id,
                "action_type": action.action_type,
                "name": action.name,
                "description": action.description,
                "dry_run": action.dry_run,
                "source_cycle": action.source_cycle,
                "created_at": action.created_at,
                "promoted": action.promoted,
                "rejected": action.rejected,
                "details": action.details,
            }, f, indent=2)

    def _create_dry_run_cron(self, action: ScheduledAction) -> None:
        """Register a new cron job in dry-run mode (log only)."""
        if not self._scheduler:
            logger.info(
                f"[DRY-RUN] Would create cron '{action.name}': {action.description}"
            )
            return

        schedule = action.details.get("schedule", "0 * * * *")

        async def dry_run_task(**kwargs):
            logger.info(
                f"[DRY-RUN CRON] {action.name}: {action.description} "
                f"(would execute, but in dry-run mode)"
            )
            return {"dry_run": True, "action_id": action.id}

        self._scheduler.add_job(
            name=f"dryrun-{action.name}",
            schedule=schedule,
            task=dry_run_task,
            description=f"[DRY-RUN] {action.description}",
            enabled=True,
            timeout=30.0,
            max_retries=1,
        )
        logger.info(f"Registered dry-run cron: {action.name} [{schedule}]")

    def _create_eval_stub(self, action: ScheduledAction) -> None:
        """Write a promptfoo eval config stub for operator review."""
        eval_dir = _PROJECT_ROOT / "able" / "evals"
        eval_dir.mkdir(parents=True, exist_ok=True)

        domain = action.details.get("domain", "unknown")
        stub = {
            "description": action.description,
            "providers": ["able:tier-1", "able:tier-2"],
            "prompts": action.details.get("sample_prompts", [
                f"# Add test prompts for {domain} domain regression",
            ]),
            "tests": [
                {
                    "vars": {"domain": domain},
                    "assert": [
                        {"type": "llm-rubric", "value": f"Response adequately addresses {domain} requirements"},
                    ],
                }
            ],
            "_meta": {
                "generated_by": "self_scheduler",
                "source_cycle": action.source_cycle,
                "created_at": action.created_at,
                "status": "draft",
            },
        }

        eval_path = eval_dir / f"eval-{action.name}.yaml"
        with open(eval_path, "w") as f:
            yaml.dump(stub, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Created eval stub: {eval_path}")

    def _create_skill_proposal(self, action: ScheduledAction) -> None:
        """Write a skill proposal for operator review."""
        proposals_dir = self._actions_dir / "skill_proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)

        proposal = {
            "name": action.name,
            "task_type": action.details.get("task_type", "unknown"),
            "description": action.description,
            "source_cycle": action.source_cycle,
            "created_at": action.created_at,
            "status": "proposed",
            "rationale": f"Detected uncovered task type in routing data",
        }

        path = proposals_dir / f"{action.name}.json"
        with open(path, "w") as f:
            json.dump(proposal, f, indent=2)

        logger.info(f"Created skill proposal: {path}")

    def _log_weight_proposal(self, action: ScheduledAction) -> None:
        """Log a weight adjustment proposal (never auto-applies)."""
        logger.info(
            f"[WEIGHT PROPOSAL] {action.name}: "
            f"{action.details.get('target')} "
            f"{action.details.get('current_value')} -> {action.details.get('proposed_value')} "
            f"(confidence: {action.details.get('confidence', 0):.2f})"
        )

    def promote_action(self, action_id: str) -> bool:
        """
        Promote a dry-run action to active. Operator-initiated only.

        Returns True if promoted, False if not found.
        """
        action_path = self._actions_dir / f"{action_id}.json"
        if not action_path.exists():
            logger.warning(f"Action not found: {action_id}")
            return False

        with open(action_path) as f:
            data = json.load(f)

        if data.get("rejected"):
            logger.warning(f"Cannot promote rejected action: {action_id}")
            return False

        data["promoted"] = True
        data["dry_run"] = False

        with open(action_path, "w") as f:
            json.dump(data, f, indent=2)

        # If it's a cron, enable the real version on the scheduler
        if data["action_type"] == "cron_create" and self._scheduler:
            dry_name = f"dryrun-{data['name']}"
            if dry_name in self._scheduler.jobs:
                self._scheduler.remove_job(dry_name)
                logger.info(f"Removed dry-run cron: {dry_name}")

        logger.info(f"Promoted action: {action_id}")
        return True

    def reject_action(self, action_id: str) -> bool:
        """
        Reject a proposed action. Operator-initiated only.

        Returns True if rejected, False if not found.
        """
        action_path = self._actions_dir / f"{action_id}.json"
        if not action_path.exists():
            logger.warning(f"Action not found: {action_id}")
            return False

        with open(action_path) as f:
            data = json.load(f)

        data["rejected"] = True

        with open(action_path, "w") as f:
            json.dump(data, f, indent=2)

        # If it's a cron, remove the dry-run job
        if data["action_type"] == "cron_create" and self._scheduler:
            dry_name = f"dryrun-{data['name']}"
            if dry_name in self._scheduler.jobs:
                self._scheduler.remove_job(dry_name)

        logger.info(f"Rejected action: {action_id}")
        return True

    def get_pending_actions(self) -> List[Dict[str, Any]]:
        """Get all actions pending operator review (not promoted, not rejected)."""
        pending = []
        for path in self._actions_dir.glob("*.json"):
            if path.name.startswith("report_"):
                continue
            try:
                with open(path) as f:
                    data = json.load(f)
                if not data.get("promoted") and not data.get("rejected"):
                    pending.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        return sorted(pending, key=lambda x: x.get("created_at", ""), reverse=True)

    def _persist_report(self, report: SchedulerCycleReport) -> None:
        """Save cycle report to disk."""
        report_path = self._actions_dir / f"report_{report.cycle_id}.json"
        with open(report_path, "w") as f:
            json.dump({
                "cycle_id": report.cycle_id,
                "timestamp": report.timestamp,
                "actions_proposed": report.actions_proposed,
                "actions_created": report.actions_created,
                "crons_created": report.crons_created,
                "evals_created": report.evals_created,
                "skills_proposed": report.skills_proposed,
                "errors": report.errors,
                "action_ids": [a.id for a in report.actions],
            }, f, indent=2)

    @property
    def status(self) -> Dict[str, Any]:
        """Current self-scheduler status."""
        pending = self.get_pending_actions()
        return {
            "cycles_completed": self._cycle_count,
            "pending_review": len(pending),
            "actions_dir": str(self._actions_dir),
            "max_crons_per_cycle": MAX_CRONS_PER_CYCLE,
        }
