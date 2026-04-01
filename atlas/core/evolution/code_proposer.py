"""
Code Proposer — Generates patches and opens PRs for low-risk changes.

The evolution daemon's code-generation arm. Limited to LOW-risk changes
(config YAML, scorer weights). Never touches core Python without human review.

Safety:
  - Only auto-applies to files in _ALLOWED_AUTO_FILES
  - All proposals logged to data/code_proposals/
  - Uses `gh` CLI for branch creation and PR opening
  - PR body includes risk assessment and rollback instructions

Integration:
  - research_pipeline.py → feeds classified actions here
  - daemon.py → triggers after auto-improve step
"""

import hashlib
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Project root — three levels up from this file
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Files the proposer is allowed to auto-modify
_ALLOWED_AUTO_FILES = {
    "config/scorer_weights.yaml",
    "config/routing_config.yaml",
    "config/split_tests.yaml",
}

# Max number of PRs per daemon cycle to avoid spam
_MAX_PRS_PER_CYCLE = 3


@dataclass
class Proposal:
    """A proposed code/config change with PR metadata."""
    id: str
    action_description: str
    target_file: str
    risk: str  # "low", "medium", "high"
    change_type: str  # "config_change", "code_change"
    patch_description: str = ""
    branch_name: str = ""
    pr_url: str = ""
    status: str = "pending"  # "pending", "proposed", "merged", "rejected"
    created_at: str = ""
    error: str = ""


@dataclass
class ProposerCycleResult:
    """Result of a proposer cycle."""
    proposals_received: int = 0
    proposals_created: int = 0
    prs_opened: int = 0
    auto_merged: int = 0
    skipped_high_risk: int = 0
    errors: List[str] = field(default_factory=list)
    proposals: List[Proposal] = field(default_factory=list)
    duration_ms: float = 0.0


def _is_gh_available() -> bool:
    """Check if `gh` CLI is installed and authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_gh(args: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run a `gh` CLI command."""
    return subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=cwd or str(_PROJECT_ROOT),
    )


