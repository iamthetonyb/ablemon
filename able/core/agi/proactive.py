"""
ABLE Proactive Engine - Autonomous background intelligence.

OpenClaw-inspired: "The agent does useful work without being asked."

The proactive engine runs continuously, watching for opportunities to:
    - Surface relevant information before the user asks
    - Detect anomalies and potential issues
    - Suggest actions based on patterns
    - Keep memory up to date
    - Monitor system health

It's the difference between a reactive tool and an AGI assistant.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Awaitable

logger = logging.getLogger(__name__)


class ProactiveActionType(Enum):
    ALERT = "alert"              # Something needs immediate attention
    SUGGESTION = "suggestion"    # Might be useful, low urgency
    OBSERVATION = "observation"  # Informational, no action needed
    DIGEST = "digest"            # Regular summary/briefing


@dataclass
class ProactiveAction:
    """An action the proactive engine wants to take"""
    action_type: ProactiveActionType
    title: str
    description: str
    urgency: int = 5             # 1-10
    client_id: str = "owner"
    requires_human: bool = False  # Needs human to act
    auto_execute: bool = False    # Engine can act autonomously
    data: Dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class ProactiveCheck:
    """Base class for all proactive checks"""

    name: str = "base_check"
    interval_seconds: int = 300  # Default: check every 5 minutes

    def __init__(self, **kwargs):
        self.last_run: Optional[float] = None
        self.kwargs = kwargs

    def is_due(self) -> bool:
        if self.last_run is None:
            return True
        return (time.time() - self.last_run) >= self.interval_seconds

    async def run(self) -> List[ProactiveAction]:
        """Execute the check and return actions"""
        raise NotImplementedError

    def mark_run(self):
        self.last_run = time.time()


class DailyBriefingCheck(ProactiveCheck):
    """
    Generate a morning briefing at the start of each work day.

    Includes:
    - Yesterday's accomplishments
    - Today's objectives
    - Blocked items
    - System health summary
    """

    name = "daily_briefing"
    interval_seconds = 1800  # Check every 30 min (triggers once per day)

    def __init__(self, memory=None, work_hours=(10, 19), timezone="America/Los_Angeles"):
        super().__init__()
        self.memory = memory
        self.work_start_hour, self.work_end_hour = work_hours
        self.timezone = timezone
        self._briefing_sent_date: Optional[date] = None

    async def run(self) -> List[ProactiveAction]:
        now = datetime.now()
        today = now.date()

        # Only send once per day during work hours
        if self._briefing_sent_date == today:
            return []

        if now.hour < self.work_start_hour or now.hour >= self.work_end_hour:
            return []

        # Build briefing content
        self._briefing_sent_date = today

        briefing = f"""
═══════════════════════════════════════════════════════════════
📊 ABLE MORNING BRIEFING | {now.strftime('%A, %B %d %Y %H:%M')}
═══════════════════════════════════════════════════════════════

Good morning. Here's your daily briefing.

System Status: ✅ All systems operational

Today's Date: {today.strftime('%A, %B %d')}

Use commands:
  • /status - Full system status
  • /queue - View pending tasks
  • /goals - Active goals
  • /insights - AI planning insights

