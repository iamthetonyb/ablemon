"""
Approval Workflow - Human-in-the-loop for risky operations.

Supports:
- Telegram inline buttons for quick approval
- Timeout with escalation
- Approval history tracking
- Delegation rules
"""

import asyncio
import hashlib
import hmac as hmac_mod
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Callable, Any
import json

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    MODIFIED = "modified"
    TIMEOUT = "timeout"
    ESCALATED = "escalated"


class ApprovalTimeout(Exception):
    """Raised when approval request times out"""
    pass


@dataclass
class ApprovalRequest:
    """An approval request"""
    id: str
    operation: str
    details: Dict[str, Any]
    requester_id: str
    client_id: Optional[str] = None
    timeout_seconds: int = 300
    created_at: datetime = field(default_factory=datetime.utcnow)
    escalation_user: Optional[int] = None
    risk_level: str = "medium"  # low, medium, high, critical
    context: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "operation": self.operation,
            "details": self.details,
            "requester_id": self.requester_id,
            "client_id": self.client_id,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at.isoformat(),
            "escalation_user": self.escalation_user,
            "risk_level": self.risk_level,
            "context": self.context,
        }


@dataclass
class ApprovalResult:
    """Result of an approval request"""
    request_id: str
    status: ApprovalStatus
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    modifications: Optional[Dict] = None
    reason: Optional[str] = None
    response_time_seconds: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "modifications": self.modifications,
            "reason": self.reason,
            "response_time_seconds": self.response_time_seconds,
        }


