"""
Billing Reports - Generate usage and billing reports.
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from .tracker import BillingTracker, UsageRecord

logger = logging.getLogger(__name__)


class BillingReports:
    """
    Generate various billing reports.

    Reports:
    - Daily usage summary
    - Weekly usage summary
    - Client breakdown
    - Provider cost analysis
    - Trend analysis
    """

    def __init__(self, tracker: BillingTracker):
        self.tracker = tracker

    async def daily_summary(self, target_date: date = None) -> Dict:
        """Get usage summary for a specific day"""
        if target_date is None:
            target_date = date.today()

        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt = datetime.combine(target_date, datetime.max.time())

        records = await self.tracker._get_records(None, start_dt, end_dt)

        return self._summarize_records(records, f"Daily Summary: {target_date}")

    async def weekly_summary(self, end_date: date = None) -> Dict:
        """Get usage summary for the past week"""
        if end_date is None:
            end_date = date.today()

        start_date = end_date - timedelta(days=7)

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        records = await self.tracker._get_records(None, start_dt, end_dt)

        summary = self._summarize_records(records, f"Weekly Summary: {start_date} to {end_date}")

        # Add daily breakdown
        daily = defaultdict(lambda: {"input": 0, "output": 0, "cost": 0.0})
        for r in records:
            day = r.timestamp.date().isoformat()
            daily[day]["input"] += r.input_tokens
            daily[day]["output"] += r.output_tokens
            daily[day]["cost"] += r.cost

        summary["daily_breakdown"] = dict(daily)

        return summary

    async def client_breakdown(
        self,
        start_date: date = None,
        end_date: date = None
    ) -> Dict:
        """Get usage broken down by client"""
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=30)

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        records = await self.tracker._get_records(None, start_dt, end_dt)

        by_client = defaultdict(lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": 0.0,
            "request_count": 0,
        })

        for r in records:
            by_client[r.client_id]["input_tokens"] += r.input_tokens
            by_client[r.client_id]["output_tokens"] += r.output_tokens
            by_client[r.client_id]["cost"] += r.cost
            by_client[r.client_id]["request_count"] += 1

        # Sort by cost
        sorted_clients = sorted(
            by_client.items(),
            key=lambda x: x[1]["cost"],
            reverse=True
        )

        total_cost = sum(c["cost"] for c in by_client.values())

        return {
            "report": "Client Breakdown",
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "clients": [
                {
                    "client_id": client_id,
                    "percentage": (data["cost"] / total_cost * 100) if total_cost > 0 else 0,
                    **data
                }
                for client_id, data in sorted_clients
            ],
            "total_cost": total_cost,
        }

    async def provider_analysis(
        self,
        start_date: date = None,
        end_date: date = None
    ) -> Dict:
        """Analyze costs by provider"""
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=30)

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        records = await self.tracker._get_records(None, start_dt, end_dt)

        by_provider = defaultdict(lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": 0.0,
            "request_count": 0,
            "models": defaultdict(int),
        })

        for r in records:
            by_provider[r.provider]["input_tokens"] += r.input_tokens
            by_provider[r.provider]["output_tokens"] += r.output_tokens
            by_provider[r.provider]["cost"] += r.cost
            by_provider[r.provider]["request_count"] += 1
            by_provider[r.provider]["models"][r.model] += 1

        # Calculate efficiency (cost per 1K tokens)
        for provider, data in by_provider.items():
            total_tokens = data["input_tokens"] + data["output_tokens"]
            if total_tokens > 0:
                data["cost_per_1k_tokens"] = (data["cost"] / total_tokens) * 1000
            else:
                data["cost_per_1k_tokens"] = 0

            # Convert defaultdict to dict
            data["models"] = dict(data["models"])

        return {
            "report": "Provider Analysis",
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "providers": dict(by_provider),
            "recommendation": self._provider_recommendation(dict(by_provider)),
        }

    async def trend_analysis(self, days: int = 30) -> Dict:
        """Analyze usage trends over time"""
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        records = await self.tracker._get_records(None, start_dt, end_dt)

        # Daily aggregation
        daily = defaultdict(lambda: {"input": 0, "output": 0, "cost": 0.0, "requests": 0})
        for r in records:
            day = r.timestamp.date().isoformat()
            daily[day]["input"] += r.input_tokens
            daily[day]["output"] += r.output_tokens
            daily[day]["cost"] += r.cost
            daily[day]["requests"] += 1

        # Calculate trends
        days_list = sorted(daily.keys())
        if len(days_list) >= 7:
            recent = days_list[-7:]
            earlier = days_list[-14:-7] if len(days_list) >= 14 else days_list[:7]

            recent_cost = sum(daily[d]["cost"] for d in recent)
            earlier_cost = sum(daily[d]["cost"] for d in earlier)

            if earlier_cost > 0:
                cost_change = ((recent_cost - earlier_cost) / earlier_cost) * 100
            else:
                cost_change = 0

            trend = "increasing" if cost_change > 10 else "decreasing" if cost_change < -10 else "stable"
        else:
            cost_change = 0
            trend = "insufficient_data"

        return {
            "report": "Trend Analysis",
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "days": days,
            },
            "daily_data": dict(daily),
            "trend": {
                "direction": trend,
                "week_over_week_change_percent": cost_change,
            },
            "averages": {
                "daily_cost": sum(d["cost"] for d in daily.values()) / len(daily) if daily else 0,
                "daily_requests": sum(d["requests"] for d in daily.values()) / len(daily) if daily else 0,
            }
        }

    def _summarize_records(self, records: List[UsageRecord], title: str) -> Dict:
        """Create summary from records"""
        total_input = sum(r.input_tokens for r in records)
        total_output = sum(r.output_tokens for r in records)
        total_cost = sum(r.cost for r in records)

        by_provider = defaultdict(lambda: {"input": 0, "output": 0, "cost": 0.0})
        by_client = defaultdict(lambda: {"input": 0, "output": 0, "cost": 0.0})

        for r in records:
            by_provider[r.provider]["input"] += r.input_tokens
            by_provider[r.provider]["output"] += r.output_tokens
            by_provider[r.provider]["cost"] += r.cost

            by_client[r.client_id]["input"] += r.input_tokens
            by_client[r.client_id]["output"] += r.output_tokens
            by_client[r.client_id]["cost"] += r.cost

        return {
            "report": title,
            "generated_at": datetime.utcnow().isoformat(),
            "totals": {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_input + total_output,
                "cost": total_cost,
                "request_count": len(records),
            },
            "by_provider": dict(by_provider),
            "by_client": dict(by_client),
        }

    def _provider_recommendation(self, providers: Dict) -> str:
        """Generate cost optimization recommendation"""
        if not providers:
            return "No data available for recommendations."

        # Find most expensive provider
        most_expensive = max(
            providers.items(),
            key=lambda x: x[1]["cost"],
            default=(None, {"cost": 0})
        )

        if most_expensive[0] and most_expensive[1]["cost"] > 0:
            cost_per_1k = most_expensive[1].get("cost_per_1k_tokens", 0)

            if most_expensive[0] == "anthropic" and cost_per_1k > 0.01:
                return (
                    f"Consider using NVIDIA NIM (free) or OpenRouter for routine tasks. "
                    f"Currently spending ${most_expensive[1]['cost']:.2f} on Anthropic."
                )
            elif "ollama" not in providers and most_expensive[1]["cost"] > 1:
                return (
                    "Consider setting up local Ollama for development and testing "
                    "to reduce API costs."
                )

        return "Current provider usage looks optimal."
