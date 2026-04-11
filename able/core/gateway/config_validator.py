"""
F10 — Config Validation at Boot.

Validates YAML configuration files at startup, reporting actionable
errors before the system processes any requests. Also audits regex
patterns for catastrophic backtracking potential.

Forked from Hermes v0.8 PR #5426.

Usage:
    validator = ConfigValidator()
    report = validator.validate_all()
    if not report.valid:
        for error in report.errors:
            print(f"  {error}")
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """A single validation issue."""
    file: str
    field: str
    severity: str  # "error", "warning"
    message: str
    suggestion: str = ""


@dataclass
class ValidationReport:
    """Full validation report."""
    issues: List[ValidationIssue] = field(default_factory=list)
    files_checked: int = 0
    duration_ms: float = 0

    @property
    def valid(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def summary(self) -> str:
        status = "VALID" if self.valid else "INVALID"
        return (
            f"Config validation: {status} — "
            f"{len(self.errors)} errors, {len(self.warnings)} warnings, "
            f"{self.files_checked} files checked [{self.duration_ms:.0f}ms]"
        )


class ConfigValidator:
    """Validate ABLE configuration files at boot time."""

    def __init__(self, config_dir: str = "config"):
        self._config_dir = Path(config_dir)

    def validate_all(self) -> ValidationReport:
        """Run all validation checks.

        Returns a ValidationReport with all issues found.
        """
        start = time.perf_counter()
        issues = []
        files_checked = 0

        validators = [
            ("routing_config.yaml", self._validate_routing_config),
            ("scorer_weights.yaml", self._validate_scorer_weights),
            ("tool_permissions.yaml", self._validate_tool_permissions),
        ]

        for filename, validator_fn in validators:
            path = self._config_dir / filename
            if not path.exists():
                continue
            files_checked += 1
            try:
                import yaml
                with open(path) as f:
                    data = yaml.safe_load(f)
                if data is None:
                    issues.append(ValidationIssue(
                        file=filename, field="(root)",
                        severity="error",
                        message="File is empty or contains only comments",
                    ))
                    continue
                issues.extend(validator_fn(filename, data))
            except Exception as e:
                issues.append(ValidationIssue(
                    file=filename, field="(parse)",
                    severity="error",
                    message=f"YAML parse error: {e}",
                    suggestion="Check YAML syntax — common issues: tabs instead of spaces, missing colons",
                ))

        duration = (time.perf_counter() - start) * 1000
        return ValidationReport(
            issues=issues,
            files_checked=files_checked,
            duration_ms=duration,
        )

    def _validate_routing_config(
        self, filename: str, data: Dict
    ) -> List[ValidationIssue]:
        """Validate routing_config.yaml structure."""
        issues = []

        if not isinstance(data, dict):
            issues.append(ValidationIssue(
                file=filename, field="(root)",
                severity="error",
                message="Root must be a mapping",
            ))
            return issues

        # Check providers section
        providers = data.get("providers", [])
        if not isinstance(providers, list):
            issues.append(ValidationIssue(
                file=filename, field="providers",
                severity="error",
                message="providers must be a list",
            ))
        else:
            for i, p in enumerate(providers):
                if not isinstance(p, dict):
                    issues.append(ValidationIssue(
                        file=filename, field=f"providers[{i}]",
                        severity="error",
                        message="Provider entry must be a mapping",
                    ))
                    continue

                # Required fields
                for req in ("name", "provider_type"):
                    if req not in p:
                        issues.append(ValidationIssue(
                            file=filename, field=f"providers[{i}].{req}",
                            severity="error",
                            message=f"Missing required field '{req}'",
                        ))

                # Validate API key env reference
                api_key_env = p.get("api_key_env")
                if api_key_env and not os.environ.get(api_key_env):
                    issues.append(ValidationIssue(
                        file=filename, field=f"providers[{i}].api_key_env",
                        severity="warning",
                        message=f"Environment variable '{api_key_env}' not set",
                        suggestion=f"Set {api_key_env} in .env or environment",
                    ))

                # Validate cost fields are numbers
                for cost_field in ("cost_per_m_input", "cost_per_m_output"):
                    val = p.get(cost_field)
                    if val is not None and not isinstance(val, (int, float)):
                        issues.append(ValidationIssue(
                            file=filename, field=f"providers[{i}].{cost_field}",
                            severity="error",
                            message=f"'{cost_field}' must be a number, got {type(val).__name__}",
                        ))

        # Check for ${ENV_VAR} references that don't resolve
        issues.extend(self._check_env_references(filename, data))

        return issues

    def _validate_scorer_weights(
        self, filename: str, data: Dict
    ) -> List[ValidationIssue]:
        """Validate scorer_weights.yaml structure."""
        issues = []

        if not isinstance(data, dict):
            issues.append(ValidationIssue(
                file=filename, field="(root)",
                severity="error",
                message="Root must be a mapping",
            ))
            return issues

        # Check weights sum to reasonable values
        weights = data.get("weights", data)
        if isinstance(weights, dict):
            for key, val in weights.items():
                if isinstance(val, (int, float)):
                    if val < 0 or val > 10:
                        issues.append(ValidationIssue(
                            file=filename, field=f"weights.{key}",
                            severity="warning",
                            message=f"Weight {val} is outside typical range [0, 10]",
                        ))

        return issues

    def _validate_tool_permissions(
        self, filename: str, data: Dict
    ) -> List[ValidationIssue]:
        """Validate tool_permissions.yaml structure."""
        issues = []

        if not isinstance(data, dict):
            issues.append(ValidationIssue(
                file=filename, field="(root)",
                severity="error",
                message="Root must be a mapping",
            ))
            return issues

        valid_sections = {"always_allow", "ask_before", "never_allow"}
        for key in data:
            if key not in valid_sections:
                issues.append(ValidationIssue(
                    file=filename, field=key,
                    severity="warning",
                    message=f"Unknown section '{key}' (expected: {valid_sections})",
                ))

        # Validate regex patterns in rules
        for section in valid_sections:
            rules = data.get(section, [])
            if not isinstance(rules, list):
                continue
            for i, rule in enumerate(rules):
                if isinstance(rule, str):
                    issues.extend(
                        self._audit_regex(filename, f"{section}[{i}]", rule)
                    )

        return issues

    def _check_env_references(
        self, filename: str, data: Any, path: str = ""
    ) -> List[ValidationIssue]:
        """Check for unresolved ${ENV_VAR} references."""
        issues = []
        _ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")

        if isinstance(data, str):
            for match in _ENV_RE.finditer(data):
                var = match.group(1)
                has_default = match.group(2) is not None
                if not os.environ.get(var) and not has_default:
                    issues.append(ValidationIssue(
                        file=filename, field=path or "(value)",
                        severity="warning",
                        message=f"Unresolved env reference ${{'{var}'}} with no default",
                        suggestion=f"Set {var} or add a default: ${{{var}:-default_value}}",
                    ))
        elif isinstance(data, dict):
            for k, v in data.items():
                issues.extend(self._check_env_references(filename, v, f"{path}.{k}" if path else k))
        elif isinstance(data, list):
            for i, v in enumerate(data):
                issues.extend(self._check_env_references(filename, v, f"{path}[{i}]"))

        return issues

    @staticmethod
    def _audit_regex(
        filename: str, field: str, pattern: str
    ) -> List[ValidationIssue]:
        """Audit a regex pattern for validity and catastrophic backtracking risk."""
        issues = []

        # Check if it's valid regex
        try:
            re.compile(pattern)
        except re.error as e:
            issues.append(ValidationIssue(
                file=filename, field=field,
                severity="error",
                message=f"Invalid regex: {e}",
            ))
            return issues

        # Heuristic check for catastrophic backtracking
        # Patterns like (a+)+ or (a|b)* with overlapping alternatives
        danger_patterns = [
            (r'\(\w\+\)\+', "Nested quantifiers (a+)+"),
            (r'\(\.\*\)\+', "Nested quantifiers (.*)+"),
            (r'\(\w\+\)\*', "Nested quantifiers (a+)*"),
        ]
        for danger, description in danger_patterns:
            if re.search(danger, pattern):
                issues.append(ValidationIssue(
                    file=filename, field=field,
                    severity="warning",
                    message=f"Potential catastrophic backtracking: {description}",
                    suggestion="Simplify the regex or use atomic grouping",
                ))

        # Check if pattern is unreasonably long
        if len(pattern) > 500:
            issues.append(ValidationIssue(
                file=filename, field=field,
                severity="warning",
                message=f"Very long regex ({len(pattern)} chars) — may be slow",
            ))

        return issues
