"""
Secure Shell
Allowlist-enforced shell command execution with full audit logging.
"""

from __future__ import annotations

import subprocess
import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from able.core.security.command_guard import CommandAnalysis, CommandGuard, CommandVerdict


class ApprovalStatus(Enum):
    APPROVED = "approved"
    DENIED = "denied"
    PENDING = "pending"


@dataclass
class ShellResult:
    """Result of shell command execution"""
    command: str
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    approval_status: ApprovalStatus
    approval_reason: Optional[str] = None
    audit_id: Optional[str] = None


class SecureShell:
    """
    Secure shell command execution with:
    - CommandGuard allowlist enforcement
    - Approval workflow for sensitive commands
    - Full audit logging
    - Integration with v1 (~/.able/logs/audit/)
    """

    def __init__(
        self,
        trust_tier: int = 1,
        work_dir: Optional[Path] = None,
        timeout: int = 60,
        approval_callback: Optional[Callable[[str, CommandAnalysis], bool]] = None
    ):
        self.guard = CommandGuard(trust_tier=trust_tier)
        self.trust_tier = trust_tier
        self.work_dir = Path(work_dir) if work_dir else Path.cwd()
        self.timeout = timeout
        self.approval_callback = approval_callback

        # Audit log
        self.audit_log_path = Path(__file__).parent.parent.parent / "audit" / "logs" / "shell.jsonl"
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)

        # v1 audit log bridge
        self._v1_audit_path = Path.home() / ".able" / "logs" / "audit" / "audit.log"

        # Pending approvals
        self.pending_approvals: Dict[str, CommandAnalysis] = {}

    def _generate_audit_id(self, command: str) -> str:
        """Generate unique audit ID"""
        import hashlib
        timestamp = datetime.now(timezone.utc).isoformat()
        return hashlib.sha256(f"{timestamp}:{command}".encode()).hexdigest()[:16]

    def _log_audit(
        self,
        command: str,
        analysis: CommandAnalysis,
        result: Optional[ShellResult] = None
    ):
        """Log command to audit trail"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "audit_id": result.audit_id if result else self._generate_audit_id(command),
            "command": command,
            "verdict": analysis.verdict.value,
            "risk_level": analysis.risk_level,
            "reason": analysis.reason,
            "executed": result is not None and result.approval_status == ApprovalStatus.APPROVED,
            "exit_code": result.exit_code if result else None,
            "trust_tier": self.trust_tier
        }

        # Write to v2 audit log
        with open(self.audit_log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Write to v1 audit log if it exists
        if self._v1_audit_path.exists():
            v1_entry = f"[{entry['timestamp']}] ACTION=shell_command CMD={command[:50]} VERDICT={analysis.verdict.value} EXIT={entry['exit_code']}\n"
            with open(self._v1_audit_path, "a") as f:
                f.write(v1_entry)

    def _run_command(
        self,
        command: str,
        analysis: CommandAnalysis,
        audit_id: str,
        env: Optional[Dict[str, str]] = None,
        stdin: Optional[str] = None,
    ) -> ShellResult:
        """Execute a command after it has passed approval."""
        start_time = datetime.now()

        try:
            exec_env = os.environ.copy()
            if env:
                exec_env.update(env)

            # Strip binary hijack env vars (ported from Claude Code BashTool)
            for key in list(exec_env):
                if key.startswith(("LD_", "DYLD_")) or key == "PYTHONPATH":
                    exec_env.pop(key, None)

            argv = analysis.parsed_argv
            if analysis.uses_shell_syntax:
                argv = ["/bin/zsh", "-lc", command]

            proc = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.work_dir),
                env=exec_env,
                input=stdin,
            )

            execution_time = (datetime.now() - start_time).total_seconds()

            result = ShellResult(
                command=command,
                stdout=proc.stdout[:50000],
                stderr=proc.stderr[:50000],
                exit_code=proc.returncode,
                execution_time=execution_time,
                approval_status=ApprovalStatus.APPROVED,
                audit_id=audit_id,
            )

        except subprocess.TimeoutExpired:
            result = ShellResult(
                command=command,
                stdout="",
                stderr=f"Command timed out after {self.timeout} seconds",
                exit_code=-1,
                execution_time=self.timeout,
                approval_status=ApprovalStatus.APPROVED,
                audit_id=audit_id,
            )
        except Exception as e:
            result = ShellResult(
                command=command,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                execution_time=(datetime.now() - start_time).total_seconds(),
                approval_status=ApprovalStatus.APPROVED,
                audit_id=audit_id,
            )

        self._log_audit(command, analysis, result)
        return result

    def execute(
        self,
        command: str,
        env: Optional[Dict[str, str]] = None,
        stdin: Optional[str] = None
    ) -> ShellResult:
        """Execute a shell command with security checks"""
        audit_id = self._generate_audit_id(command)

        # Analyze command
        analysis = self.guard.analyze(command)

        # Handle denied commands
        if analysis.verdict == CommandVerdict.DENIED:
            result = ShellResult(
                command=command,
                stdout="",
                stderr=f"Command denied: {analysis.reason}",
                exit_code=-1,
                execution_time=0,
                approval_status=ApprovalStatus.DENIED,
                approval_reason=analysis.reason,
                audit_id=audit_id
            )
            self._log_audit(command, analysis, result)
            return result

        # Handle commands requiring approval
        if analysis.verdict == CommandVerdict.REQUIRES_APPROVAL:
            approved = False

            if self.approval_callback:
                approved = self.approval_callback(command, analysis)
            else:
                # Store for later approval
                self.pending_approvals[audit_id] = analysis
                result = ShellResult(
                    command=command,
                    stdout="",
                    stderr=f"Command requires approval: {analysis.reason}. Audit ID: {audit_id}",
                    exit_code=-1,
                    execution_time=0,
                    approval_status=ApprovalStatus.PENDING,
                    approval_reason=analysis.reason,
                    audit_id=audit_id
                )
                self._log_audit(command, analysis, result)
                return result

            if not approved:
                result = ShellResult(
                    command=command,
                    stdout="",
                    stderr=f"Command approval denied: {analysis.reason}",
                    exit_code=-1,
                    execution_time=0,
                    approval_status=ApprovalStatus.DENIED,
                    approval_reason="Approval denied by callback",
                    audit_id=audit_id
                )
                self._log_audit(command, analysis, result)
                return result

        return self._run_command(command, analysis, audit_id, env=env, stdin=stdin)

    def approve_pending(self, audit_id: str) -> Optional[ShellResult]:
        """Approve a pending command"""
        if audit_id not in self.pending_approvals:
            return None

        analysis = self.pending_approvals.pop(audit_id)
        return self._run_command(analysis.command, analysis, audit_id)

    def deny_pending(self, audit_id: str) -> bool:
        """Deny a pending command"""
        if audit_id in self.pending_approvals:
            del self.pending_approvals[audit_id]
            return True
        return False

    def list_pending(self) -> List[Dict[str, Any]]:
        """List pending approvals"""
        return [
            {
                "audit_id": audit_id,
                "command": analysis.command,
                "reason": analysis.reason,
                "risk_level": analysis.risk_level
            }
            for audit_id, analysis in self.pending_approvals.items()
        ]

    def get_recent_commands(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent command history from audit log"""
        commands = []

        if self.audit_log_path.exists():
            with open(self.audit_log_path) as f:
                for line in f:
                    try:
                        commands.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        return commands[-limit:]