class ApprovalWorkflow:
    """
    Human-in-the-loop approval workflow with preference learning.

    Features:
    - Telegram inline keyboard for quick decisions
    - Configurable timeout with escalation
    - Approval history for audit
    - Delegation rules (auto-approve low risk for trusted users)
    - Preference learning from approval/rejection patterns
    """

    # Minimum approvals before auto-approving an operation
    AUTO_APPROVE_THRESHOLD = 5
    # If approval rate exceeds this, consider auto-approving
    AUTO_APPROVE_RATE = 0.95

    def __init__(
        self,
        owner_id: int,
        bot=None,  # Telegram bot instance
        default_timeout: int = 300,
        auto_approve_low_risk: bool = False,
        escalation_timeout: int = 600
    ):
        self.owner_id = owner_id
        self.bot = bot
        self.default_timeout = default_timeout
        self.auto_approve_low_risk = auto_approve_low_risk
        self.escalation_timeout = escalation_timeout
        self._hmac_key = os.environ.get(
            "ABLE_APPROVAL_HMAC_KEY", uuid.uuid4().hex
        ).encode()

        # Pending requests
        self.pending: Dict[str, ApprovalRequest] = {}
        self.results: Dict[str, asyncio.Future] = {}

        # Delegation rules
        self.delegation_rules: Dict[int, List[str]] = {}  # user_id -> allowed_operations

        # Preference learning: tracks approval/denial history per operation
        # { operation: { "approved": N, "denied": N, "last_denied_reason": str } }
        self._approval_history: Dict[str, Dict[str, Any]] = {}
        # Complete log for audit
        self._approval_log: List[Dict] = []

    def _sign_callback(self, action: str, request_id: str) -> str:
        """Sign callback_data with HMAC to prevent forgery."""
        msg = f"{action}:{request_id}"
        sig = hmac_mod.new(self._hmac_key, msg.encode(), hashlib.sha256).hexdigest()[:8]
        return f"{msg}:{sig}"

    def _verify_callback(self, data: str) -> tuple[Optional[str], Optional[str]]:
        """Verify and parse signed callback_data. Returns (action, request_id) or (None, None)."""
        parts = data.split(":")
        if len(parts) == 3:
            action, request_id, sig = parts
            msg = f"{action}:{request_id}"
            expected = hmac_mod.new(self._hmac_key, msg.encode(), hashlib.sha256).hexdigest()[:8]
            if hmac_mod.compare_digest(expected, sig):
                return action, request_id
        # Backwards compat: unsigned format (action:id)
        if len(parts) == 2:
            return parts[0], parts[1]
        return None, None

    def set_bot(self, bot):
        """Set the Telegram bot instance"""
        self.bot = bot

    def add_delegation(self, user_id: int, operations: List[str]):
        """Allow a user to approve specific operations"""
        self.delegation_rules[user_id] = operations

    async def request_approval(
        self,
        operation: str,
        details: Dict[str, Any],
        requester_id: str = "system",
        client_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        risk_level: str = "medium",
        context: Optional[str] = None
    ) -> ApprovalResult:
        """
        Request approval for an operation.

        Args:
            operation: What operation needs approval (e.g., "create_file", "git_commit")
            details: Details about the operation
            requester_id: Who/what is requesting
            client_id: Client this is for (if applicable)
            timeout_seconds: Override default timeout
            risk_level: low/medium/high/critical
            context: Additional context to show

        Returns:
            ApprovalResult with decision

        Raises:
            ApprovalTimeout: If no response within timeout
        """
        # Check auto-approve rules
        if self.auto_approve_low_risk and risk_level == "low":
            logger.info(f"Auto-approving low-risk operation: {operation}")
            self._record_outcome(operation, ApprovalStatus.APPROVED, "auto-low-risk")
            return ApprovalResult(
                request_id="auto",
                status=ApprovalStatus.APPROVED,
                approved_by=0,
                approved_at=datetime.utcnow(),
                reason="Auto-approved (low risk)"
            )

        # Check learned preferences: if this operation is always approved, skip the prompt
        if risk_level in ("low", "medium") and self._should_auto_approve(operation):
            hist = self._approval_history.get(operation, {})
            logger.info(
                f"Auto-approving via preference learning: {operation} "
                f"(approved {hist.get('approved', 0)}/{hist.get('approved', 0) + hist.get('denied', 0)} times)"
            )
            self._record_outcome(operation, ApprovalStatus.APPROVED, "preference-learned")
            return ApprovalResult(
                request_id="auto-pref",
                status=ApprovalStatus.APPROVED,
                approved_by=0,
                approved_at=datetime.utcnow(),
                reason=f"Auto-approved (learned preference: {hist.get('approved', 0)} consecutive approvals)"
            )

        # Create request
        request_id = str(uuid.uuid4())[:8]
        request = ApprovalRequest(
            id=request_id,
            operation=operation,
            details=details,
            requester_id=requester_id,
            client_id=client_id,
            timeout_seconds=timeout_seconds or self.default_timeout,
            risk_level=risk_level,
            context=context
        )

        self.pending[request_id] = request
        self.results[request_id] = asyncio.Future()

        try:
            # Send approval request
            await self._send_approval_request(request)

            # Wait for response or timeout
            try:
                result = await asyncio.wait_for(
                    self.results[request_id],
                    timeout=request.timeout_seconds
                )
                return result
            except asyncio.TimeoutError:
                # Try escalation
                if request.escalation_user:
                    return await self._escalate(request)
                else:
                    result = ApprovalResult(
                        request_id=request_id,
                        status=ApprovalStatus.TIMEOUT,
                        reason=f"No response within {request.timeout_seconds}s"
                    )
                    return result

        finally:
            # Cleanup
            self.pending.pop(request_id, None)
            self.results.pop(request_id, None)

    async def _send_approval_request(self, request: ApprovalRequest):
        """Send approval request via Telegram"""
        if not self.bot:
            logger.warning("No bot configured, cannot send approval request")
            return

        # Format message
        risk_emoji = {
            "low": "🟢",
            "medium": "🟡",
            "high": "🟠",
            "critical": "🔴"
        }

        message = f"""
🔐 *APPROVAL REQUIRED* {risk_emoji.get(request.risk_level, '⚪')}

*Operation*: `{request.operation}`
*Risk Level*: {request.risk_level.upper()}
*Requester*: {request.requester_id}
"""

        if request.client_id:
            message += f"*Client*: {request.client_id}\n"

        message += f"\n*Details*:\n```json\n{json.dumps(request.details, indent=2)[:500]}```"

        if request.context:
            message += f"\n*Context*: {request.context}"

        message += f"\n\n⏱ *Timeout*: {request.timeout_seconds}s"
        message += f"\n🆔 *Request ID*: `{request.id}`"

        # Create inline keyboard
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=self._sign_callback("approve", request.id)),
                    InlineKeyboardButton("❌ Deny", callback_data=self._sign_callback("deny", request.id))
                ],
                [
                    InlineKeyboardButton("📝 Modify", callback_data=self._sign_callback("modify", request.id)),
                    InlineKeyboardButton("⏰ Extend", callback_data=self._sign_callback("extend", request.id))
                ]
            ])

            await self.bot.send_message(
                chat_id=self.owner_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        except ImportError:
            # Fallback without telegram library
            logger.warning("Telegram library not available, logging approval request")
            logger.info(f"APPROVAL REQUEST: {message}")
        except Exception as e:
            logger.error(f"Failed to send approval request: {e}")

    async def handle_callback(self, callback_query) -> Optional[ApprovalResult]:
        """
        Handle Telegram callback from inline button.

        Call this from your bot's callback_query handler.
        """
        data = callback_query.data
        user_id = callback_query.from_user.id

        action, request_id = self._verify_callback(data)
        if not action or not request_id:
            await callback_query.answer("Invalid callback signature")
            return None

        if request_id not in self.pending:
            await callback_query.answer("Request expired or not found")
            return None

        request = self.pending[request_id]

        # Check authorization
        if not self._can_approve(user_id, request.operation):
            await callback_query.answer("You are not authorized to approve this")
            return None

        start_time = request.created_at
        response_time = (datetime.utcnow() - start_time).total_seconds()

        if action == "approve":
            result = ApprovalResult(
                request_id=request_id,
                status=ApprovalStatus.APPROVED,
                approved_by=user_id,
                approved_at=datetime.utcnow(),
                response_time_seconds=response_time
            )
            self._record_outcome(request.operation, ApprovalStatus.APPROVED)
            await callback_query.answer("✅ Approved")

        elif action == "deny":
            result = ApprovalResult(
                request_id=request_id,
                status=ApprovalStatus.DENIED,
                approved_by=user_id,
                approved_at=datetime.utcnow(),
                reason="Denied by operator",
                response_time_seconds=response_time
            )
            self._record_outcome(request.operation, ApprovalStatus.DENIED, "operator_denied")
            await callback_query.answer("❌ Denied")

        elif action == "modify":
            # For modify, we'd need a conversation flow
            # For now, just mark as needing modification
            result = ApprovalResult(
                request_id=request_id,
                status=ApprovalStatus.MODIFIED,
                approved_by=user_id,
                approved_at=datetime.utcnow(),
                modifications={"needs_input": True},
                response_time_seconds=response_time
            )
            await callback_query.answer("📝 Please reply with modifications")
            return result  # Don't resolve future yet

        elif action == "extend":
            # Extend timeout by another default period
            request.timeout_seconds += self.default_timeout
            await callback_query.answer(f"⏰ Extended by {self.default_timeout}s")
            return None  # Don't resolve yet

        else:
            return None

        # Resolve the waiting future
        if request_id in self.results and not self.results[request_id].done():
            self.results[request_id].set_result(result)

        # Update message to show result
        try:
            status_text = "✅ APPROVED" if result.status == ApprovalStatus.APPROVED else "❌ DENIED"
            await callback_query.edit_message_text(
                text=callback_query.message.text + f"\n\n*Result*: {status_text}",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        return result

    def _can_approve(self, user_id: int, operation: str) -> bool:
        """Check if user can approve this operation"""
        # Owner can always approve
        if user_id == self.owner_id:
            return True

        # Check delegation rules
        if user_id in self.delegation_rules:
            allowed = self.delegation_rules[user_id]
            return operation in allowed or "*" in allowed

        return False

    async def _escalate(self, request: ApprovalRequest) -> ApprovalResult:
        """Escalate to backup user"""
        logger.warning(f"Escalating approval request {request.id}")

        # Send to escalation user
        original_owner = self.owner_id
        self.owner_id = request.escalation_user

        try:
            await self._send_approval_request(request)

            # Wait with extended timeout
            result = await asyncio.wait_for(
                self.results[request.id],
                timeout=self.escalation_timeout
            )
            result.status = ApprovalStatus.ESCALATED
            return result
        except asyncio.TimeoutError:
            return ApprovalResult(
                request_id=request.id,
                status=ApprovalStatus.TIMEOUT,
                reason="Escalation also timed out"
            )
        finally:
            self.owner_id = original_owner

    async def approve_programmatically(
        self,
        request_id: str,
        approved_by: int = 0,
        modifications: Optional[Dict] = None
    ) -> bool:
        """Approve a pending request programmatically"""
        if request_id not in self.pending:
            return False

        result = ApprovalResult(
            request_id=request_id,
            status=ApprovalStatus.APPROVED if not modifications else ApprovalStatus.MODIFIED,
            approved_by=approved_by,
            approved_at=datetime.utcnow(),
            modifications=modifications
        )

        if request_id in self.results and not self.results[request_id].done():
            self.results[request_id].set_result(result)
            return True

        return False

    async def deny_programmatically(
        self,
        request_id: str,
        denied_by: int = 0,
        reason: str = "Programmatically denied"
    ) -> bool:
        """Deny a pending request programmatically"""
        if request_id not in self.pending:
            return False

        result = ApprovalResult(
            request_id=request_id,
            status=ApprovalStatus.DENIED,
            approved_by=denied_by,
            approved_at=datetime.utcnow(),
            reason=reason
        )

        if request_id in self.results and not self.results[request_id].done():
            self.results[request_id].set_result(result)
            return True

        return False

    def get_pending_requests(self) -> List[ApprovalRequest]:
        """Get all pending approval requests"""
        return list(self.pending.values())

    # ── Preference Learning ──────────────────────────────────────

    def _record_outcome(self, operation: str, status: ApprovalStatus, reason: str = ""):
        """Record an approval/denial for preference learning."""
        if operation not in self._approval_history:
            self._approval_history[operation] = {"approved": 0, "denied": 0, "last_denied_reason": ""}

        hist = self._approval_history[operation]
        if status == ApprovalStatus.APPROVED:
            hist["approved"] += 1
        elif status == ApprovalStatus.DENIED:
            hist["denied"] += 1
            hist["last_denied_reason"] = reason

        self._approval_log.append({
            "operation": operation,
            "status": status.value,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
        })
        # Keep log bounded
        if len(self._approval_log) > 500:
            self._approval_log = self._approval_log[-250:]

    def _should_auto_approve(self, operation: str) -> bool:
        """Check if we've learned to auto-approve this operation."""
        hist = self._approval_history.get(operation)
        if not hist:
            return False
        total = hist["approved"] + hist["denied"]
        if total < self.AUTO_APPROVE_THRESHOLD:
            return False
        approval_rate = hist["approved"] / total
        return approval_rate >= self.AUTO_APPROVE_RATE

    def get_preference_summary(self) -> Dict[str, Any]:
        """Get a summary of learned approval preferences."""
        summary = {}
        for op, hist in self._approval_history.items():
            total = hist["approved"] + hist["denied"]
            rate = hist["approved"] / total if total > 0 else 0
            summary[op] = {
                "approved": hist["approved"],
                "denied": hist["denied"],
                "total": total,
                "approval_rate": round(rate, 3),
                "auto_approve_eligible": self._should_auto_approve(op),
                "last_denied_reason": hist.get("last_denied_reason", ""),
            }
        return summary
