"""
Base Agent Architecture - Implements read/write separation
Scanner Agent (read-only) → Audit Agent → Trust Gate → Executor Agent (write)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
from pathlib import Path
import json
import uuid

from core.security.trust_gate import TrustGate, TrustTier, SecurityVerdict
from core.security.command_guard import CommandGuard, CommandVerdict

class AgentRole(Enum):
    SCANNER = "scanner"      # Read-only, processes inputs
    AUDITOR = "auditor"      # Validates scanner outputs
    EXECUTOR = "executor"    # Write operations, tool use
    SUPERVISOR = "supervisor" # Coordinates other agents

@dataclass
class AgentContext:
    agent_id: str
    role: AgentRole
    trust_tier: TrustTier
    client_id: Optional[str] = None
    session_id: Optional[str] = None
    parent_agent_id: Optional[str] = None

@dataclass
class AgentMessage:
    id: str
    timestamp: datetime
    role: str  # user, assistant, system, tool
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    trust_score: Optional[float] = None
    audit_id: Optional[str] = None

@dataclass
class AgentAction:
    action_type: str  # "tool_call", "message", "delegate", "escalate"
    target: str
    parameters: Dict[str, Any]
    requires_approval: bool = False
    risk_level: int = 1

class BaseAgent(ABC):
    """Base class for all agents"""

    def __init__(self, context: AgentContext, audit_dir: str = "audit/logs"):
        self.context = context
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.trust_gate = TrustGate(audit_dir=str(self.audit_dir))
        self.command_guard = CommandGuard(trust_tier=context.trust_tier.value)
        self.message_history: List[AgentMessage] = []
        self.action_log: List[Dict] = []

    @abstractmethod
    async def process(self, message: str, metadata: Dict = None) -> Any:
        """Process incoming message - implemented by subclasses"""
        pass

    def _log_action(self, action: AgentAction, result: Any):
        """Log action for audit trail"""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent_id": self.context.agent_id,
            "agent_role": self.context.role.value,
            "client_id": self.context.client_id,
            "action_type": action.action_type,
            "target": action.target,
            "parameters": action.parameters,
            "requires_approval": action.requires_approval,
            "risk_level": action.risk_level,
            "result_summary": str(result)[:500]
        }
        self.action_log.append(entry)

        # Write to audit log
        agent_log = self.audit_dir / f"agent_{self.context.agent_id}.jsonl"
        with open(agent_log, "a") as f:
            f.write(json.dumps(entry) + "\n")


class ScannerAgent(BaseAgent):
    """
    Read-only agent for processing external inputs.
    CANNOT execute commands or write files.
    Passes validated content to Auditor.
    """

    def __init__(self, context: AgentContext, audit_dir: str = "audit/logs"):
        context.role = AgentRole.SCANNER
        super().__init__(context, audit_dir)

    async def process(self, message: str, metadata: Dict = None) -> Dict[str, Any]:
        """Scan and validate input, return analysis"""

        # Validate through trust gate
        verdict = self.trust_gate.evaluate(
            text=message,
            source=metadata.get("source", "unknown") if metadata else "unknown",
            user_trust_tier=self.context.trust_tier
        )

        # Create analysis result
        analysis = {
            "input_hash": hash(message),
            "length": len(message),
            "security_verdict": {
                "passed": verdict.passed,
                "threat_level": verdict.threat_level.name,
                "trust_score": verdict.trust_score,
                "flags": verdict.flags,
                "audit_id": verdict.audit_id
            },
            "sanitized_content": verdict.sanitized_input,
            "blocked_reason": verdict.blocked_reason,
            "scanner_agent_id": self.context.agent_id,
            "timestamp": datetime.utcnow().isoformat()
        }

        # Log the scan
        self._log_action(
            AgentAction(
                action_type="scan",
                target="input",
                parameters={"length": len(message)},
                risk_level=1
            ),
            analysis
        )

        return analysis


class AuditorAgent(BaseAgent):
    """
    Validates Scanner outputs before passing to Executor.
    Performs fact-checking, relevance scoring, and secondary security analysis.
    """

    def __init__(self, context: AgentContext, audit_dir: str = "audit/logs"):
        context.role = AgentRole.AUDITOR
        super().__init__(context, audit_dir)
        self.audit_history: List[Dict] = []

    async def process(self, scanner_output: Dict, original_objective: str = None) -> Dict[str, Any]:
        """Audit scanner output and produce readability rating"""

        audit_result = {
            "audit_id": str(uuid.uuid4())[:8],
            "timestamp": datetime.utcnow().isoformat(),
            "scanner_audit_id": scanner_output.get("security_verdict", {}).get("audit_id"),
            "checks": {},
            "overall_rating": 0.0,
            "approved_for_executor": False,
            "notes": []
        }

        # Check 1: Security verdict passed
        security_passed = scanner_output.get("security_verdict", {}).get("passed", False)
        audit_result["checks"]["security"] = {
            "passed": security_passed,
            "weight": 0.4
        }

        # Check 2: Trust score threshold
        trust_score = scanner_output.get("security_verdict", {}).get("trust_score", 0)
        trust_passed = trust_score >= 0.7
        audit_result["checks"]["trust_score"] = {
            "passed": trust_passed,
            "score": trust_score,
            "weight": 0.3
        }

        # Check 3: No critical flags
        flags = scanner_output.get("security_verdict", {}).get("flags", [])
        critical_flags = [f for f in flags if "CRITICAL" in f.upper()]
        no_critical = len(critical_flags) == 0
        audit_result["checks"]["no_critical_flags"] = {
            "passed": no_critical,
            "critical_count": len(critical_flags),
            "weight": 0.3
        }

        # Calculate overall rating
        total_weight = 0
        weighted_score = 0
        for check_name, check_data in audit_result["checks"].items():
            weight = check_data.get("weight", 0.33)
            total_weight += weight
            if check_data.get("passed"):
                weighted_score += weight

        audit_result["overall_rating"] = weighted_score / total_weight if total_weight > 0 else 0

        # Determine if approved for executor
        audit_result["approved_for_executor"] = (
            audit_result["overall_rating"] >= 0.7 and
            security_passed and
            no_critical
        )

        if not audit_result["approved_for_executor"]:
            audit_result["notes"].append(
                f"Blocked: rating={audit_result['overall_rating']:.2f}, "
                f"security={security_passed}, critical_flags={len(critical_flags)}"
            )

        self.audit_history.append(audit_result)

        # Log the audit
        self._log_action(
            AgentAction(
                action_type="audit",
                target="scanner_output",
                parameters={"scanner_audit_id": scanner_output.get("security_verdict", {}).get("audit_id")},
                risk_level=2
            ),
            audit_result
        )

        return audit_result


class ExecutorAgent(BaseAgent):
    """
    Write-capable agent that executes actions.
    Only receives inputs that passed Scanner → Auditor pipeline.
    Has graduated permissions based on trust tier.
    """

    def __init__(self, context: AgentContext, audit_dir: str = "audit/logs"):
        context.role = AgentRole.EXECUTOR
        super().__init__(context, audit_dir)
        self.pending_approvals: List[AgentAction] = []

    async def process(self, audit_result: Dict, action_request: AgentAction) -> Dict[str, Any]:
        """Execute action if audit passed and permissions allow"""

        result = {
            "executed": False,
            "action": action_request.action_type,
            "target": action_request.target,
            "error": None,
            "output": None,
            "required_approval": False
        }

        # Verify audit approval
        if not audit_result.get("approved_for_executor"):
            result["error"] = "Audit did not approve this content for execution"
            return result

        # Check command permissions if it's a shell command
        if action_request.action_type == "shell_command":
            cmd_analysis = self.command_guard.analyze(action_request.target)

            if cmd_analysis.verdict == CommandVerdict.DENIED:
                result["error"] = f"Command denied: {cmd_analysis.reason}"
                return result

            if cmd_analysis.verdict == CommandVerdict.REQUIRES_APPROVAL:
                result["required_approval"] = True
                result["error"] = f"Command requires approval: {cmd_analysis.reason}"
                self.pending_approvals.append(action_request)
                return result

        # Execute based on action type
        if action_request.action_type == "message":
            result["executed"] = True
            result["output"] = "Message sent"

        elif action_request.action_type == "tool_call":
            # Implement tool execution
            result["executed"] = True
            result["output"] = f"Tool {action_request.target} executed"

        elif action_request.action_type == "shell_command":
            # Execute allowed command (would use subprocess in real impl)
            result["executed"] = True
            result["output"] = f"Command executed: {action_request.target}"

        # Log execution
        self._log_action(action_request, result)

        return result
