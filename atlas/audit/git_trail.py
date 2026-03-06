"""
Git-based Audit Trail — ThePopeBot Pattern.

Every significant action creates a git commit, providing:
- Full reversibility (git revert)
- Transparency (git log)
- Tamper evidence (commit hashes)

Usage:
    from atlas.audit.git_trail import GitAuditTrail

    trail = GitAuditTrail(Path("~/.atlas"))
    await trail.record_action("file_write", {"path": "/foo", "bytes": 1024})
"""

import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import git
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    logger.warning("GitPython not installed — git audit trail unavailable. Run: pip install gitpython")


class GitAuditTrail:
    """
    Commit-based audit trail for reversibility and transparency.

    Every action recorded as:
    1. JSONL entry in audit/{date}.jsonl
    2. Git commit with structured message

    Benefits:
    - `git log --oneline` shows action history
    - `git revert <sha>` undoes any action
    - `git diff <sha1>..<sha2>` shows changes between actions
    - Tamper-evident (commit hashes)
    """

    def __init__(self, repo_path: Path, auto_commit: bool = True):
        """
        Args:
            repo_path: Path to git repository (e.g., ~/.atlas)
            auto_commit: If True, commit after each action (default True)
        """
        self.repo_path = Path(repo_path).expanduser().resolve()
        self.auto_commit = auto_commit
        self.audit_dir = self.repo_path / "logs" / "audit"
        self.audit_dir.mkdir(parents=True, exist_ok=True)

        self._repo: Optional["git.Repo"] = None

    @property
    def repo(self) -> Optional["git.Repo"]:
        """Lazy-load git repo"""
        if not GIT_AVAILABLE:
            return None
        if self._repo is None:
            try:
                self._repo = git.Repo(self.repo_path)
            except git.InvalidGitRepositoryError:
                # Initialize if not a repo
                self._repo = git.Repo.init(self.repo_path)
                logger.info(f"Initialized git repo at {self.repo_path}")
        return self._repo

    async def record_action(
        self,
        action_type: str,
        details: Dict[str, Any],
        files_changed: Optional[List[Path]] = None,
        trace_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Record an action to the audit trail.

        Args:
            action_type: Type of action (e.g., "file_write", "api_call", "skill_execute")
            details: Action-specific details
            files_changed: Optional list of files affected by this action
            trace_id: Optional trace ID for correlation

        Returns:
            Commit SHA if auto_commit enabled and repo available, else None
        """
        record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action": action_type,
            "details": details,
            "trace_id": trace_id,
        }
        if files_changed:
            record["files"] = [str(f) for f in files_changed]

        # Write to daily JSONL file
        today_file = self.audit_dir / f"{date.today()}.jsonl"
        line = json.dumps(record, separators=(",", ":")) + "\n"

        # Use asyncio file write for non-blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._write_record, today_file, line)

        # Git commit if enabled
        if self.auto_commit and self.repo:
            summary = details.get("summary", action_type)
            return await self._commit_action(action_type, summary, today_file, files_changed)

        return None

    def _write_record(self, path: Path, line: str):
        """Sync file write (run in executor)"""
        with open(path, "a") as f:
            f.write(line)

    async def _commit_action(
        self,
        action_type: str,
        summary: str,
        audit_file: Path,
        files_changed: Optional[List[Path]] = None,
    ) -> Optional[str]:
        """Create git commit for the action"""
        if not self.repo:
            return None

        try:
            # Stage audit file
            self.repo.index.add([str(audit_file.relative_to(self.repo_path))])

            # Stage changed files if any
            if files_changed:
                for f in files_changed:
                    try:
                        rel_path = f.relative_to(self.repo_path)
                        self.repo.index.add([str(rel_path)])
                    except ValueError:
                        # File outside repo
                        pass

            # Commit
            message = f"[AUDIT] {action_type}: {summary}"
            commit = self.repo.index.commit(message)
            logger.debug(f"Audit commit: {commit.hexsha[:8]} — {message}")
            return commit.hexsha

        except Exception as e:
            logger.error(f"Git commit failed: {e}")
            return None

    async def get_recent_actions(
        self,
        limit: int = 50,
        action_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get recent actions from audit trail.

        Args:
            limit: Maximum number of actions to return
            action_type: Filter by action type (optional)

        Returns:
            List of action records, newest first
        """
        actions = []

        # Read from today's file first, then previous days
        for day_offset in range(7):  # Check last 7 days
            file_date = date.today().replace(day=date.today().day - day_offset)
            audit_file = self.audit_dir / f"{file_date}.jsonl"

            if not audit_file.exists():
                continue

            with open(audit_file) as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        if action_type and record.get("action") != action_type:
                            continue
                        actions.append(record)
                    except json.JSONDecodeError:
                        continue

            if len(actions) >= limit:
                break

        # Sort by timestamp descending and limit
        actions.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return actions[:limit]

    async def revert_action(self, commit_sha: str) -> bool:
        """
        Revert a specific action by commit SHA.

        Args:
            commit_sha: The commit SHA to revert

        Returns:
            True if revert succeeded
        """
        if not self.repo:
            logger.error("Git repo not available for revert")
            return False

        try:
            self.repo.git.revert(commit_sha, no_edit=True)
            logger.info(f"Reverted commit: {commit_sha}")
            return True
        except Exception as e:
            logger.error(f"Revert failed: {e}")
            return False

    def get_action_history(self, limit: int = 20) -> List[Dict[str, str]]:
        """
        Get action history from git log.

        Returns list of: {"sha": "abc123", "message": "[AUDIT] ...", "date": "..."}
        """
        if not self.repo:
            return []

        history = []
        try:
            for commit in self.repo.iter_commits(max_count=limit):
                if commit.message.startswith("[AUDIT]"):
                    history.append({
                        "sha": commit.hexsha,
                        "message": commit.message.strip(),
                        "date": commit.committed_datetime.isoformat(),
                    })
        except Exception as e:
            logger.error(f"Failed to read git history: {e}")

        return history

    # ── Popebot-Inspired Enhancements ────────────────────────────────────────

    async def record_job(
        self,
        job_id: str,
        action: str,
        details: Dict[str, Any],
        files_changed: Optional[List[Path]] = None,
    ) -> Optional[str]:
        """
        Record a job action with structured commit message (popebot pattern).

        Uses the "repository IS the agent" philosophy — every significant
        agent action is a git commit for full auditability and reversibility.

        Args:
            job_id: Unique job identifier (e.g., "skill-create-2026-03-05")
            action: Action verb (e.g., "started", "completed", "failed")
            details: Job-specific details
            files_changed: Files affected by this job step
        """
        record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "job_id": job_id,
            "action": action,
            "details": details,
        }
        if files_changed:
            record["files"] = [str(f) for f in files_changed]

        # Write to daily JSONL
        today_file = self.audit_dir / f"{date.today()}.jsonl"
        line = json.dumps(record, separators=(",", ":")) + "\n"

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._write_record, today_file, line)

        # Structured commit message (popebot-style)
        if self.auto_commit and self.repo:
            summary = details.get("summary", action)
            commit_msg = f"[JOB:{job_id}] {action}: {summary}"
            return await self._commit_with_message(commit_msg, today_file, files_changed)

        return None

    async def _commit_with_message(
        self,
        message: str,
        audit_file: Path,
        files_changed: Optional[List[Path]] = None,
    ) -> Optional[str]:
        """Create git commit with custom message"""
        if not self.repo:
            return None

        try:
            self.repo.index.add([str(audit_file.relative_to(self.repo_path))])

            if files_changed:
                for f in files_changed:
                    try:
                        rel_path = f.relative_to(self.repo_path)
                        self.repo.index.add([str(rel_path)])
                    except ValueError:
                        pass

            commit = self.repo.index.commit(message)
            logger.debug(f"Job commit: {commit.hexsha[:8]} — {message}")
            return commit.hexsha

        except Exception as e:
            logger.error(f"Job commit failed: {e}")
            return None

    def get_job_history(self, job_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, str]]:
        """
        Get job history from git log, optionally filtered by job_id.

        Popebot-style: every job action is traceable via git log.
        """
        if not self.repo:
            return []

        history = []
        prefix = f"[JOB:{job_id}]" if job_id else "[JOB:"
        try:
            for commit in self.repo.iter_commits(max_count=limit * 2):
                msg = commit.message.strip()
                if msg.startswith("[AUDIT]") or msg.startswith(prefix):
                    history.append({
                        "sha": commit.hexsha,
                        "message": msg,
                        "date": commit.committed_datetime.isoformat(),
                        "files": [d.a_path for d in commit.diff(commit.parents[0])] if commit.parents else [],
                    })
                    if len(history) >= limit:
                        break
        except Exception as e:
            logger.error(f"Failed to read job history: {e}")

        return history

    def get_dashboard_summary(self) -> Dict[str, Any]:
        """
        Generate a dashboard-ready summary of recent audit activity.

        Returns counts and recent entries for the status endpoint.
        """
        summary = {
            "total_actions_today": 0,
            "total_jobs_today": 0,
            "recent_actions": [],
            "action_types": {},
        }

        today_file = self.audit_dir / f"{date.today()}.jsonl"
        if not today_file.exists():
            return summary

        try:
            with open(today_file) as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        action = record.get("action", "unknown")
                        summary["total_actions_today"] += 1
                        summary["action_types"][action] = summary["action_types"].get(action, 0) + 1

                        if "job_id" in record:
                            summary["total_jobs_today"] += 1

                        summary["recent_actions"].append({
                            "time": record.get("timestamp", ""),
                            "action": action,
                            "summary": record.get("details", {}).get("summary", ""),
                        })
                    except json.JSONDecodeError:
                        continue

            # Keep only last 10 recent actions
            summary["recent_actions"] = summary["recent_actions"][-10:]

        except Exception as e:
            logger.error(f"Failed to build dashboard summary: {e}")

        return summary
