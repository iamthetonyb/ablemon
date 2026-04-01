"""
Research Action Pipeline — Classifies findings into actionable categories.

Takes research report action items from weekly_research.py and classifies them
as code_change, config_change, or skill_improvement. Routes each to the
appropriate handler (code proposer for low-risk config, learnings for skills).

Integration:
  - weekly_research.py → feeds analyzed findings here
  - code_proposer.py → handles code_change and config_change items
  - daemon.py → triggers this after the auto-improve step
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Project root — three levels up from this file (atlas/core/evolution/ → ATLAS/)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ── Action classification ─────────────────────────────────────

class ActionType:
    CODE_CHANGE = "code_change"
    CONFIG_CHANGE = "config_change"
    SKILL_IMPROVEMENT = "skill_improvement"


class RiskLevel:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Keywords that signal each action type
_CONFIG_KEYWORDS = [
    "weight", "threshold", "config", "yaml", "scorer_weights",
    "routing_config", "domain_adjustment", "tier_threshold",
    "budget", "interval", "parameter", "setting",
]

_CODE_KEYWORDS = [
    "refactor", "implement", "add function", "fix bug", "patch",
    "rewrite", "update code", "modify", "new endpoint", "migration",
    "python", ".py", "class", "method",
]

_SKILL_KEYWORDS = [
    "skill", "prompt", "SKILL.md", "instruction", "template",
    "enricher", "hard prompt", "tone", "voice", "format",
    "quality", "criteria",
]

# Files considered low-risk for auto-apply (shared with code_proposer)
try:
    from .code_proposer import _ALLOWED_AUTO_FILES as _LOW_RISK_FILES
except ImportError:
    _LOW_RISK_FILES = {
        "config/scorer_weights.yaml",
        "config/routing_config.yaml",
        "config/split_tests.yaml",
    }


@dataclass
class ClassifiedAction:
    """A research action item with classification metadata."""
    original: Dict[str, Any]
    action_type: str  # ActionType constant
    risk: str  # RiskLevel constant
    target_file: str = ""
    description: str = ""
    auto_applicable: bool = False


@dataclass
class PipelineResult:
    """Result of processing a research report through the action pipeline."""
    timestamp: str = ""
    actions_received: int = 0
    actions_classified: int = 0
    code_changes: List[ClassifiedAction] = field(default_factory=list)
    config_changes: List[ClassifiedAction] = field(default_factory=list)
    skill_improvements: List[ClassifiedAction] = field(default_factory=list)
    auto_applied: int = 0
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0


def _classify_action(action: Dict[str, Any]) -> ClassifiedAction:
    """
    Classify a single action item from the research report.

    Uses keyword matching on the action text, category, and target hints
    to determine type and risk level.
    """
    text = (action.get("action", "") + " " + action.get("ties_to", "")).lower()
    category = action.get("category", "").lower()
    effort = action.get("effort", "").lower()

    # Determine action type by keyword density
    config_score = sum(1 for kw in _CONFIG_KEYWORDS if kw in text)
    code_score = sum(1 for kw in _CODE_KEYWORDS if kw in text)
    skill_score = sum(1 for kw in _SKILL_KEYWORDS if kw in text)

    # Category hints boost the right type
    if category in ("upgrade", "infrastructure", "cost_savings"):
        config_score += 2
    elif category in ("new_capability",):
        code_score += 2
    elif category in ("client_value", "training"):
        skill_score += 1

    # Pick the highest-scoring type
    scores = {
        ActionType.CONFIG_CHANGE: config_score,
        ActionType.CODE_CHANGE: code_score,
        ActionType.SKILL_IMPROVEMENT: skill_score,
    }
    action_type = max(scores, key=scores.get)  # type: ignore[arg-type]

    # If all scores are 0, default to skill_improvement (safest)
    if all(v == 0 for v in scores.values()):
        action_type = ActionType.SKILL_IMPROVEMENT

    # Try to extract target file from action text
    target_file = _extract_target_file(text)

    # Determine risk level (pass target_file to avoid redundant extraction)
    risk = _assess_risk(action, action_type, effort, target_file)

    # Auto-applicable: config changes to known safe files with low risk
    auto_applicable = (
        action_type == ActionType.CONFIG_CHANGE
        and risk == RiskLevel.LOW
        and target_file in _LOW_RISK_FILES
    )

    return ClassifiedAction(
        original=action,
        action_type=action_type,
        risk=risk,
        target_file=target_file,
        description=action.get("action", ""),
        auto_applicable=auto_applicable,
    )


def _assess_risk(
    action: Dict[str, Any], action_type: str, effort: str, target_file: str = ""
) -> str:
    """Determine risk level for an action."""
    text = action.get("action", "").lower()

    # High risk: touches core python, security, production
    high_risk_patterns = [
        "security", "trust_gate", "production", "deploy",
        "database", "migration", "authentication", "encryption",
        "delete", "remove", "drop",
    ]
    if any(p in text for p in high_risk_patterns):
        return RiskLevel.HIGH

    # Code changes are at least medium risk
    if action_type == ActionType.CODE_CHANGE:
        return RiskLevel.HIGH if effort == "major" else RiskLevel.MEDIUM

    # Config changes to known files are low risk
    if action_type == ActionType.CONFIG_CHANGE:
        if target_file in _LOW_RISK_FILES:
            return RiskLevel.LOW
        return RiskLevel.MEDIUM

    # Skill improvements are medium risk (they change LLM behavior)
    return RiskLevel.MEDIUM


def _extract_target_file(text: str) -> str:
    """Try to extract a file path from action text."""
    # Look for common config file references
    for known_file in _LOW_RISK_FILES:
        if known_file.split("/")[-1].replace(".yaml", "") in text:
            return known_file

    # Look for path-like patterns
    match = re.search(r'([\w/]+\.(?:py|yaml|yml|md|json))', text)
    if match:
        return match.group(1)

    return ""


class ResearchActionPipeline:
    """
    Processes research report action items into classified, routable actions.

    Usage:
        pipeline = ResearchActionPipeline()
        result = await pipeline.process(report_path_or_actions)
    """

    def __init__(self, log_dir: Optional[str] = None):
        self.log_dir = Path(log_dir) if log_dir else _PROJECT_ROOT / "data" / "research_actions"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def process(
        self,
        report: Any,
        auto_apply: bool = False,
    ) -> PipelineResult:
        """
        Classify and route action items from a research report.

        Args:
            report: Either a path to a research report JSON, a dict with
                    'action_items' key, or a list of action item dicts.
            auto_apply: If True, auto-apply LOW-risk config changes via CodeProposer.

        Returns:
            PipelineResult with classified actions and apply status.
        """
        start = time.perf_counter()
        result = PipelineResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Normalize input to list of action dicts
        actions = self._extract_actions(report)
        result.actions_received = len(actions)

        if not actions:
            result.duration_ms = (time.perf_counter() - start) * 1000
            return result

        # Classify each action
        for action in actions:
            try:
                classified = _classify_action(action)
                result.actions_classified += 1

                if classified.action_type == ActionType.CODE_CHANGE:
                    result.code_changes.append(classified)
                elif classified.action_type == ActionType.CONFIG_CHANGE:
                    result.config_changes.append(classified)
                else:
                    result.skill_improvements.append(classified)
            except Exception as e:
                result.errors.append(f"Classification failed: {e}")
                logger.warning(f"Failed to classify action: {e}")

        # Auto-apply low-risk config changes if requested
        if auto_apply:
            applied = await self._auto_apply_config(result.config_changes)
            result.auto_applied = applied

        result.duration_ms = (time.perf_counter() - start) * 1000
        self._log_result(result)

        logger.info(
            f"[RESEARCH_PIPELINE] Processed {result.actions_received} actions: "
            f"{len(result.code_changes)} code, {len(result.config_changes)} config, "
            f"{len(result.skill_improvements)} skill | "
            f"{result.auto_applied} auto-applied ({result.duration_ms:.0f}ms)"
        )

        return result

    def _extract_actions(self, report: Any) -> List[Dict[str, Any]]:
        """Normalize report input into a list of action dicts."""
        if isinstance(report, list):
            return report

        if isinstance(report, dict):
            return report.get("action_items", [])

        # Try loading from file path
        if isinstance(report, (str, Path)):
            path = Path(report)
            if path.exists():
                try:
                    with open(path) as f:
                        data = json.load(f)
                    return data.get("action_items", [])
                except Exception as e:
                    logger.warning(f"Failed to load report from {path}: {e}")

        return []

    async def _auto_apply_config(
        self, config_changes: List[ClassifiedAction]
    ) -> int:
        """Auto-apply low-risk config changes via CodeProposer."""
        applied = 0
        auto_applicable = [c for c in config_changes if c.auto_applicable]

        if not auto_applicable:
            return 0

        try:
            from .code_proposer import CodeProposer
            proposer = CodeProposer()
        except ImportError:
            logger.warning("CodeProposer not available for auto-apply")
            return 0

        for change in auto_applicable:
            try:
                proposal = await proposer.propose(change)
                if proposal and proposal.get("status") == "proposed":
                    applied += 1
            except Exception as e:
                logger.warning(f"Auto-apply failed for {change.target_file}: {e}")

        return applied

    def _log_result(self, result: PipelineResult):
        """Log pipeline result to disk."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.log_dir / f"pipeline_{timestamp}.json"

        data = {
            "timestamp": result.timestamp,
            "actions_received": result.actions_received,
            "actions_classified": result.actions_classified,
            "code_changes": len(result.code_changes),
            "config_changes": len(result.config_changes),
            "skill_improvements": len(result.skill_improvements),
            "auto_applied": result.auto_applied,
            "errors": result.errors,
            "duration_ms": result.duration_ms,
            "classified": [
                {
                    "type": c.action_type,
                    "risk": c.risk,
                    "target": c.target_file,
                    "description": c.description,
                    "auto_applicable": c.auto_applicable,
                }
                for c in (
                    result.code_changes
                    + result.config_changes
                    + result.skill_improvements
                )
            ],
        }

        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to log pipeline result: {e}")
