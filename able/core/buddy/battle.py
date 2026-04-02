"""
Battle system — eval-based challenges that train the agent AND the operator.

Every battle is a real promptfoo eval run.  Wins feed the distillation
pipeline, losses identify skill gaps for the auto-improver.
"""

from __future__ import annotations

import glob
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .model import (
    BattleRecord,
    BuddyState,
    XP_BATTLE_WIN,
    XP_BATTLE_DRAW,
    save_buddy,
)

logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Eval configs that map to battle domains
BATTLE_DOMAINS = {
    "security": "able/evals/eval-security.yaml",
    "copywriting": "able/evals/eval-copywriting.yaml",
    "code": "able/evals/eval-code-refactoring.yaml",
    "reasoning": "able/evals/eval-reasoning.yaml",
    "tools": "able/evals/eval-tools.yaml",
    "enricher": "able/evals/eval-enricher-3way.yaml",
    "shootout": "able/evals/eval-model-shootout.yaml",
}


def _battle_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.is_absolute():
        return path
    return (_PROJECT_ROOT / path).resolve()


def list_available_battles() -> list[str]:
    """Return domain names for which an eval config exists on disk."""
    available = []
    for domain, path in BATTLE_DOMAINS.items():
        if _battle_config_path(path).exists():
            available.append(domain)
    return available


def run_battle(
    buddy: BuddyState,
    domain: str,
    *,
    dry_run: bool = False,
) -> Optional[BattleRecord]:
    """
    Execute an eval-based battle.

    In dry_run mode, simulates a result without running promptfoo.
    In real mode, shells out to promptfoo and parses the output.
    """
    config_path = BATTLE_DOMAINS.get(domain)
    resolved_config = _battle_config_path(config_path) if config_path else None
    if not config_path or not resolved_config or not resolved_config.exists():
        logger.warning("No eval config for domain: %s", domain)
        return None

    if dry_run:
        return _simulate_battle(buddy, domain)

    return _real_battle(buddy, domain, str(resolved_config))


def _simulate_battle(buddy: BuddyState, domain: str) -> BattleRecord:
    """Simulate a battle result for testing / offline use."""
    import random

    total = 7
    # Species bonus makes victories more likely in matching domains
    bonus_domains = buddy.meta.get("bonus_domains", [])
    base_rate = 0.65
    if domain in bonus_domains:
        base_rate = 0.80

    passed = sum(1 for _ in range(total) if random.random() < base_rate)
    pct = passed / total * 100

    if pct >= 80:
        result = "win"
        xp = XP_BATTLE_WIN
    elif pct >= 60:
        result = "draw"
        xp = XP_BATTLE_DRAW
    else:
        result = "loss"
        xp = 5  # Consolation XP — you still showed up

    return BattleRecord(
        domain=domain,
        score_pct=pct,
        passed=passed,
        total=total,
        result=result,
        xp_earned=xp,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _real_battle(
    buddy: BuddyState, domain: str, config_path: str
) -> Optional[BattleRecord]:
    """Run a real promptfoo eval and parse the results."""
    try:
        proc = subprocess.run(
            ["npx", "promptfoo", "eval", "-c", config_path, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if proc.returncode != 0:
            logger.warning("promptfoo eval failed: %s", proc.stderr[:200])
            return None

        data = json.loads(proc.stdout)
        results = data.get("results", {})
        stats = results.get("stats", {})
        total = stats.get("total", 0)
        passed = stats.get("passed", 0)

        if total == 0:
            return None

        pct = passed / total * 100

        if pct >= 80:
            result = "win"
            xp = XP_BATTLE_WIN
        elif pct >= 60:
            result = "draw"
            xp = XP_BATTLE_DRAW
        else:
            result = "loss"
            xp = 5

        # Update buddy's eval pass count
        buddy.eval_passes += passed

        return BattleRecord(
            domain=domain,
            score_pct=pct,
            passed=passed,
            total=total,
            result=result,
            xp_earned=xp,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except subprocess.TimeoutExpired:
        logger.warning("Battle timed out for domain: %s", domain)
        return None
    except Exception as e:
        logger.warning("Battle failed: %s", e)
        return None
