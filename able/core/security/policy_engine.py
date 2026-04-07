"""
Priority-based policy engine for tool and command permissions.

Enhances the simple 3-tier YAML (always_allow / ask_before / never_allow)
with structured PolicyRecord entries that support:
  - Priority ordering (higher priority rules evaluated first)
  - Glob pattern matching on tool/command names
  - Per-rule metadata (reason, added_by, expiry)
  - Scope-based policies (per-tenant, per-channel, global)
  - Composable with the existing CommandGuard

Inspired by RhysSullivan/executor's policy engine — adapted for
ABLE's Python stack and YAML-first configuration.

Usage:
    engine = PolicyEngine.from_yaml("config/tool_permissions.yaml")
    verdict = engine.evaluate("git push origin main")
    # PolicyVerdict(action=REQUIRES_APPROVAL, rule=..., reason="Git write operation")

The enhanced YAML format is backward-compatible:
  - Old format (lists) works as before
  - New format (list of dicts with priority/pattern/action) adds power

Enhanced format example in tool_permissions.yaml:
    policies:
      - pattern: "git push --force*"
        action: deny
        priority: 100
        reason: "Force push is destructive"
      - pattern: "git push*"
        action: require_approval
        priority: 50
        reason: "Git write operation"
      - pattern: "git *"
        action: allow
        priority: 10
        reason: "Git read operations"
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PolicyAction(Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class PolicyRecord:
    """A single policy rule with priority and metadata."""
    pattern: str                         # Glob pattern for tool/command name
    action: PolicyAction
    priority: int = 50                   # Higher = evaluated first
    scope: str = "global"                # global, tenant:<id>, channel:<name>
    reason: str = ""                     # Why this policy exists
    added_by: str = ""                   # Who added it
    expires: Optional[str] = None        # ISO date or None for permanent
    metadata: Dict[str, Any] = field(default_factory=dict)

    def matches(self, command: str) -> bool:
        """Check if command matches this policy's pattern."""
        cmd_lower = command.lower().strip()
        pattern_lower = self.pattern.lower()

        # Exact match
        if cmd_lower == pattern_lower:
            return True

        # Subcommand match: "git push" matches "git push origin main"
        if cmd_lower.startswith(pattern_lower + " "):
            return True

        # Glob match: "git*" matches "git push"
        if fnmatch.fnmatch(cmd_lower, pattern_lower):
            return True

        return False


@dataclass
class PolicyVerdict:
    """Result of policy evaluation."""
    action: PolicyAction
    rule: Optional[PolicyRecord] = None
    reason: str = ""
    matched: bool = False


class PolicyEngine:
    """
    Priority-based policy evaluator.

    Loads policies from the existing tool_permissions.yaml (backward-compatible)
    and optional enhanced 'policies' section with priority/pattern/action records.

    Evaluation order:
    1. Enhanced policies (sorted by priority, descending)
    2. Legacy never_allow / always_allow / ask_before lists
    3. Default: REQUIRE_APPROVAL (fail-safe)
    """

    def __init__(self, policies: Optional[List[PolicyRecord]] = None):
        self._policies: List[PolicyRecord] = sorted(
            policies or [],
            key=lambda p: p.priority,
            reverse=True,  # Higher priority first
        )

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> PolicyEngine:
        """Load policies from tool_permissions.yaml (supports both formats)."""
        path = Path(yaml_path)
        if not path.exists():
            logger.warning("Policy config not found at %s", path)
            return cls([])

        try:
            import yaml
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Failed to load policy config: %s", e)
            return cls([])

        policies: List[PolicyRecord] = []

        # ── Load enhanced policies (new format) ──────────────────────
        for entry in data.get("policies", []):
            if isinstance(entry, dict):
                try:
                    action_str = entry.get("action", "require_approval").lower()
                    action_map = {
                        "allow": PolicyAction.ALLOW,
                        "deny": PolicyAction.DENY,
                        "require_approval": PolicyAction.REQUIRE_APPROVAL,
                        "ask": PolicyAction.REQUIRE_APPROVAL,
                    }
                    policies.append(PolicyRecord(
                        pattern=entry["pattern"],
                        action=action_map.get(action_str, PolicyAction.REQUIRE_APPROVAL),
                        priority=int(entry.get("priority", 50)),
                        scope=entry.get("scope", "global"),
                        reason=entry.get("reason", ""),
                        added_by=entry.get("added_by", ""),
                        expires=entry.get("expires"),
                        metadata=entry.get("metadata", {}),
                    ))
                except (KeyError, ValueError) as e:
                    logger.warning("Invalid policy entry: %s", e)

        # ── Load legacy 3-tier lists (backward-compatible) ───────────
        # never_allow → priority 90 (high, but below explicit deny policies)
        for pattern in data.get("never_allow", []):
            if isinstance(pattern, str):
                policies.append(PolicyRecord(
                    pattern=pattern,
                    action=PolicyAction.DENY,
                    priority=90,
                    reason="Legacy never_allow list",
                ))

        # always_allow → priority 80
        for pattern in data.get("always_allow", []):
            if isinstance(pattern, str):
                policies.append(PolicyRecord(
                    pattern=pattern,
                    action=PolicyAction.ALLOW,
                    priority=80,
                    reason="Legacy always_allow list",
                ))

        # ask_before → priority 70
        for pattern in data.get("ask_before", []):
            if isinstance(pattern, str):
                policies.append(PolicyRecord(
                    pattern=pattern,
                    action=PolicyAction.REQUIRE_APPROVAL,
                    priority=70,
                    reason="Legacy ask_before list",
                ))

        logger.info("PolicyEngine loaded %d rules from %s", len(policies), path)
        return cls(policies)

    def evaluate(
        self,
        command: str,
        scope: str = "global",
    ) -> PolicyVerdict:
        """
        Evaluate a command against all policies.

        Returns the verdict from the highest-priority matching rule.
        If no rule matches, returns REQUIRE_APPROVAL (fail-safe).
        """
        for policy in self._policies:
            # Scope filtering: global matches everything, specific scope must match
            if policy.scope != "global" and policy.scope != scope:
                continue

            if policy.matches(command):
                return PolicyVerdict(
                    action=policy.action,
                    rule=policy,
                    reason=policy.reason,
                    matched=True,
                )

        # Default: fail-safe to require approval
        return PolicyVerdict(
            action=PolicyAction.REQUIRE_APPROVAL,
            reason="No matching policy — defaulting to require approval",
            matched=False,
        )

    def add_policy(self, record: PolicyRecord) -> None:
        """Add a policy and re-sort by priority."""
        self._policies.append(record)
        self._policies.sort(key=lambda p: p.priority, reverse=True)

    def remove_pattern(self, pattern: str) -> int:
        """Remove all policies matching a pattern. Returns count removed."""
        before = len(self._policies)
        self._policies = [p for p in self._policies if p.pattern != pattern]
        return before - len(self._policies)

    def list_policies(
        self,
        action: Optional[PolicyAction] = None,
        scope: str = "global",
    ) -> List[PolicyRecord]:
        """List policies, optionally filtered by action and/or scope."""
        result = self._policies
        if action:
            result = [p for p in result if p.action == action]
        if scope != "global":
            result = [p for p in result if p.scope in ("global", scope)]
        return result

    @property
    def policy_count(self) -> int:
        return len(self._policies)

    def stats(self) -> Dict[str, int]:
        """Policy distribution by action."""
        counts: Dict[str, int] = {"allow": 0, "deny": 0, "require_approval": 0}
        for p in self._policies:
            counts[p.action.value] = counts.get(p.action.value, 0) + 1
        return counts
