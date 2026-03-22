"""
Split Test Integration — A/B testing for evolution daemon weight changes.

Decides when proposed weight changes should be A/B tested vs direct deployed.
Manages proposal lifecycle: create → run → record outcomes → conclude.

Policy:
- Changes > 10% on any single weight -> split test required
- Changes to tier thresholds -> split test required
- Small changes (< 10%) across all weights -> direct deploy OK
- Security-related weight increases -> direct deploy (err on side of caution)
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class WeightChange:
    """A single weight delta within a split test proposal."""

    feature: str  # e.g. "requires_code_weight"
    old_value: float
    new_value: float
    delta_pct: float  # percentage change (0.15 = 15%)


@dataclass
class SplitTestProposal:
    """An A/B test proposal for a set of weight changes."""

    id: str
    changes: list[WeightChange]
    reason: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "pending"  # pending | running | concluded | rejected
    control_config: dict = field(default_factory=dict)
    experiment_config: dict = field(default_factory=dict)
    results: dict | None = None


# Keys that count as security-related weights
_SECURITY_KEYS = frozenset({
    "safety_critical_weight",
    "security",
})


class EvolutionSplitTestPolicy:
    """Decides when to A/B test vs direct deploy for weight changes.

    Policy:
    - Changes > threshold on any single weight -> split test required
    - Changes to tier thresholds -> split test required
    - Small changes (< threshold) across all weights -> direct deploy OK
    - Security-related weight increases -> direct deploy (err on side of caution)
    """

    def __init__(
        self,
        min_samples: int = 30,
        split_test_threshold: float = 0.10,
        proposals_dir: str | None = None,
    ):
        self.min_samples = min_samples
        self.split_test_threshold = split_test_threshold
        self.proposals_dir = proposals_dir or os.path.expanduser(
            "~/.atlas/evolution/proposals"
        )
        os.makedirs(self.proposals_dir, exist_ok=True)

    # ── Policy Decision ─────────────────────────────────────────

    def should_split_test(self, improvements: list[dict]) -> bool:
        """Return True if any improvement exceeds the threshold.

        Bypasses split testing for security weight *increases* — we always
        want to deploy those immediately.

        Args:
            improvements: list of Improvement-like dicts or objects with
                          .target, .change_pct (or dict equivalents).
        """
        for imp in improvements:
            target = _attr_or_key(imp, "target", "")
            change_pct = abs(_attr_or_key(imp, "change_pct", 0.0))

            # Security weight increases bypass split testing
            if self._is_security_increase(imp):
                continue

            # Tier threshold changes always require split testing
            if "tier_thresholds" in target:
                return True

            # Large changes require split testing
            if change_pct > self.split_test_threshold:
                return True

        return False

    # ── Proposal Lifecycle ──────────────────────────────────────

    def create_proposal(
        self,
        improvements: list[dict],
        current_weights: dict,
        reason: str,
    ) -> SplitTestProposal:
        """Create a split test proposal from proposed improvements."""
        changes = []
        experiment_weights = copy.deepcopy(current_weights)

        for imp in improvements:
            target = _attr_or_key(imp, "target", "")
            current_val = _attr_or_key(imp, "current_value", 0.0)
            proposed_val = _attr_or_key(imp, "proposed_value", 0.0)
            change_pct = _attr_or_key(imp, "change_pct", 0.0)

            changes.append(WeightChange(
                feature=target,
                old_value=current_val,
                new_value=proposed_val,
                delta_pct=change_pct,
            ))

            # Apply change to experiment config
            parts = target.split(".")
            node = experiment_weights
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = round(proposed_val, 4)

        proposal_id = self._generate_id(changes)
        proposal = SplitTestProposal(
            id=proposal_id,
            changes=changes,
            reason=reason,
            status="running",
            control_config=copy.deepcopy(current_weights),
            experiment_config=experiment_weights,
            results={"control": [], "experiment": []},
        )
        self._save_proposal(proposal)
        return proposal

    def assign_group(self, proposal_id: str, request_hash: str) -> str:
        """Assign request to 'control' or 'experiment' group.

        Uses consistent hashing so the same request always lands in the
        same bucket.
        """
        digest = hashlib.sha256(
            f"{proposal_id}:{request_hash}".encode()
        ).hexdigest()
        return "experiment" if int(digest, 16) % 2 == 0 else "control"

    def record_outcome(
        self,
        proposal_id: str,
        group: str,
        success: bool,
        latency_ms: int = 0,
    ):
        """Record outcome for a request in a split test."""
        proposals = self._load_proposals(status="running")
        for proposal in proposals:
            if proposal.id == proposal_id:
                if proposal.results is None:
                    proposal.results = {"control": [], "experiment": []}
                proposal.results.setdefault(group, []).append({
                    "success": success,
                    "latency_ms": latency_ms,
                })
                self._save_proposal(proposal)
                return

        logger.warning(f"No running proposal found with id {proposal_id}")

    def check_running_tests(self) -> list[dict]:
        """Check all running proposals. Auto-conclude if min_samples reached."""
        concluded = []
        for proposal in self._load_proposals(status="running"):
            results = proposal.results or {}
            control_n = len(results.get("control", []))
            experiment_n = len(results.get("experiment", []))

            if control_n >= self.min_samples and experiment_n >= self.min_samples:
                result = self.conclude_test(proposal.id)
                concluded.append(result)

        return concluded

    def conclude_test(self, proposal_id: str) -> dict:
        """Conclude a test. Returns winner with statistics."""
        proposals = self._load_proposals()
        for proposal in proposals:
            if proposal.id != proposal_id:
                continue

            results = proposal.results or {}
            control_outcomes = results.get("control", [])
            experiment_outcomes = results.get("experiment", [])

            control_rate = _success_rate(control_outcomes)
            experiment_rate = _success_rate(experiment_outcomes)

            winner = "experiment" if experiment_rate > control_rate else "control"

            summary = {
                "id": proposal_id,
                "winner": winner,
                "control_success_rate": control_rate,
                "experiment_success_rate": experiment_rate,
                "control_n": len(control_outcomes),
                "experiment_n": len(experiment_outcomes),
                "experiment_config": proposal.experiment_config,
            }

            proposal.status = "concluded"
            proposal.results = {
                **(proposal.results or {}),
                "summary": summary,
            }
            self._save_proposal(proposal)
            return summary

        return {"id": proposal_id, "error": "proposal not found"}

    def get_active_proposal(self) -> SplitTestProposal | None:
        """Get the currently active (running) split test, if any."""
        running = self._load_proposals(status="running")
        return running[0] if running else None

    # ── Persistence ─────────────────────────────────────────────

    def _save_proposal(self, proposal: SplitTestProposal):
        """Save proposal to disk as JSON."""
        path = Path(self.proposals_dir) / f"{proposal.id}.json"
        data = {
            "id": proposal.id,
            "changes": [
                {
                    "feature": c.feature,
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                    "delta_pct": c.delta_pct,
                }
                for c in proposal.changes
            ],
            "reason": proposal.reason,
            "created_at": proposal.created_at.isoformat(),
            "status": proposal.status,
            "control_config": proposal.control_config,
            "experiment_config": proposal.experiment_config,
            "results": proposal.results,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def _load_proposals(
        self, status: str | None = None
    ) -> list[SplitTestProposal]:
        """Load proposals from disk, optionally filtered by status."""
        proposals = []
        proposals_path = Path(self.proposals_dir)
        if not proposals_path.exists():
            return proposals

        for path in proposals_path.glob("*.json"):
            try:
                with open(path) as f:
                    data = json.load(f)
                proposal = SplitTestProposal(
                    id=data["id"],
                    changes=[
                        WeightChange(**c) for c in data.get("changes", [])
                    ],
                    reason=data.get("reason", ""),
                    created_at=datetime.fromisoformat(data["created_at"]),
                    status=data.get("status", "pending"),
                    control_config=data.get("control_config", {}),
                    experiment_config=data.get("experiment_config", {}),
                    results=data.get("results"),
                )
                if status is None or proposal.status == status:
                    proposals.append(proposal)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Skipping malformed proposal {path}: {e}")

        return proposals

    # ── Helpers ──────────────────────────────────────────────────

    def _is_security_increase(self, imp) -> bool:
        """Return True if this is a security-related weight increase."""
        target = _attr_or_key(imp, "target", "")
        change_pct = _attr_or_key(imp, "change_pct", 0.0)

        for key in _SECURITY_KEYS:
            if key in target and change_pct > 0:
                return True
        return False

    @staticmethod
    def _generate_id(changes: list[WeightChange]) -> str:
        """Deterministic proposal ID from the set of changes."""
        payload = json.dumps(
            [{"f": c.feature, "o": c.old_value, "n": c.new_value} for c in changes],
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
        ts = int(datetime.now(timezone.utc).timestamp())
        return f"split_{ts}_{digest}"


# ── Module-level helpers ────────────────────────────────────────


def _attr_or_key(obj, name: str, default=None):
    """Read a value from an object attribute or dict key."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _success_rate(outcomes: list[dict]) -> float:
    """Compute success rate from a list of outcome dicts."""
    if not outcomes:
        return 0.0
    successes = sum(1 for o in outcomes if o.get("success"))
    return successes / len(outcomes)
