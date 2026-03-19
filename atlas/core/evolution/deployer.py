"""
Change Deployer — Step 5 of the evolution cycle.

Hot-deploys validated weight changes to the scorer.
Maintains rollback capability via versioned YAML files.
"""

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class DeployResult:
    """Result of deploying weight changes."""

    success: bool = False
    version: int = 0
    backup_path: str = ""
    changes_applied: int = 0
    error: str = ""


class ChangeDeployer:
    """
    Deploys validated weight changes to scorer_weights.yaml
    and triggers hot-reload.

    Maintains versioned backups for rollback:
        config/scorer_weights.v1.yaml
        config/scorer_weights.v2.yaml
        ...
    """

    def __init__(
        self,
        weights_path: str = "config/scorer_weights.yaml",
        max_backups: int = 10,
    ):
        self._weights_path = Path(weights_path)
        self._max_backups = max_backups

    def deploy(
        self, new_weights: Dict[str, Any], changes_count: int = 0
    ) -> DeployResult:
        """
        Write new weights to disk and create backup.

        Args:
            new_weights: Complete new weights dict
            changes_count: Number of improvements applied

        Returns:
            DeployResult with success status and backup path
        """
        result = DeployResult()

        try:
            # Read current version
            current_version = 0
            if self._weights_path.exists():
                with open(self._weights_path) as f:
                    current = yaml.safe_load(f) or {}
                current_version = current.get("version", 1)

            # Create backup
            backup_path = self._create_backup(current_version)
            result.backup_path = str(backup_path) if backup_path else ""

            # Update metadata
            new_weights["version"] = current_version + 1
            new_weights["last_updated"] = datetime.now(timezone.utc).isoformat()
            new_weights["updated_by"] = "evolution_daemon"

            # Write new weights
            self._weights_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._weights_path, "w") as f:
                yaml.dump(
                    new_weights,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                )

            result.success = True
            result.version = new_weights["version"]
            result.changes_applied = changes_count

            logger.info(
                f"Deployed scorer weights v{result.version} "
                f"({changes_count} changes)"
            )

            # Prune old backups
            self._prune_backups()

        except Exception as e:
            result.error = str(e)
            logger.error(f"Deploy failed: {e}")

        return result

    def rollback(self, to_version: Optional[int] = None) -> DeployResult:
        """
        Rollback to a previous version.

        Args:
            to_version: Specific version to restore. None = previous version.
        """
        result = DeployResult()

        try:
            if to_version is not None:
                backup = self._weights_path.parent / f"scorer_weights.v{to_version}.yaml"
            else:
                # Find the most recent backup
                backups = sorted(
                    self._weights_path.parent.glob("scorer_weights.v*.yaml"),
                    key=lambda p: int(p.stem.split(".v")[1]),
                    reverse=True,
                )
                if not backups:
                    result.error = "No backups available for rollback"
                    return result
                backup = backups[0]

            if not backup.exists():
                result.error = f"Backup not found: {backup}"
                return result

            # Restore backup
            shutil.copy2(backup, self._weights_path)

            with open(self._weights_path) as f:
                restored = yaml.safe_load(f) or {}

            result.success = True
            result.version = restored.get("version", 0)
            logger.info(f"Rolled back to scorer weights v{result.version}")

        except Exception as e:
            result.error = str(e)
            logger.error(f"Rollback failed: {e}")

        return result

    def _create_backup(self, version: int) -> Optional[Path]:
        """Create a versioned backup of current weights."""
        if not self._weights_path.exists():
            return None

        backup_path = (
            self._weights_path.parent / f"scorer_weights.v{version}.yaml"
        )
        shutil.copy2(self._weights_path, backup_path)
        logger.debug(f"Backed up weights to {backup_path}")
        return backup_path

    def _prune_backups(self):
        """Remove old backups beyond max_backups limit."""
        backups = sorted(
            self._weights_path.parent.glob("scorer_weights.v*.yaml"),
            key=lambda p: int(p.stem.split(".v")[1]),
        )
        while len(backups) > self._max_backups:
            oldest = backups.pop(0)
            oldest.unlink()
            logger.debug(f"Pruned old backup: {oldest}")