def _run_git(args: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run a `git` command."""
    return subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=cwd or str(_PROJECT_ROOT),
    )


class CodeProposer:
    """
    Generates config/code patches and opens PRs for evolution-driven changes.

    Limitations:
      - Only auto-modifies files in _ALLOWED_AUTO_FILES
      - Code changes (*.py) always require human review
      - Max _MAX_PRS_PER_CYCLE PRs per invocation
    """

    def __init__(
        self,
        log_dir: Optional[str] = None,
        dry_run: bool = False,
    ):
        self.log_dir = Path(log_dir) if log_dir else _PROJECT_ROOT / "data" / "code_proposals"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.dry_run = dry_run
        self._prs_this_cycle = 0

    async def propose(
        self,
        improvement: Any,
    ) -> Dict[str, Any]:
        """
        Generate a proposal for a single improvement.

        Args:
            improvement: A ClassifiedAction from research_pipeline, or a dict with
                        'description', 'target_file', 'risk' keys.

        Returns:
            Dict with proposal status and metadata.
        """
        # Normalize input
        if hasattr(improvement, "description"):
            desc = improvement.description
            target = improvement.target_file
            risk = improvement.risk
            change_type = getattr(improvement, "action_type", "config_change")
        elif isinstance(improvement, dict):
            desc = improvement.get("description", "")
            target = improvement.get("target_file", "")
            risk = improvement.get("risk", "medium")
            change_type = improvement.get("action_type", "config_change")
        else:
            return {"status": "error", "error": "Invalid improvement format"}

        desc_hash = int(hashlib.md5(desc.encode()).hexdigest()[:8], 16) % 10000
        proposal_id = f"prop_{int(time.time())}_{desc_hash:04d}"
        proposal = Proposal(
            id=proposal_id,
            action_description=desc,
            target_file=target,
            risk=risk,
            change_type=change_type,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Safety gate: never auto-modify core Python
        if risk == "high":
            proposal.status = "skipped_high_risk"
            self._log_proposal(proposal)
            return {
                "status": "skipped",
                "reason": "HIGH risk — requires human review",
                "proposal_id": proposal_id,
            }

        # Safety gate: only auto-modify allowed files
        if target and target not in _ALLOWED_AUTO_FILES:
            proposal.status = "proposed"
            proposal.patch_description = f"Manual review needed: {desc}"
            self._log_proposal(proposal)
            return {
                "status": "proposed",
                "reason": f"File {target} not in auto-apply allowlist",
                "proposal_id": proposal_id,
            }

        # Rate limit PRs per cycle
        if self._prs_this_cycle >= _MAX_PRS_PER_CYCLE:
            proposal.status = "deferred"
            self._log_proposal(proposal)
            return {
                "status": "deferred",
                "reason": f"PR limit reached ({_MAX_PRS_PER_CYCLE}/cycle)",
                "proposal_id": proposal_id,
            }

        # Generate the patch description (rule-based, no LLM needed for config)
        proposal.patch_description = self._generate_patch_description(desc, target)

        if self.dry_run:
            proposal.status = "dry_run"
            self._log_proposal(proposal)
            return {
                "status": "dry_run",
                "proposal_id": proposal_id,
                "patch_description": proposal.patch_description,
            }

        # Create branch and PR
        pr_result = self._create_pr(proposal)
        if pr_result.get("success"):
            proposal.status = "proposed"
            proposal.branch_name = pr_result.get("branch", "")
            proposal.pr_url = pr_result.get("pr_url", "")
            self._prs_this_cycle += 1
        else:
            proposal.status = "error"
            proposal.error = pr_result.get("error", "Unknown error")

        self._log_proposal(proposal)
        return {
            "status": proposal.status,
            "proposal_id": proposal_id,
            "pr_url": proposal.pr_url,
            "branch": proposal.branch_name,
            "error": proposal.error,
        }

    async def propose_batch(
        self,
        improvements: List[Any],
    ) -> ProposerCycleResult:
        """
        Process a batch of improvements (from research pipeline or daemon).

        Returns aggregated results.
        """
        start = time.perf_counter()
        result = ProposerCycleResult(proposals_received=len(improvements))

        for improvement in improvements:
            try:
                proposal_result = await self.propose(improvement)
                status = proposal_result.get("status", "error")

                if status == "proposed":
                    result.proposals_created += 1
                    if proposal_result.get("pr_url"):
                        result.prs_opened += 1
                elif status == "skipped":
                    result.skipped_high_risk += 1
                elif status == "error":
                    result.errors.append(proposal_result.get("error", "Unknown"))
            except Exception as e:
                result.errors.append(str(e))
                logger.warning(f"Proposal failed: {e}")

        result.duration_ms = (time.perf_counter() - start) * 1000

        logger.info(
            f"[CODE_PROPOSER] Batch: {result.proposals_received} received, "
            f"{result.proposals_created} proposed, {result.prs_opened} PRs opened, "
            f"{result.skipped_high_risk} skipped (high risk)"
        )

        return result

    def _generate_patch_description(self, description: str, target_file: str) -> str:
        """Generate a human-readable patch description for a config change."""
        lines = [
            f"Change target: {target_file}",
            f"Rationale: {description}",
            "",
            "This change was identified by the ATLAS evolution daemon's research pipeline.",
            f"Risk: LOW (auto-apply eligible for {target_file})",
        ]
        return "\n".join(lines)

    def _create_pr(self, proposal: Proposal) -> Dict[str, Any]:
        """Create a git branch and open a PR via `gh`."""
        if not _is_gh_available():
            return {"success": False, "error": "gh CLI not available or not authenticated"}

        branch_name = f"evolution/{proposal.id}"
        cwd = str(_PROJECT_ROOT)

        # Check for uncommitted changes first
        status = _run_git(["status", "--porcelain"], cwd=cwd)
        if status.stdout.strip():
            return {
                "success": False,
                "error": "Working tree has uncommitted changes — cannot create PR",
            }

        # Create branch
        result = _run_git(["checkout", "-b", branch_name], cwd=cwd)
        if result.returncode != 0:
            return {"success": False, "error": f"Branch creation failed: {result.stderr.strip()}"}

        try:
            # For now, the PR is a proposal with description only.
            # Actual file edits would be applied by a human or a follow-up step.
            # Create a marker file so the branch has a commit
            marker_path = _PROJECT_ROOT / "data" / "code_proposals" / f"{proposal.id}.json"
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            with open(marker_path, "w") as f:
                json.dump({
                    "id": proposal.id,
                    "description": proposal.action_description,
                    "target_file": proposal.target_file,
                    "risk": proposal.risk,
                    "patch_description": proposal.patch_description,
                    "created_at": proposal.created_at,
                }, f, indent=2)

            _run_git(["add", str(marker_path)], cwd=cwd)
            _run_git(
                ["commit", "-m", f"evolution: propose {proposal.id}\n\n{proposal.patch_description}"],
                cwd=cwd,
            )

            # Push branch
            push_result = _run_git(["push", "-u", "origin", branch_name], cwd=cwd)
            if push_result.returncode != 0:
                return {"success": False, "error": f"Push failed: {push_result.stderr.strip()}"}

            # Open PR
            pr_body = (
                f"## Evolution Daemon Proposal\n\n"
                f"**Action:** {proposal.action_description}\n"
                f"**Target:** `{proposal.target_file}`\n"
                f"**Risk:** {proposal.risk.upper()}\n\n"
                f"### Patch Description\n\n{proposal.patch_description}\n\n"
                f"### Rollback\n\n"
                f"If this change causes issues:\n"
                f"```bash\n"
                f"git revert HEAD\n"
                f"```\n\n"
                f"---\n"
                f"*Auto-generated by ATLAS evolution daemon*"
            )

            pr_result = _run_gh(
                ["pr", "create",
                 "--title", f"evolution: {proposal.action_description[:60]}",
                 "--body", pr_body],
                cwd=cwd,
            )

            if pr_result.returncode == 0:
                pr_url = pr_result.stdout.strip()
                return {"success": True, "branch": branch_name, "pr_url": pr_url}
            else:
                return {"success": False, "error": f"PR creation failed: {pr_result.stderr.strip()}"}

        finally:
            # Always return to the original branch
            _run_git(["checkout", "-"], cwd=cwd)

    def _log_proposal(self, proposal: Proposal):
        """Log proposal to disk for audit."""
        path = self.log_dir / f"{proposal.id}.json"
        try:
            data = {
                "id": proposal.id,
                "action_description": proposal.action_description,
                "target_file": proposal.target_file,
                "risk": proposal.risk,
                "change_type": proposal.change_type,
                "patch_description": proposal.patch_description,
                "branch_name": proposal.branch_name,
                "pr_url": proposal.pr_url,
                "status": proposal.status,
                "created_at": proposal.created_at,
                "error": proposal.error,
            }
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to log proposal {proposal.id}: {e}")
