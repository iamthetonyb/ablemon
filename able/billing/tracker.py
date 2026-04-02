"""
Billing Tracker - Track token usage and costs.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import aiofiles

logger = logging.getLogger(__name__)


@dataclass
class UsageRecord:
    """A single usage record"""
    timestamp: datetime
    client_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    session_id: Optional[str] = None
    task_description: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "client_id": self.client_id,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost": self.cost,
            "session_id": self.session_id,
            "task_description": self.task_description,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'UsageRecord':
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            client_id=data["client_id"],
            provider=data["provider"],
            model=data.get("model", "unknown"),
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
            cost=data["cost"],
            session_id=data.get("session_id"),
            task_description=data.get("task_description"),
        )


@dataclass
class BillingSession:
    """An active billing session"""
    session_id: str
    client_id: str
    task_description: str
    clock_in: datetime
    clock_out: Optional[datetime] = None
    status: str = "active"
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    work_log: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "client_id": self.client_id,
            "task_description": self.task_description,
            "clock_in": self.clock_in.isoformat(),
            "clock_out": self.clock_out.isoformat() if self.clock_out else None,
            "status": self.status,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
            "work_log": self.work_log,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'BillingSession':
        return cls(
            session_id=data["session_id"],
            client_id=data["client_id"],
            task_description=data["task_description"],
            clock_in=datetime.fromisoformat(data["clock_in"]),
            clock_out=datetime.fromisoformat(data["clock_out"]) if data.get("clock_out") else None,
            status=data.get("status", "active"),
            total_input_tokens=data.get("total_input_tokens", 0),
            total_output_tokens=data.get("total_output_tokens", 0),
            total_cost=data.get("total_cost", 0.0),
            work_log=data.get("work_log", []),
        )


class BillingTracker:
    """
    Track usage and costs for billing.

    Features:
    - Session-based tracking (clock in/out)
    - Per-request usage logging
    - Provider-specific cost calculation
    - V1 bridge sync
    """

    # Default provider costs ($ per million tokens)
    # These are overridden when a ProviderRegistry is available.
    DEFAULT_COSTS = {
        "nvidia_nim": {"input": 0.0, "output": 0.0},
        "openrouter": {"input": 0.60, "output": 3.00},
        "anthropic": {"input": 3.00, "output": 15.00},
        "ollama": {"input": 0.0, "output": 0.0},
        # Registry-based provider names
        "gpt-5.4-mini": {"input": 0.0, "output": 0.0},
        "gpt-5.4": {"input": 0.0, "output": 0.0},
        "nemotron-120b-nim": {"input": 0.30, "output": 0.80},
        "nemotron-120b-openrouter": {"input": 0.0, "output": 0.0},
        "mimo-v2-pro": {"input": 1.00, "output": 3.00},
        "minimax-m2.7": {"input": 0.30, "output": 1.20},
        "claude-opus-4-6": {"input": 15.00, "output": 75.00},
        "ollama-local": {"input": 0.0, "output": 0.0},
        "ollama-local-small": {"input": 0.0, "output": 0.0},
    }

    # Client billing rates (can be higher than cost)
    DEFAULT_RATES = {
        "input_per_million": 6.25,
        "output_per_million": 31.25,
    }

    def __init__(
        self,
        data_path: Path,
        v1_bridge=None,
        provider_costs: Dict = None,
        client_rates: Dict = None
    ):
        self.data_path = Path(data_path)
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.v1_bridge = v1_bridge

        self.provider_costs = provider_costs or self.DEFAULT_COSTS
        self.client_rates = client_rates or self.DEFAULT_RATES

    @classmethod
    def from_registry(cls, data_path: Path, registry, **kwargs) -> "BillingTracker":
        """Create a BillingTracker with costs populated from a ProviderRegistry."""
        costs = registry.get_all_costs() if registry else cls.DEFAULT_COSTS
        return cls(data_path=data_path, provider_costs=costs, **kwargs)

        # Active sessions
        self.sessions: Dict[str, BillingSession] = {}

        # Usage log file
        self.usage_log = self.data_path / 'usage.jsonl'
        self.sessions_dir = self.data_path / 'sessions'
        self.sessions_dir.mkdir(exist_ok=True)

    async def clock_in(
        self,
        client_id: str,
        task_description: str
    ) -> BillingSession:
        """
        Start a billing session.

        Args:
            client_id: Client to bill
            task_description: What work is being done

        Returns:
            New BillingSession
        """
        session_id = f"{client_id}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

        session = BillingSession(
            session_id=session_id,
            client_id=client_id,
            task_description=task_description,
            clock_in=datetime.utcnow()
        )

        self.sessions[session_id] = session

        # Save session file
        await self._save_session(session)

        logger.info(f"[CLOCK_IN] Client: {client_id} | Task: {task_description} | Session: {session_id}")

        # Sync to V1 if bridge available
        if self.v1_bridge:
            await self.v1_bridge.sync_billing_session(session.to_dict())

        return session

    async def clock_out(self, session_id: str) -> BillingSession:
        """
        End a billing session.

        Args:
            session_id: Session to close

        Returns:
            Completed BillingSession
        """
        if session_id not in self.sessions:
            # Try to load from disk
            session = await self._load_session(session_id)
            if not session:
                raise ValueError(f"Session not found: {session_id}")
        else:
            session = self.sessions[session_id]

        session.clock_out = datetime.utcnow()
        session.status = "completed"

        # Save final session
        await self._save_session(session)

        # Remove from active
        self.sessions.pop(session_id, None)

        duration = (session.clock_out - session.clock_in).total_seconds() / 60

        logger.info(
            f"[CLOCK_OUT] Client: {session.client_id} | "
            f"Duration: {duration:.1f}m | "
            f"Tokens: {session.total_input_tokens}/{session.total_output_tokens} | "
            f"Cost: ${session.total_cost:.4f}"
        )

        # Sync to V1
        if self.v1_bridge:
            await self.v1_bridge.sync_billing_session(session.to_dict())

        return session

    async def track_usage(
        self,
        client_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        session_id: Optional[str] = None,
        task_description: Optional[str] = None
    ):
        """
        Track a single usage event.

        Args:
            client_id: Client to bill
            provider: LLM provider used
            model: Model name
            input_tokens: Input tokens used
            output_tokens: Output tokens generated
            session_id: Associated session (if any)
            task_description: What was done
        """
        # Calculate cost based on provider costs
        provider_cost = self.provider_costs.get(provider, {"input": 1.0, "output": 5.0})
        cost = (
            (input_tokens / 1_000_000) * provider_cost["input"] +
            (output_tokens / 1_000_000) * provider_cost["output"]
        )

        # Calculate client charge based on rates
        charge = (
            (input_tokens / 1_000_000) * self.client_rates["input_per_million"] +
            (output_tokens / 1_000_000) * self.client_rates["output_per_million"]
        )

        record = UsageRecord(
            timestamp=datetime.utcnow(),
            client_id=client_id,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=charge,  # Use client rate for billing
            session_id=session_id,
            task_description=task_description,
        )

        # Append to log
        async with aiofiles.open(self.usage_log, 'a') as f:
            await f.write(json.dumps(record.to_dict()) + '\n')

        # Update session if active
        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
            session.total_input_tokens += input_tokens
            session.total_output_tokens += output_tokens
            session.total_cost += charge
            session.work_log.append({
                "timestamp": datetime.utcnow().isoformat(),
                "action": task_description or "completion",
                "tokens_in": input_tokens,
                "tokens_out": output_tokens,
                "cost": charge,
            })
            await self._save_session(session)

        logger.debug(
            f"Tracked usage: {client_id} | "
            f"{provider}/{model} | "
            f"{input_tokens}/{output_tokens} tokens | "
            f"${charge:.6f}"
        )

    async def get_client_usage(
        self,
        client_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict:
        """Get usage summary for a client"""
        records = await self._get_records(client_id, start_date, end_date)

        total_input = sum(r.input_tokens for r in records)
        total_output = sum(r.output_tokens for r in records)
        total_cost = sum(r.cost for r in records)

        by_provider = {}
        for r in records:
            if r.provider not in by_provider:
                by_provider[r.provider] = {"input": 0, "output": 0, "cost": 0.0}
            by_provider[r.provider]["input"] += r.input_tokens
            by_provider[r.provider]["output"] += r.output_tokens
            by_provider[r.provider]["cost"] += r.cost

        return {
            "client_id": client_id,
            "period": {
                "start": start_date.isoformat() if start_date else None,
                "end": end_date.isoformat() if end_date else None,
            },
            "totals": {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cost": total_cost,
            },
            "by_provider": by_provider,
            "record_count": len(records),
        }

    async def _get_records(
        self,
        client_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[UsageRecord]:
        """Get filtered usage records"""
        records = []

        if not self.usage_log.exists():
            return records

        async with aiofiles.open(self.usage_log, 'r') as f:
            async for line in f:
                try:
                    data = json.loads(line.strip())
                    record = UsageRecord.from_dict(data)

                    if client_id and record.client_id != client_id:
                        continue
                    if start_date and record.timestamp < start_date:
                        continue
                    if end_date and record.timestamp > end_date:
                        continue

                    records.append(record)
                except (json.JSONDecodeError, KeyError):
                    continue

        return records

    async def _save_session(self, session: BillingSession):
        """Save session to disk"""
        path = self.sessions_dir / f"{session.session_id}.json"
        async with aiofiles.open(path, 'w') as f:
            await f.write(json.dumps(session.to_dict(), indent=2))

    async def _load_session(self, session_id: str) -> Optional[BillingSession]:
        """Load session from disk"""
        path = self.sessions_dir / f"{session_id}.json"
        if not path.exists():
            return None

        async with aiofiles.open(path, 'r') as f:
            data = json.loads(await f.read())
            return BillingSession.from_dict(data)

    def get_active_sessions(self) -> List[BillingSession]:
        """Get all active sessions"""
        return list(self.sessions.values())
