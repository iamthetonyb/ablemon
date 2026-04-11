"""
Config Schema Validation (Claurst + Hermes v0.8 pattern).

Validates the 3 core ABLE YAML config files at boot time:
  - routing_config.yaml  — provider entries, tiers, costs
  - scorer_weights.yaml  — weight values and sum constraint
  - tool_permissions.yaml — no overlap between always_allow / never_allow

Run at startup via validate_all_configs() or per-file via validate_config().

Plan item: Module 5 — Config Schema Validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"

# Required fields per routing provider entry
_PROVIDER_REQUIRED = ("name", "tier", "provider_type", "model_id", "enabled")


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class ValidationError:
    """A single validation finding."""

    field: str
    message: str
    severity: str = "error"   # "error" | "warning"

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.field}: {self.message}"


@dataclass
class ValidationResult:
    """Outcome of validating a single config file."""

    valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)

    def all_issues(self) -> List[ValidationError]:
        return self.errors + self.warnings

    def __str__(self) -> str:
        status = "VALID" if self.valid else "INVALID"
        return (
            f"{status} — {len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        )


# ── Public entry points ───────────────────────────────────────────────────────


def validate_config(config_path: Path | str) -> ValidationResult:
    """Validate a single config file based on its filename.

    Dispatches to the appropriate validator. Unknown files return valid=True
    with a warning so the boot sequence is not blocked.
    """
    path = Path(config_path)
    name = path.name

    try:
        with open(path, "r") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return ValidationResult(
            valid=False,
            errors=[ValidationError(field="(file)", message=f"File not found: {path}")],
        )
    except yaml.YAMLError as exc:
        return ValidationResult(
            valid=False,
            errors=[ValidationError(field="(parse)", message=f"YAML parse error: {exc}")],
        )

    if name == "routing_config.yaml":
        return _validate_routing(data)
    if name == "scorer_weights.yaml":
        return _validate_scorer_weights(data)
    if name == "tool_permissions.yaml":
        return _validate_tool_permissions(data)

    return ValidationResult(
        valid=True,
        warnings=[
            ValidationError(
                field="(file)",
                message=f"No schema defined for '{name}' — skipped",
                severity="warning",
            )
        ],
    )


def validate_all_configs(config_dir: Path | str | None = None) -> dict[str, ValidationResult]:
    """Validate all 3 core config files.

    Returns a mapping of filename → ValidationResult. Logs a summary.
    """
    cfg_dir = Path(config_dir) if config_dir else _CONFIG_DIR
    targets = ["routing_config.yaml", "scorer_weights.yaml", "tool_permissions.yaml"]
    results: dict[str, ValidationResult] = {}

    for filename in targets:
        path = cfg_dir / filename
        result = validate_config(path)
        results[filename] = result
        if result.valid:
            logger.info("Config %s: OK (%d warning(s))", filename, len(result.warnings))
        else:
            for err in result.errors:
                logger.error("Config %s — %s", filename, err)
            for warn in result.warnings:
                logger.warning("Config %s — %s", filename, warn)

    total_errors = sum(len(r.errors) for r in results.values())
    total_warnings = sum(len(r.warnings) for r in results.values())
    logger.info(
        "Config validation complete: %d error(s), %d warning(s) across %d files",
        total_errors, total_warnings, len(targets),
    )
    return results


# ── Per-file validators ───────────────────────────────────────────────────────


def _validate_routing(data: Any) -> ValidationResult:
    """Validate routing_config.yaml."""
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    if not isinstance(data, dict):
        return ValidationResult(
            valid=False,
            errors=[ValidationError("(root)", "Must be a YAML mapping")],
        )

    providers = data.get("providers")
    if providers is None:
        errors.append(ValidationError("providers", "Missing required 'providers' key"))
        return ValidationResult(valid=False, errors=errors)

    if not isinstance(providers, list):
        errors.append(ValidationError("providers", "Must be a YAML list"))
        return ValidationResult(valid=False, errors=errors)

    for idx, entry in enumerate(providers):
        prefix = f"providers[{idx}]"
        if not isinstance(entry, dict):
            errors.append(ValidationError(prefix, "Each provider must be a mapping"))
            continue

        # Required fields
        for req in _PROVIDER_REQUIRED:
            if req not in entry:
                errors.append(
                    ValidationError(f"{prefix}.{req}", f"Required field '{req}' is missing")
                )

        # Tier range 1–5
        tier = entry.get("tier")
        if tier is not None:
            if not isinstance(tier, int) or not (1 <= tier <= 5):
                errors.append(
                    ValidationError(
                        f"{prefix}.tier",
                        f"Tier must be an integer 1–5, got {tier!r}",
                    )
                )

        # Cost fields >= 0
        for cost_key in ("cost_per_m_input", "cost_per_m_output"):
            cost = entry.get(cost_key)
            if cost is not None:
                if not isinstance(cost, (int, float)):
                    errors.append(
                        ValidationError(
                            f"{prefix}.{cost_key}",
                            f"Must be a number, got {type(cost).__name__}",
                        )
                    )
                elif cost < 0:
                    errors.append(
                        ValidationError(
                            f"{prefix}.{cost_key}",
                            f"Cost must be >= 0, got {cost}",
                        )
                    )

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _validate_scorer_weights(data: Any) -> ValidationResult:
    """Validate scorer_weights.yaml — weights must be >= 0 and sum to ~1.0."""
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    if not isinstance(data, dict):
        return ValidationResult(
            valid=False,
            errors=[ValidationError("(root)", "Must be a YAML mapping")],
        )

    features = data.get("features")
    if features is None:
        warnings.append(
            ValidationError("features", "No 'features' section found", severity="warning")
        )
        return ValidationResult(valid=True, warnings=warnings)

    if not isinstance(features, dict):
        errors.append(ValidationError("features", "Must be a mapping"))
        return ValidationResult(valid=False, errors=errors)

    # Collect numeric weight values (keys ending in _weight)
    weight_values: list[float] = []
    for key, val in features.items():
        if not key.endswith("_weight"):
            continue
        if not isinstance(val, (int, float)):
            errors.append(
                ValidationError(
                    f"features.{key}",
                    f"Weight must be a number, got {type(val).__name__}",
                )
            )
            continue
        if val < 0:
            errors.append(
                ValidationError(f"features.{key}", f"Weight must be >= 0, got {val}")
            )
        else:
            weight_values.append(float(val))

    if weight_values:
        total = sum(weight_values)
        if abs(total - 1.0) > 0.01:
            warnings.append(
                ValidationError(
                    "features.*_weight",
                    f"Weights sum to {total:.4f}, expected ~1.0 (tolerance 0.01)",
                    severity="warning",
                )
            )

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _validate_tool_permissions(data: Any) -> ValidationResult:
    """Validate tool_permissions.yaml — no tool in both always_allow and never_allow."""
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    if not isinstance(data, dict):
        return ValidationResult(
            valid=False,
            errors=[ValidationError("(root)", "Must be a YAML mapping")],
        )

    always = set(data.get("always_allow") or [])
    never = set(data.get("never_allow") or [])
    ask = set(data.get("ask_before") or [])

    # Type checks
    for section_name, raw in [
        ("always_allow", data.get("always_allow")),
        ("never_allow", data.get("never_allow")),
        ("ask_before", data.get("ask_before")),
    ]:
        if raw is not None and not isinstance(raw, list):
            errors.append(
                ValidationError(section_name, f"Section '{section_name}' must be a list")
            )

    # Overlap between always_allow and never_allow
    conflicts = always & never
    if conflicts:
        for item in sorted(conflicts):
            errors.append(
                ValidationError(
                    "always_allow / never_allow",
                    f"Tool '{item}' appears in both always_allow and never_allow",
                )
            )

    # Warn on overlap between ask_before and always_allow (not an error, but suspicious)
    ask_always_overlap = ask & always
    if ask_always_overlap:
        for item in sorted(ask_always_overlap):
            warnings.append(
                ValidationError(
                    "ask_before / always_allow",
                    f"Tool '{item}' is in both ask_before and always_allow — always_allow takes precedence",
                    severity="warning",
                )
            )

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)
