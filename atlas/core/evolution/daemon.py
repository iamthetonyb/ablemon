"""
Evolution Daemon — Main orchestrator for the M2.7 self-evolution cycle.

Runs as an async background task. Never user-facing.

Cycle: Collect → Analyze → Improve → Validate → Deploy

Can be started via:
    - atlas scheduler (cron)
    - CLI: python -m atlas.core.evolution.daemon
    - Programmatic: await daemon.run_cycle()
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .collector import MetricsCollector
from .analyzer import EvolutionAnalyzer, AnalysisResult
from .improver import WeightImprover, Improvement
from .validator import ChangeValidator, ValidationResult
from .deployer import ChangeDeployer, DeployResult

logger = logging.getLogger(__name__)


@dataclass
class EvolutionConfig:
    """Configuration for the evolution daemon."""

    # Paths
    weights_path: str = "config/scorer_weights.yaml"
    interaction_db: str = "data/interaction_log.db"
    cycle_log_dir: str = "data/evolution_cycles"

    # Timing
    cycle_interval_hours: int = 6
    min_interactions_for_cycle: int = 20
    lookback_hours: int = 24

    # Budget
    daily_budget_usd: float = 5.00
    monthly_budget_usd: float = 50.00

    # Safety
    max_changes_per_cycle: int = 3
    require_validation: bool = True
    auto_deploy: bool = True  # False = propose only, wait for approval


@dataclass
class CycleResult:
    """Result of a single evolution cycle."""

    cycle_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_ms: float = 0.0

    # Step results
    metrics_collected: bool = False
    interactions_analyzed: int = 0
    problems_found: int = 0
    improvements_proposed: int = 0
    improvements_approved: int = 0
    improvements_deployed: int = 0

    # Outcome
    new_version: int = 0
    deploy_result: Optional[DeployResult] = None
    validation_warnings: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def success(self) -> bool:
        return self.error == "" and self.metrics_collected


class EvolutionDaemon:
    """
    Background daemon that evolves the routing system.

    Uses MiniMax M2.7 (tier 3) for analysis — never user-facing.
    Falls back to rule-based analysis if M2.7 is unavailable.
    """

    def __init__(
        self,
        config: Optional[EvolutionConfig] = None,
        m27_provider=None,
    ):
        self.config = config or EvolutionConfig()
        self._collector = MetricsCollector(db_path=self.config.interaction_db)
        self._analyzer = EvolutionAnalyzer(provider=m27_provider)
        self._deployer = ChangeDeployer(weights_path=self.config.weights_path)
        self._running = False
        self._cycles_completed = 0
        self._daily_spend = 0.0

        # Ensure cycle log directory exists
        Path(self.config.cycle_log_dir).mkdir(parents=True, exist_ok=True)

    async def run_cycle(self) -> CycleResult:
        """
        Execute one evolution cycle: Collect → Analyze → Improve → Validate → Deploy.

        This is the primary entry point. Can be called by cron or manually.
        """
        result = CycleResult(
            cycle_id=f"evo_{int(time.time())}",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        start = time.perf_counter()

        try:
            # ── Step 1: Collect ───────────────────────────────
            metrics = self._collector.collect(
                hours=self.config.lookback_hours
            )
            result.metrics_collected = True

            # Check minimum interactions threshold
            total_interactions = sum(
                t.get("total", 0)
                for t in metrics.get("failures_by_tier", [])
            )
            result.interactions_analyzed = total_interactions

            if total_interactions < self.config.min_interactions_for_cycle:
                logger.info(
                    f"Skipping cycle: only {total_interactions} interactions "
                    f"(need {self.config.min_interactions_for_cycle})"
                )
                result.completed_at = datetime.now(timezone.utc).isoformat()
                result.duration_ms = (time.perf_counter() - start) * 1000
                self._log_cycle(result)
                return result

            # ── Step 2: Analyze ───────────────────────────────
            analysis = await self._analyzer.analyze(metrics)
            result.problems_found = len(analysis.problems)

            if not analysis.recommendations:
                logger.info("No recommendations from analysis — system is healthy")
                result.completed_at = datetime.now(timezone.utc).isoformat()
                result.duration_ms = (time.perf_counter() - start) * 1000
                self._log_cycle(result)
                return result

            # ── Step 3: Improve ───────────────────────────────
            current_weights = self._load_current_weights()
            improver = WeightImprover(current_weights)
            improvements = improver.generate_improvements(analysis)

            # Cap improvements per cycle
            improvements = improvements[: self.config.max_changes_per_cycle]
            result.improvements_proposed = len(improvements)

            if not improvements:
                logger.info("No valid improvements generated")
                result.completed_at = datetime.now(timezone.utc).isoformat()
                result.duration_ms = (time.perf_counter() - start) * 1000
                self._log_cycle(result)
                return result

            # ── Step 4: Validate ──────────────────────────────
            if self.config.require_validation:
                validator = ChangeValidator(current_weights)
                validation = validator.validate(improvements)
                result.validation_warnings = validation.warnings
                improvements = validation.approved_improvements
                result.improvements_approved = len(improvements)

                if not improvements:
                    logger.warning("All improvements rejected by validator")
                    result.completed_at = datetime.now(timezone.utc).isoformat()
                    result.duration_ms = (time.perf_counter() - start) * 1000
                    self._log_cycle(result)
                    return result
            else:
                result.improvements_approved = len(improvements)

            # ── Step 5: Deploy ────────────────────────────────
            if self.config.auto_deploy:
                new_weights = improver.apply_improvements(improvements)
                deploy_result = self._deployer.deploy(
                    new_weights, changes_count=len(improvements)
                )
                result.deploy_result = deploy_result
                result.improvements_deployed = (
                    deploy_result.changes_applied if deploy_result.success else 0
                )
                result.new_version = deploy_result.version

                if not deploy_result.success:
                    result.error = f"Deploy failed: {deploy_result.error}"
            else:
                logger.info(
                    f"Auto-deploy disabled. {len(improvements)} improvements "
                    f"ready for manual review."
                )

        except Exception as e:
            result.error = str(e)
            logger.error(f"Evolution cycle failed: {e}", exc_info=True)

        result.completed_at = datetime.now(timezone.utc).isoformat()
        result.duration_ms = (time.perf_counter() - start) * 1000
        self._cycles_completed += 1
        self._log_cycle(result)

        return result

    async def run_continuous(self):
        """
        Run the daemon continuously with configurable interval.

        This is the long-running entry point for background operation.
        """
        self._running = True
        logger.info(
            f"Evolution daemon started (interval: {self.config.cycle_interval_hours}h)"
        )

        while self._running:
            try:
                result = await self.run_cycle()
                if result.success:
                    logger.info(
                        f"Cycle {result.cycle_id} complete: "
                        f"{result.improvements_deployed} changes deployed"
                    )
                else:
                    logger.warning(
                        f"Cycle {result.cycle_id} issue: {result.error or 'no changes needed'}"
                    )
            except Exception as e:
                logger.error(f"Daemon cycle error: {e}", exc_info=True)

            # Sleep until next cycle
            await asyncio.sleep(self.config.cycle_interval_hours * 3600)

        logger.info("Evolution daemon stopped")

    def stop(self):
        """Signal the daemon to stop after current cycle."""
        self._running = False

    def _load_current_weights(self) -> Dict[str, Any]:
        """Load current scorer weights from disk."""
        path = Path(self.config.weights_path)
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f) or {}
        return {}

    def _log_cycle(self, result: CycleResult):
        """Persist cycle result to disk for audit."""
        log_path = Path(self.config.cycle_log_dir) / f"{result.cycle_id}.json"
        try:
            data = {
                "cycle_id": result.cycle_id,
                "started_at": result.started_at,
                "completed_at": result.completed_at,
                "duration_ms": result.duration_ms,
                "metrics_collected": result.metrics_collected,
                "interactions_analyzed": result.interactions_analyzed,
                "problems_found": result.problems_found,
                "improvements_proposed": result.improvements_proposed,
                "improvements_approved": result.improvements_approved,
                "improvements_deployed": result.improvements_deployed,
                "new_version": result.new_version,
                "validation_warnings": result.validation_warnings,
                "error": result.error,
                "success": result.success,
            }
            with open(log_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to log cycle: {e}")

    @property
    def status(self) -> Dict[str, Any]:
        """Current daemon status."""
        return {
            "running": self._running,
            "cycles_completed": self._cycles_completed,
            "daily_spend_usd": self._daily_spend,
            "config": {
                "interval_hours": self.config.cycle_interval_hours,
                "min_interactions": self.config.min_interactions_for_cycle,
                "auto_deploy": self.config.auto_deploy,
                "max_changes_per_cycle": self.config.max_changes_per_cycle,
            },
        }


# ── CLI Entry Point ───────────────────────────────────────────

async def _main():
    """CLI entry point for the evolution daemon."""
    import argparse

    parser = argparse.ArgumentParser(description="ATLAS Evolution Daemon")
    parser.add_argument("--once", action="store_true", help="Run a single cycle")
    parser.add_argument("--interval", type=int, default=6, help="Hours between cycles")
    parser.add_argument("--min-interactions", type=int, default=20)
    parser.add_argument("--weights", default="config/scorer_weights.yaml")
    parser.add_argument("--db", default="data/interaction_log.db")
    parser.add_argument("--dry-run", action="store_true", help="Analyze but don't deploy")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = EvolutionConfig(
        weights_path=args.weights,
        interaction_db=args.db,
        cycle_interval_hours=args.interval,
        min_interactions_for_cycle=args.min_interactions,
        auto_deploy=not args.dry_run,
    )

    daemon = EvolutionDaemon(config=config)

    if args.once:
        result = await daemon.run_cycle()
        print(f"Cycle {result.cycle_id}: {'SUCCESS' if result.success else 'FAILED'}")
        print(f"  Interactions: {result.interactions_analyzed}")
        print(f"  Problems: {result.problems_found}")
        print(f"  Improvements: {result.improvements_proposed} proposed, "
              f"{result.improvements_approved} approved, "
              f"{result.improvements_deployed} deployed")
        if result.new_version:
            print(f"  New version: {result.new_version}")
        if result.error:
            print(f"  Error: {result.error}")
    else:
        await daemon.run_continuous()


if __name__ == "__main__":
    asyncio.run(_main())
