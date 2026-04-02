"""
Approval History - Audit trail for approval decisions.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
import aiofiles

from .workflow import ApprovalRequest, ApprovalResult, ApprovalStatus

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRecord:
    """Combined request and result for history"""
    request: ApprovalRequest
    result: ApprovalResult
    recorded_at: datetime

    def to_dict(self) -> Dict:
        return {
            "request": self.request.to_dict(),
            "result": self.result.to_dict(),
            "recorded_at": self.recorded_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'ApprovalRecord':
        request_data = data["request"]
        result_data = data["result"]

        request = ApprovalRequest(
            id=request_data["id"],
            operation=request_data["operation"],
            details=request_data["details"],
            requester_id=request_data["requester_id"],
            client_id=request_data.get("client_id"),
            timeout_seconds=request_data.get("timeout_seconds", 300),
            created_at=datetime.fromisoformat(request_data["created_at"]),
            escalation_user=request_data.get("escalation_user"),
            risk_level=request_data.get("risk_level", "medium"),
            context=request_data.get("context")
        )

        result = ApprovalResult(
            request_id=result_data["request_id"],
            status=ApprovalStatus(result_data["status"]),
            approved_by=result_data.get("approved_by"),
            approved_at=datetime.fromisoformat(result_data["approved_at"]) if result_data.get("approved_at") else None,
            modifications=result_data.get("modifications"),
            reason=result_data.get("reason"),
            response_time_seconds=result_data.get("response_time_seconds", 0)
        )

        return cls(
            request=request,
            result=result,
            recorded_at=datetime.fromisoformat(data["recorded_at"])
        )


class ApprovalHistory:
    """
    Persistent storage for approval history.

    Uses JSONL format for append-friendly storage.
    """

    def __init__(self, history_path: Path):
        self.history_path = Path(history_path)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

    async def record(self, request: ApprovalRequest, result: ApprovalResult):
        """Record an approval decision"""
        record = ApprovalRecord(
            request=request,
            result=result,
            recorded_at=datetime.utcnow()
        )

        async with aiofiles.open(self.history_path, 'a') as f:
            await f.write(json.dumps(record.to_dict()) + '\n')

        logger.info(
            f"Recorded approval: {request.operation} -> {result.status.value} "
            f"by {result.approved_by}"
        )

    async def get_history(
        self,
        since: Optional[datetime] = None,
        operation: Optional[str] = None,
        status: Optional[ApprovalStatus] = None,
        client_id: Optional[str] = None,
        limit: int = 100
    ) -> List[ApprovalRecord]:
        """
        Query approval history.

        Args:
            since: Only records after this time
            operation: Filter by operation type
            status: Filter by result status
            client_id: Filter by client
            limit: Maximum records to return
        """
        records = []

        if not self.history_path.exists():
            return records

        async with aiofiles.open(self.history_path, 'r') as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    record = ApprovalRecord.from_dict(data)

                    # Apply filters
                    if since and record.recorded_at < since:
                        continue
                    if operation and record.request.operation != operation:
                        continue
                    if status and record.result.status != status:
                        continue
                    if client_id and record.request.client_id != client_id:
                        continue

                    records.append(record)

                    if len(records) >= limit:
                        break

                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Invalid history record: {e}")
                    continue

        # Return most recent first
        records.reverse()
        return records

    async def get_statistics(
        self,
        since: Optional[datetime] = None
    ) -> Dict:
        """Get approval statistics"""
        records = await self.get_history(since=since, limit=10000)

        stats = {
            "total": len(records),
            "by_status": {},
            "by_operation": {},
            "by_risk_level": {},
            "avg_response_time_seconds": 0,
            "approval_rate": 0,
        }

        if not records:
            return stats

        total_response_time = 0
        approved_count = 0

        for record in records:
            # By status
            status = record.result.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # By operation
            op = record.request.operation
            stats["by_operation"][op] = stats["by_operation"].get(op, 0) + 1

            # By risk level
            risk = record.request.risk_level
            stats["by_risk_level"][risk] = stats["by_risk_level"].get(risk, 0) + 1

            # Response time
            total_response_time += record.result.response_time_seconds

            # Approval rate
            if record.result.status == ApprovalStatus.APPROVED:
                approved_count += 1

        stats["avg_response_time_seconds"] = total_response_time / len(records)
        stats["approval_rate"] = approved_count / len(records)

        return stats

    async def get_user_decisions(
        self,
        user_id: int,
        since: Optional[datetime] = None,
        limit: int = 50
    ) -> List[ApprovalRecord]:
        """Get decisions made by a specific user"""
        all_records = await self.get_history(since=since, limit=10000)

        user_records = [
            r for r in all_records
            if r.result.approved_by == user_id
        ]

        return user_records[:limit]

    async def cleanup_old_records(self, days: int = 90):
        """Remove records older than specified days"""
        if not self.history_path.exists():
            return 0

        cutoff = datetime.utcnow() - timedelta(days=days)
        kept_records = []
        removed_count = 0

        async with aiofiles.open(self.history_path, 'r') as f:
            async for line in f:
                try:
                    data = json.loads(line.strip())
                    recorded_at = datetime.fromisoformat(data["recorded_at"])
                    if recorded_at >= cutoff:
                        kept_records.append(line)
                    else:
                        removed_count += 1
                except (json.JSONDecodeError, KeyError):
                    # Keep malformed records for manual review
                    kept_records.append(line)

        # Rewrite file with kept records
        async with aiofiles.open(self.history_path, 'w') as f:
            for line in kept_records:
                await f.write(line)

        logger.info(f"Cleaned up {removed_count} old approval records")
        return removed_count