═══════════════════════════════════════════════════════════════
"""

        return [ProactiveAction(
            action_type=ProactiveActionType.DIGEST,
            title="Good Morning - Daily Briefing",
            description=briefing,
            urgency=3,
            requires_human=False,
            auto_execute=True  # Send automatically
        )]


class MemoryConsolidationCheck(ProactiveCheck):
    """
    Periodically consolidate and compress memory.

    - Removes duplicate memories
    - Summarizes old conversation logs
    - Archives memories older than threshold
    """

    name = "memory_consolidation"
    interval_seconds = 3600 * 4  # Every 4 hours

    def __init__(self, memory=None, max_memories: int = 10000):
        super().__init__()
        self.memory = memory
        self.max_memories = max_memories

    async def run(self) -> List[ProactiveAction]:
        if not self.memory:
            return []

        actions = []
        try:
            stats = await self.memory.get_stats()
            total = stats.get("total_memories", 0)

            if total > self.max_memories:
                actions.append(ProactiveAction(
                    action_type=ProactiveActionType.ALERT,
                    title="Memory Near Capacity",
                    description=(
                        f"Memory store has {total:,} entries (limit: {self.max_memories:,}). "
                        "Consider archiving old memories to maintain performance."
                    ),
                    urgency=7,
                    requires_human=True,
                    data={"total_memories": total, "limit": self.max_memories}
                ))
            elif total > self.max_memories * 0.8:
                actions.append(ProactiveAction(
                    action_type=ProactiveActionType.SUGGESTION,
                    title="Memory Approaching Capacity",
                    description=f"Memory at {total/self.max_memories:.0%} capacity ({total:,} entries).",
                    urgency=4,
                    data={"total_memories": total}
                ))

        except Exception as e:
            logger.warning(f"Memory check failed: {e}")

        return actions


class AnomalyDetectionCheck(ProactiveCheck):
    """
    Detect anomalies in usage patterns that might indicate:
    - Abuse/attacks
    - Runaway processes
    - Billing anomalies
    """

    name = "anomaly_detection"
    interval_seconds = 600  # Every 10 minutes

    def __init__(self, rate_limiter=None, billing_tracker=None):
        super().__init__()
        self.rate_limiter = rate_limiter
        self.billing = billing_tracker
        self._baseline: Dict[str, float] = {}

    async def run(self) -> List[ProactiveAction]:
        actions = []

        # Check for unusual token usage (if billing tracker available)
        if self.billing:
            try:
                today_usage = await self.billing.get_today_usage()
                yesterday = self._baseline.get("yesterday_tokens", 0)

                if yesterday > 0 and today_usage > yesterday * 3:
                    actions.append(ProactiveAction(
                        action_type=ProactiveActionType.ALERT,
                        title="⚠️ Unusual Token Usage",
                        description=(
                            f"Today's usage ({today_usage:,} tokens) is 3x higher than yesterday. "
                            "This may indicate a runaway process or billing issue."
                        ),
                        urgency=8,
                        requires_human=True,
                        data={"today": today_usage, "baseline": yesterday}
                    ))
            except Exception:
                pass

        return actions


class LearningInsightCheck(ProactiveCheck):
    """
    Surface insights from recent learnings and patterns.

    Observes:
    - Recurring failed tasks
    - Newly discovered patterns
    - Skills that could be created from repeated tasks
    """

    name = "learning_insights"
    interval_seconds = 3600 * 6  # Every 6 hours

    def __init__(self, memory=None, min_pattern_count: int = 3, collector=None):
        super().__init__()
        self.memory = memory
        self.min_pattern_count = min_pattern_count
        self.collector = collector

    async def run(self) -> List[ProactiveAction]:
        if not self.memory:
            return []

        actions = []

        try:
            # Search for repeated failure patterns
            failure_memories = await self.memory.search(
                "error failed could not",
                memory_type="LEARNING",
                limit=20
            )

            if len(failure_memories) >= self.min_pattern_count:
                description = (
                    f"Found {len(failure_memories)} similar failure events. "
                    "Consider creating a skill to handle this pattern, or reviewing system config."
                )
                # Group by similarity (simplified)
                actions.append(ProactiveAction(
                    action_type=ProactiveActionType.SUGGESTION,
                    title="💡 Recurring Failure Pattern Detected",
                    description=description,
                    urgency=4,
                    requires_human=True,
                    data={"failure_count": len(failure_memories)}
                ))
                if self.collector:
                    self.collector.submit_insight(
                        title="Recurring Failure Pattern Detected",
                        description=description,
                        source="proactive.learning_insights",
                        category="learning_pattern",
                        data={"failure_count": len(failure_memories)},
                    )
        except Exception as e:
            logger.warning(f"Learning insight check failed: {e}")

        return actions


class SystemHealthCheck(ProactiveCheck):
    """
    Monitor system health indicators.
    """

    name = "system_health"
    interval_seconds = 300  # Every 5 minutes

    def __init__(self, gateway=None, rate_limiter=None):
        super().__init__()
        self.gateway = gateway
        self.rate_limiter = rate_limiter

    async def run(self) -> List[ProactiveAction]:
        actions = []

        try:
            cpu_pct, mem_pct, mem_avail_mb = None, None, None

            # Try /proc first (Linux/Docker — zero deps)
            try:
                with open('/proc/meminfo') as f:
                    meminfo = {}
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 2:
                            meminfo[parts[0].rstrip(':')] = int(parts[1])
                    total = meminfo.get('MemTotal', 1)
                    avail = meminfo.get('MemAvailable', total)
                    mem_pct = 100.0 * (1 - avail / total)
                    mem_avail_mb = avail // 1024
                load1, _, _ = os.getloadavg()
                cpu_count = os.cpu_count() or 1
                cpu_pct = min(100.0, (load1 / cpu_count) * 100)
            except (OSError, AttributeError):
                pass  # Not Linux — skip system health

            if cpu_pct is not None and cpu_pct > 90:
                actions.append(ProactiveAction(
                    action_type=ProactiveActionType.ALERT,
                    title="🔴 High CPU Usage",
                    description=f"CPU at {cpu_pct:.0f}%. System may be under load.",
                    urgency=8,
                    requires_human=True,
                    data={"cpu_percent": cpu_pct}
                ))

            if mem_pct is not None and mem_pct > 85:
                actions.append(ProactiveAction(
                    action_type=ProactiveActionType.ALERT,
                    title="🔴 Low Memory",
                    description=f"RAM at {mem_pct:.0f}% ({mem_avail_mb}MB free).",
                    urgency=8,
                    requires_human=True,
                    data={"memory_percent": mem_pct}
                ))

        except Exception as e:
            logger.warning(f"System health check failed: {e}")

        return actions


class BuddyNeedsCheck(ProactiveCheck):
    """
    Check buddy needs every 2 hours and surface nudges to the operator.
    Maps to Tamagotchi-style care reminders.
    """

    name = "buddy_needs"
    interval_seconds = 7200  # Every 2 hours

    async def run(self) -> List[ProactiveAction]:
        actions = []
        try:
            from able.core.buddy.nudge import check_nudge
            nudge = check_nudge()
            if nudge:
                actions.append(ProactiveAction(
                    action_type=ProactiveActionType.ALERT,
                    title="Buddy needs attention",
                    description=nudge,
                    urgency=3,
                    requires_human=False,
                ))
        except Exception as e:
            logger.debug(f"Buddy needs check skipped: {e}")
        return actions


class DistillationReadinessCheck(ProactiveCheck):
    """
    Autonomous self-improvement: monitor corpus growth and trigger training.

    Inspired by Karpathy's AutoResearch loop — the system autonomously:
    1. Tracks corpus pair count growth since last training run
    2. When corpus passes a threshold (default 100 new pairs), alerts
    3. Optionally auto-generates training scripts (MLX local or Unsloth Colab)
    4. After training completes, triggers eval validation

    This is the AGI piece: the system recognizes when it has enough new
    data to improve itself and takes action without being asked.
    """

    name = "distillation_readiness"
    interval_seconds = 3600 * 12  # Every 12 hours

    def __init__(
        self,
        corpus_threshold: int = 100,
        last_training_pairs: int = 0,
        auto_export: bool = True,
        collector=None,
    ):
        super().__init__()
        self.corpus_threshold = corpus_threshold
        self.last_training_pairs = last_training_pairs
        self.auto_export = auto_export
        self.collector = collector

    async def run(self) -> List[ProactiveAction]:
        actions = []
        try:
            from able.core.distillation.store import DistillationStore

            store = DistillationStore()
            all_pairs = store.get_pairs(limit=100_000)
            current_count = len(all_pairs)
            new_pairs = current_count - self.last_training_pairs

            if new_pairs < self.corpus_threshold:
                return actions

            # Corpus has grown enough — time to retrain
            description = (
                f"Distillation corpus grew by {new_pairs} pairs "
                f"(total: {current_count}). Threshold: {self.corpus_threshold}. "
                f"Ready for a training run."
            )

            # Auto-export training scripts if enabled
            export_paths = {}
            if self.auto_export:
                try:
                    from able.core.distillation.training.unsloth_exporter import UnslothExporter
                    exporter = UnslothExporter()

                    # Always export MLX for local training (free, no GPU wait)
                    mlx_path = exporter.export_mlx_training_script(
                        "able-nano-9b",
                        "~/.able/distillation/corpus/default/latest/train.jsonl",
                    )
                    export_paths["mlx_script"] = str(mlx_path)

                    # Export Colab notebook for cloud training
                    nb_path = exporter.export_notebook(
                        "able-nano-9b",
                        "~/.able/distillation/corpus/default/latest/train.jsonl",
                    )
                    export_paths["colab_notebook"] = str(nb_path)

                    description += (
                        f"\n\nTraining scripts auto-generated:"
                        f"\n  MLX local: {mlx_path}"
                        f"\n  Colab T4:  {nb_path}"
                    )
                except Exception as exc:
                    logger.warning("Auto-export failed: %s", exc)

            actions.append(ProactiveAction(
                action_type=ProactiveActionType.ALERT,
                title="Distillation corpus ready for training",
                description=description,
                urgency=6,
                requires_human=False,
                auto_execute=False,
                data={
                    "current_pairs": current_count,
                    "new_pairs": new_pairs,
                    "threshold": self.corpus_threshold,
                    **export_paths,
                },
            ))

            if self.collector:
                self.collector.submit_insight(
                    title="Distillation Corpus Ready",
                    description=f"{new_pairs} new pairs. Total: {current_count}.",
                    source="proactive.distillation_readiness",
                    category="self_improvement",
                    data={"pairs": current_count, "new": new_pairs},
                )

        except Exception as exc:
            logger.debug("Distillation readiness check skipped: %s", exc)

        return actions


class ClaudeCodeSessionCheck(ProactiveCheck):
    """
    Every 5 min: harvest any new Claude Code session not yet in corpus.

    Compares the active transcript_path to the last-harvested marker.
    When a new/changed session is detected, triggers a targeted harvest
    of just that JSONL file — no full-sweep needed.
    """

    name = "claude_code_session"
    interval_seconds = 300  # 5 minutes

    async def run(self) -> List[ProactiveAction]:
        from able.core.agi.claude_code_monitor import (
            get_new_session_to_harvest,
            mark_session_harvested,
        )
        from pathlib import Path

        path = get_new_session_to_harvest()
        if not path:
            return []

        try:
            from able.core.distillation.harvesters.claude_code_harvester import (
                ClaudeCodeHarvester,
            )
            from able.core.distillation.formatter import TrainingFormatter
            from able.core.distillation.store import DistillationStore

            harvester = ClaudeCodeHarvester()
            convos = harvester.harvest(source_path=Path(path).parent)
            if not convos:
                mark_session_harvested(path)
                return []

            formatter = TrainingFormatter()
            store = DistillationStore()
            new_count = 0
            for convo in convos:
                try:
                    pair = formatter.normalize(convo)
                    if store.save_training_pair(pair):
                        new_count += 1
                except Exception:
                    pass  # Skip malformed conversations

            mark_session_harvested(path)

            if new_count > 0:
                logger.info(
                    "Claude Code session harvested: %d new pairs from %s",
                    new_count,
                    Path(path).name,
                )
                return [
                    ProactiveAction(
                        action_type=ProactiveActionType.OBSERVATION,
                        title="Claude Code session harvested",
                        description=(
                            f"Ingested {new_count} new training pairs "
                            f"from {Path(path).name}"
                        ),
                        urgency=2,
                        data={"pairs": new_count, "source": path},
                    )
                ]
        except Exception as e:
            logger.warning("Claude Code session harvest failed: %s", e)

        return []


class ProactiveEngine:
    """
    Runs all proactive checks and dispatches actions.

    This is what makes ABLE feel like an AGI rather than a chatbot -
    it's always watching, always learning, always ready to surface
    relevant information or take autonomous action within its bounds.

    Usage:
        engine = ProactiveEngine(dispatcher=send_telegram_message)
        engine.add_check(DailyBriefingCheck(memory=hybrid_memory))
        engine.add_check(SystemHealthCheck())
        await engine.run_forever()
    """

    def __init__(
        self,
        dispatcher: Callable[[ProactiveAction], Awaitable[None]] = None,
        owner_channel: str = None,
    ):
        self.dispatcher = dispatcher
        self.owner_channel = owner_channel
        self.checks: List[ProactiveCheck] = []
        self._running = False
        self._action_history: List[ProactiveAction] = []
        self._dedup_window = 3600  # Don't repeat same action within 1 hour

    def add_check(self, check: ProactiveCheck):
        """Register a proactive check"""
        self.checks.append(check)
        logger.info(f"Registered proactive check: {check.name}")

    def remove_check(self, name: str):
        """Remove a check by name"""
        self.checks = [c for c in self.checks if c.name != name]

    async def run_forever(self, poll_interval: float = 60.0):
        """Main proactive loop - runs all checks continuously"""
        self._running = True
        logger.info(f"🤖 Proactive Engine started ({len(self.checks)} checks)")

        while self._running:
            try:
                await self._run_due_checks()
            except Exception as e:
                logger.error(f"Proactive engine error: {e}")

            await asyncio.sleep(poll_interval)

    async def _run_due_checks(self):
        """Run all checks that are due"""
        for check in self.checks:
            if not check.is_due():
                continue

            try:
                actions = await check.run()
                check.mark_run()

                for action in actions:
                    if not self._is_duplicate(action):
                        self._action_history.append(action)
                        await self._dispatch(action)

            except Exception as e:
                logger.warning(f"Check '{check.name}' failed: {e}")

    def _is_duplicate(self, action: ProactiveAction) -> bool:
        """Check if this action was recently sent"""
        now = time.time()
        for past in self._action_history:
            if (
                past.title == action.title and
                (now - past.created_at) < self._dedup_window
            ):
                return True
        return False

    async def _dispatch(self, action: ProactiveAction):
        """Dispatch an action to the appropriate channel"""
        log_msg = f"[{action.action_type.value.upper()}] {action.title}: {action.description[:100]}"

        if action.urgency >= 8:
            logger.warning(f"🚨 URGENT {log_msg}")
        elif action.urgency >= 5:
            logger.info(f"⚠️ {log_msg}")
        else:
            logger.info(f"ℹ️ {log_msg}")

        if self.dispatcher:
            try:
                await self.dispatcher(action)
            except Exception as e:
                logger.error(f"Failed to dispatch action: {e}")

    async def stop(self):
        """Stop the proactive engine"""
        self._running = False
        logger.info("Proactive Engine stopped")

    def get_recent_actions(self, limit: int = 20) -> List[Dict]:
        """Get recent proactive actions"""
        recent = sorted(self._action_history, key=lambda a: a.created_at, reverse=True)
        return [
            {
                "type": a.action_type.value,
                "title": a.title,
                "urgency": a.urgency,
                "timestamp": a.created_at,
            }
            for a in recent[:limit]
        ]


def create_default_engine(
    memory=None,
    rate_limiter=None,
    billing=None,
    gateway=None,
    dispatcher=None,
    collector=None,
    work_hours: tuple = (10, 19),
) -> ProactiveEngine:
    """
    Create a ProactiveEngine with all standard checks.

    This is the recommended way to initialize the proactive engine.
    """
    engine = ProactiveEngine(dispatcher=dispatcher)

    engine.add_check(DailyBriefingCheck(
        memory=memory,
        work_hours=work_hours
    ))

    engine.add_check(MemoryConsolidationCheck(memory=memory))
    engine.add_check(AnomalyDetectionCheck(rate_limiter=rate_limiter, billing_tracker=billing))
    engine.add_check(LearningInsightCheck(memory=memory, collector=collector))
    engine.add_check(SystemHealthCheck(gateway=gateway, rate_limiter=rate_limiter))
    engine.add_check(BuddyNeedsCheck())

    return engine
