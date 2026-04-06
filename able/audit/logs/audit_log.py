"""
Audit Log
Central audit logging for all ABLE operations.
Syncs with v1 (~/.able/logs/audit/).
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from enum import Enum


class AuditAction(Enum):
    # Security
    SECURITY_CHECK = "security_check"
    INJECTION_DETECTED = "injection_detected"
    COMMAND_BLOCKED = "command_blocked"
    COMMAND_APPROVED = "command_approved"

    # Agent operations
    AGENT_SCAN = "agent_scan"
    AGENT_AUDIT = "agent_audit"
    AGENT_EXECUTE = "agent_execute"

    # Client operations
    CLIENT_MESSAGE = "client_message"
    CLIENT_CREATED = "client_created"
    CLIENT_UPGRADED = "client_upgraded"

    # Billing
    BILLING_CLOCK_IN = "billing_clock_in"
    BILLING_CLOCK_OUT = "billing_clock_out"

    # System
    SYSTEM_START = "system_start"
    SYSTEM_STOP = "system_stop"
    CONFIG_CHANGE = "config_change"


class AuditLog:
    """
    Central audit log with v1 bridge support.
    All security-relevant operations are logged here.
    """

    def __init__(self, logs_dir: Optional[Path] = None):
        self.logs_dir = logs_dir or Path(__file__).parent
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # v1 audit log bridge — ensure directory + file exist
        self._v1_audit_path = Path.home() / ".able" / "logs" / "audit" / "audit.log"
        self._v1_audit_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._v1_audit_path.exists():
            self._v1_audit_path.touch()

    def log(
        self,
        action: AuditAction,
        details: Dict[str, Any] = None,
        client_id: Optional[str] = None,
        user_id: Optional[str] = None,
        success: bool = True,
        error: Optional[str] = None
    ):
        """Log an audit entry"""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": action.value,
            "client_id": client_id,
            "user_id": user_id,
            "success": success,
            "error": error,
            "details": details or {}
        }

        # Write to v2 log
        log_file = self.logs_dir / "audit.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Write to v1 log format
        if self._v1_audit_path.exists():
            v1_entry = f"[{entry['timestamp']}] ACTION={action.value}"
            if client_id:
                v1_entry += f" CLIENT={client_id}"
            v1_entry += f" SUCCESS={success}"
            if error:
                v1_entry += f" ERROR={error}"
            v1_entry += "\n"

            with open(self._v1_audit_path, "a") as f:
                f.write(v1_entry)

    def log_security(
        self,
        event_type: str,
        threat_level: str,
        details: Dict[str, Any],
        blocked: bool = False
    ):
        """Log a security event"""
        self.log(
            action=AuditAction.SECURITY_CHECK,
            details={
                "event_type": event_type,
                "threat_level": threat_level,
                "blocked": blocked,
                **details
            },
            success=not blocked
        )

    def log_command(
        self,
        command: str,
        verdict: str,
        reason: str,
        client_id: Optional[str] = None
    ):
        """Log a command execution attempt"""
        action = AuditAction.COMMAND_APPROVED if verdict == "allowed" else AuditAction.COMMAND_BLOCKED
        self.log(
            action=action,
            details={
                "command": command[:100],
                "verdict": verdict,
                "reason": reason
            },
            client_id=client_id,
            success=(verdict == "allowed")
        )

    def log_agent(
        self,
        agent_type: str,
        action: str,
        result: Dict[str, Any],
        client_id: Optional[str] = None
    ):
        """Log an agent operation"""
        action_map = {
            "scan": AuditAction.AGENT_SCAN,
            "audit": AuditAction.AGENT_AUDIT,
            "execute": AuditAction.AGENT_EXECUTE
        }

        self.log(
            action=action_map.get(action, AuditAction.AGENT_EXECUTE),
            details={
                "agent_type": agent_type,
                "agent_action": action,
                "result_summary": str(result)[:200]
            },
            client_id=client_id
        )

    def log_client_message(
        self,
        client_id: str,
        user_id: str,
        direction: str,
        message_length: int
    ):
        """Log a client message"""
        self.log(
            action=AuditAction.CLIENT_MESSAGE,
            details={
                "direction": direction,
                "message_length": message_length
            },
            client_id=client_id,
            user_id=user_id
        )

    def log_billing(
        self,
        action: str,
        client_id: str,
        details: Dict[str, Any]
    ):
        """Log a billing operation"""
        billing_action = AuditAction.BILLING_CLOCK_IN if action == "clock_in" else AuditAction.BILLING_CLOCK_OUT
        self.log(
            action=billing_action,
            details=details,
            client_id=client_id
        )

    def get_recent(
        self,
        limit: int = 100,
        action: Optional[AuditAction] = None,
        client_id: Optional[str] = None,
        since: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Get recent audit entries"""
        entries = []
        log_file = self.logs_dir / "audit.jsonl"

        if not log_file.exists():
            return entries

        with open(log_file) as f:
            for line in f:
                try:
                    entry = json.loads(line)

                    # Apply filters
                    if action and entry.get("action") != action.value:
                        continue
                    if client_id and entry.get("client_id") != client_id:
                        continue
                    if since:
                        entry_time = datetime.fromisoformat(entry["timestamp"])
                        if entry_time < since:
                            continue

                    entries.append(entry)
                except json.JSONDecodeError:
                    continue

        return entries[-limit:]

    def get_security_events(
        self,
        hours: int = 24,
        blocked_only: bool = False
    ) -> List[Dict[str, Any]]:
        """Get security events from recent history"""
        since = datetime.utcnow() - timedelta(hours=hours)
        entries = self.get_recent(limit=1000, since=since)

        security_events = [
            e for e in entries
            if e.get("action") in [
                AuditAction.SECURITY_CHECK.value,
                AuditAction.INJECTION_DETECTED.value,
                AuditAction.COMMAND_BLOCKED.value
            ]
        ]

        if blocked_only:
            security_events = [
                e for e in security_events
                if not e.get("success") or e.get("details", {}).get("blocked")
            ]

        return security_events

    def get_stats(self, hours: int = 24) -> Dict[str, Any]:
        """Get audit statistics"""
        since = datetime.utcnow() - timedelta(hours=hours)
        entries = self.get_recent(limit=10000, since=since)

        stats = {
            "total_entries": len(entries),
            "by_action": {},
            "by_client": {},
            "security_events": 0,
            "blocked_commands": 0,
            "successful_executions": 0
        }

        for entry in entries:
            action = entry.get("action", "unknown")
            client = entry.get("client_id", "system")

            stats["by_action"][action] = stats["by_action"].get(action, 0) + 1
            stats["by_client"][client] = stats["by_client"].get(client, 0) + 1

            if action in ["security_check", "injection_detected"]:
                stats["security_events"] += 1
            if action == "command_blocked":
                stats["blocked_commands"] += 1
            if action == "agent_execute" and entry.get("success"):
                stats["successful_executions"] += 1

        return stats

    async def log_event(self, action: str, details: Dict[str, Any] = None, **kwargs):
        """Async-compatible event logger — bridge for CronScheduler and other async callers."""
        self.log(
            action=AuditAction.AGENT_EXECUTE,
            details={"event_action": action, **(details or {}), **kwargs},
        )
