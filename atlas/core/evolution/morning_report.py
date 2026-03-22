"""
Morning Report — Telegram-deliverable summary of overnight system activity.

Generates a structured report covering:
- Tier distribution and routing performance
- Failures and escalations
- Cost summary by tier
- Distillation corpus stats
- GPU budget status
- Pending self-scheduler actions
- Recommended operator actions

Designed to be sent via Telegram at 7am daily.
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class MorningReportData:
    """Structured data for the morning report."""

    generated_at: str = ""
    period_hours: int = 24

    # Routing
    total_requests: int = 0
    tier_distribution: Dict[int, int] = field(default_factory=dict)
    failure_count: int = 0
    failure_rate_pct: float = 0.0
    escalation_count: int = 0
    override_rate_pct: float = 0.0

    # Cost
    cost_by_tier: Dict[str, float] = field(default_factory=dict)
    total_cost_usd: float = 0.0

    # Evolution
    evolution_cycles_run: int = 0
    improvements_deployed: int = 0
    new_evals_created: int = 0
    new_crons_proposed: int = 0
    skills_proposed: int = 0

    # Distillation
    corpus_pairs_total: int = 0
    corpus_pairs_last_24h: int = 0
    corpus_ready_for_training: bool = False
    training_threshold: int = 100

    # Budget
    opus_daily_spend_usd: float = 0.0
    opus_daily_budget_usd: float = 15.0
    opus_monthly_spend_usd: float = 0.0
    opus_monthly_budget_usd: float = 100.0
    evolution_daily_spend_usd: float = 0.0

    # Pending actions
    pending_actions: List[Dict[str, Any]] = field(default_factory=list)

    # Recommendations
    recommendations: List[str] = field(default_factory=list)

    # Errors
    errors: List[str] = field(default_factory=list)


class MorningReporter:
    """
    Generates a daily morning report for Telegram delivery.

    Reads from:
    - interaction_log.db (routing metrics)
    - cron_executions.db (job history)
    - evolution_cycles/ (evolution results)
    - data/self_scheduled/ (pending actions)
    - data/distillation_*.jsonl (corpus stats)
    - config/scorer_weights.yaml (current version)
    - config/routing_config.yaml (budget caps)
    """

    def __init__(
        self,
        interaction_db: str = "data/interaction_log.db",
        cron_db: str = "data/cron_executions.db",
        evolution_dir: str = "data/evolution_cycles",
        actions_dir: str = "data/self_scheduled",
        routing_config: str = "config/routing_config.yaml",
        weights_config: str = "config/scorer_weights.yaml",
    ):
        self._interaction_db = Path(interaction_db)
        self._cron_db = Path(cron_db)
        self._evolution_dir = Path(evolution_dir)
        self._actions_dir = Path(actions_dir)
        self._routing_config = Path(routing_config)
        self._weights_config = Path(weights_config)

    async def generate(self, period_hours: int = 24) -> MorningReportData:
        """
        Generate the morning report.

        Args:
            period_hours: Lookback window (default 24h)

        Returns:
            Structured report data
        """
        report = MorningReportData(
            generated_at=datetime.now(timezone.utc).isoformat(),
            period_hours=period_hours,
        )

        cutoff = time.time() - (period_hours * 3600)

        # Collect all interaction DB metrics in a single connection
        try:
            self._collect_interaction_metrics(report, cutoff)
        except Exception as e:
            report.errors.append(f"Interaction metrics: {e}")

        try:
            self._collect_evolution_data(report, cutoff)
        except Exception as e:
            report.errors.append(f"Evolution data: {e}")

        try:
            self._collect_corpus_stats(report)
        except Exception as e:
            report.errors.append(f"Corpus stats: {e}")

        try:
            self._collect_budget_caps(report)
        except Exception as e:
            report.errors.append(f"Budget caps: {e}")

        try:
            self._collect_pending_actions(report)
        except Exception as e:
            report.errors.append(f"Pending actions: {e}")

        self._generate_recommendations(report)

        return report

    def format_telegram(self, report: MorningReportData) -> str:
        """
        Format the report for Telegram delivery.

        Uses Telegram-compatible markdown (MarkdownV2-safe).
        Stays within Telegram's 4096 character message limit.
        """
        lines = []
        lines.append("ATLAS MORNING REPORT")
        lines.append(f"Period: {report.period_hours}h | {report.generated_at[:10]}")
        lines.append("")

        # Routing
        lines.append("-- ROUTING --")
        lines.append(f"Requests: {report.total_requests}")
        if report.tier_distribution:
            dist_parts = []
            for tier in sorted(report.tier_distribution.keys()):
                count = report.tier_distribution[tier]
                dist_parts.append(f"T{tier}:{count}")
            lines.append(f"Distribution: {' | '.join(dist_parts)}")
        lines.append(f"Failures: {report.failure_count} ({report.failure_rate_pct:.1f}%)")
        lines.append(f"Escalations: {report.escalation_count} (override: {report.override_rate_pct:.1f}%)")
        lines.append("")

        # Cost
        lines.append("-- COST (24h) --")
        if report.cost_by_tier:
            for tier_name, cost in report.cost_by_tier.items():
                if cost > 0:
                    lines.append(f"  {tier_name}: ${cost:.2f}")
        lines.append(f"Total: ${report.total_cost_usd:.2f}")
        lines.append("")

        # Evolution
        lines.append("-- EVOLUTION --")
        lines.append(f"Cycles: {report.evolution_cycles_run}")
        lines.append(f"Deployed: {report.improvements_deployed} improvements")
        if report.new_evals_created:
            lines.append(f"New evals: {report.new_evals_created}")
        if report.new_crons_proposed:
            lines.append(f"Crons proposed: {report.new_crons_proposed}")
        if report.skills_proposed:
            lines.append(f"Skills proposed: {report.skills_proposed}")
        lines.append("")

        # Corpus
        lines.append("-- DISTILLATION --")
        lines.append(f"Corpus: {report.corpus_pairs_total} pairs ({report.corpus_pairs_last_24h} new)")
        progress_pct = min(100.0, report.corpus_pairs_total / max(report.training_threshold, 1) * 100)
        lines.append(f"Training ready: {'YES' if report.corpus_ready_for_training else f'NO ({progress_pct:.0f}%)'}")
        lines.append("")

        # Budget
        lines.append("-- BUDGET --")
        lines.append(
            f"Opus daily: ${report.opus_daily_spend_usd:.2f} / ${report.opus_daily_budget_usd:.2f} "
            f"({report.opus_daily_spend_usd / max(report.opus_daily_budget_usd, 0.01) * 100:.0f}%)"
        )
        lines.append(
            f"Opus monthly: ${report.opus_monthly_spend_usd:.2f} / ${report.opus_monthly_budget_usd:.2f}"
        )
        lines.append(f"Evolution daily: ${report.evolution_daily_spend_usd:.2f}")
        lines.append("")

        # Pending actions
        if report.pending_actions:
            lines.append(f"-- PENDING REVIEW ({len(report.pending_actions)}) --")
            for action in report.pending_actions[:5]:
                lines.append(f"  [{action.get('action_type', '?')}] {action.get('name', '?')}")
            if len(report.pending_actions) > 5:
                lines.append(f"  ... +{len(report.pending_actions) - 5} more")
            lines.append("")

        # Recommendations
        if report.recommendations:
            lines.append("-- RECOMMENDATIONS --")
            for i, rec in enumerate(report.recommendations, 1):
                lines.append(f"  {i}. {rec}")
            lines.append("")

        # Errors
        if report.errors:
            lines.append(f"-- ERRORS ({len(report.errors)}) --")
            for err in report.errors[:3]:
                lines.append(f"  ! {err}")

        text = "\n".join(lines)

        # Telegram message limit
        if len(text) > 4000:
            text = text[:3950] + "\n\n... (truncated)"

        return text

    def _collect_interaction_metrics(self, report: MorningReportData, cutoff: float) -> None:
        """Collect routing, cost, and budget metrics from interaction log in a single connection."""
        if not self._interaction_db.exists():
            return

        conn = sqlite3.connect(self._interaction_db)
        try:
            # Total requests
            row = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE timestamp > ?",
                (cutoff,),
            ).fetchone()
            report.total_requests = row[0] if row else 0

            # Tier distribution
            rows = conn.execute(
                "SELECT selected_tier, COUNT(*) FROM interactions WHERE timestamp > ? GROUP BY selected_tier",
                (cutoff,),
            ).fetchall()
            for tier, count in rows:
                report.tier_distribution[int(tier)] = count

            # Failures
            row = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE timestamp > ? AND success = 0",
                (cutoff,),
            ).fetchone()
            report.failure_count = row[0] if row else 0
            if report.total_requests > 0:
                report.failure_rate_pct = round(report.failure_count / report.total_requests * 100, 1)

            # Escalations (overrides)
            row = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE timestamp > ? AND override_tier IS NOT NULL",
                (cutoff,),
            ).fetchone()
            report.escalation_count = row[0] if row else 0
            if report.total_requests > 0:
                report.override_rate_pct = round(report.escalation_count / report.total_requests * 100, 1)

            # Cost by provider
            rows = conn.execute(
                """SELECT selected_provider, SUM(cost_usd)
                   FROM interactions WHERE timestamp > ?
                   GROUP BY selected_provider""",
                (cutoff,),
            ).fetchall()
            total = 0.0
            for provider, cost in rows:
                if cost:
                    report.cost_by_tier[provider or "unknown"] = round(cost, 4)
                    total += cost
            report.total_cost_usd = round(total, 4)

            # Budget spend (Opus daily/monthly, evolution daily)
            day_cutoff = time.time() - 86400
            row = conn.execute(
                """SELECT COALESCE(SUM(cost_usd), 0)
                   FROM interactions
                   WHERE timestamp > ? AND selected_provider LIKE '%opus%'""",
                (day_cutoff,),
            ).fetchone()
            report.opus_daily_spend_usd = round(row[0], 4) if row else 0.0

            month_cutoff = time.time() - (30 * 86400)
            row = conn.execute(
                """SELECT COALESCE(SUM(cost_usd), 0)
                   FROM interactions
                   WHERE timestamp > ? AND selected_provider LIKE '%opus%'""",
                (month_cutoff,),
            ).fetchone()
            report.opus_monthly_spend_usd = round(row[0], 4) if row else 0.0

            row = conn.execute(
                """SELECT COALESCE(SUM(cost_usd), 0)
                   FROM interactions
                   WHERE timestamp > ? AND selected_provider LIKE '%minimax%'""",
                (day_cutoff,),
            ).fetchone()
            report.evolution_daily_spend_usd = round(row[0], 4) if row else 0.0

        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    def _collect_evolution_data(self, report: MorningReportData, cutoff: float) -> None:
        """Collect evolution cycle data from disk."""
        if not self._evolution_dir.exists():
            return

        for path in self._evolution_dir.glob("*.json"):
            try:
                with open(path) as f:
                    data = json.load(f)
                started = data.get("started_at", "")
                if not started:
                    continue
                # Parse ISO timestamp to epoch
                try:
                    dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    if dt.timestamp() > cutoff:
                        report.evolution_cycles_run += 1
                        report.improvements_deployed += data.get("improvements_deployed", 0)
                except (ValueError, TypeError):
                    continue
            except (json.JSONDecodeError, OSError):
                continue

        # Self-scheduler data
        if self._actions_dir.exists():
            for path in self._actions_dir.glob("report_*.json"):
                try:
                    with open(path) as f:
                        data = json.load(f)
                    ts = data.get("timestamp", "")
                    if not ts:
                        continue
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if dt.timestamp() > cutoff:
                            report.new_evals_created += data.get("evals_created", 0)
                            report.new_crons_proposed += data.get("crons_created", 0)
                            report.skills_proposed += data.get("skills_proposed", 0)
                    except (ValueError, TypeError):
                        continue
                except (json.JSONDecodeError, OSError):
                    continue

    def _collect_corpus_stats(self, report: MorningReportData) -> None:
        """Count distillation training pairs."""
        data_dir = _PROJECT_ROOT / "data"
        if not data_dir.exists():
            return

        total = 0
        recent = 0
        cutoff_24h = time.time() - 86400

        for path in data_dir.glob("distillation_*.jsonl"):
            try:
                stat = path.stat()
                is_recent = stat.st_mtime > cutoff_24h
                with open(path) as f:
                    for raw_line in f:
                        if raw_line.strip():
                            total += 1
                            if is_recent:
                                recent += 1
            except OSError:
                continue

        report.corpus_pairs_total = total
        report.corpus_pairs_last_24h = recent
        report.corpus_ready_for_training = total >= report.training_threshold

    def _collect_budget_caps(self, report: MorningReportData) -> None:
        """Read budget cap configuration from routing config YAML."""
        if not self._routing_config.exists():
            return
        try:
            with open(self._routing_config) as f:
                config = yaml.safe_load(f) or {}
            budget = config.get("budget", {})
            report.opus_daily_budget_usd = budget.get("opus_daily_usd", 15.0)
            report.opus_monthly_budget_usd = budget.get("opus_monthly_usd", 100.0)
        except Exception:
            pass

    def _collect_pending_actions(self, report: MorningReportData) -> None:
        """Collect pending self-scheduler actions."""
        if not self._actions_dir.exists():
            return

        for path in self._actions_dir.glob("*.json"):
            if path.name.startswith("report_"):
                continue
            try:
                with open(path) as f:
                    data = json.load(f)
                if not data.get("promoted") and not data.get("rejected"):
                    report.pending_actions.append(data)
            except (json.JSONDecodeError, OSError):
                continue

        report.pending_actions.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    def _generate_recommendations(self, report: MorningReportData) -> None:
        """Generate actionable recommendations based on report data."""
        # High failure rate
        if report.failure_rate_pct > 10:
            report.recommendations.append(
                f"Failure rate {report.failure_rate_pct:.1f}% exceeds 10% threshold. "
                f"Check provider health and eval results."
            )

        # High override rate
        if report.override_rate_pct > 15:
            report.recommendations.append(
                f"Override rate {report.override_rate_pct:.1f}% suggests scorer under-routing. "
                f"Review scorer weights."
            )

        # Budget alerts
        if report.opus_daily_spend_usd > report.opus_daily_budget_usd * 0.8:
            report.recommendations.append(
                f"Opus daily spend at {report.opus_daily_spend_usd / max(report.opus_daily_budget_usd, 0.01) * 100:.0f}% of budget. "
                f"Consider tightening T4 threshold."
            )

        if report.opus_monthly_spend_usd > report.opus_monthly_budget_usd * 0.8:
            report.recommendations.append(
                f"Opus monthly spend at {report.opus_monthly_spend_usd / max(report.opus_monthly_budget_usd, 0.01) * 100:.0f}% of budget. "
                f"Review T4 routing patterns."
            )

        # Corpus readiness
        if report.corpus_ready_for_training:
            report.recommendations.append(
                f"Distillation corpus has {report.corpus_pairs_total} pairs (>={report.training_threshold}). "
                f"Schedule H100 fine-tuning run."
            )
        elif report.corpus_pairs_total > report.training_threshold * 0.7:
            remaining = report.training_threshold - report.corpus_pairs_total
            report.recommendations.append(
                f"Corpus at {report.corpus_pairs_total}/{report.training_threshold} pairs. "
                f"{remaining} more needed for training."
            )

        # Pending actions need review
        if report.pending_actions:
            report.recommendations.append(
                f"{len(report.pending_actions)} self-scheduled action(s) pending review. "
                f"Promote or reject in morning review."
            )

        # No evolution cycles = daemon not running
        if report.evolution_cycles_run == 0 and report.total_requests > 20:
            report.recommendations.append(
                "No evolution cycles in the last 24h. "
                "Check if the daemon is running."
            )
