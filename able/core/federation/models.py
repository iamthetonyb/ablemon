"""Data models for the federated distillation network."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict


@dataclass
class ContributionPackage:
    """A packaged JSONL file ready for network distribution."""

    path: Path
    pair_count: int
    domains: Dict[str, int]  # domain -> count
    instance_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class IngestResult:
    """Result of ingesting a contribution file."""

    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    errors: int = 0
    domains_ingested: Dict[str, int] = field(default_factory=dict)

    @property
    def total_processed(self) -> int:
        return self.accepted + self.rejected + self.duplicates + self.errors

    def merge(self, other: IngestResult) -> IngestResult:
        """Merge another result into this one (for batch ingestion)."""
        self.accepted += other.accepted
        self.rejected += other.rejected
        self.duplicates += other.duplicates
        self.errors += other.errors
        for domain, count in other.domains_ingested.items():
            self.domains_ingested[domain] = (
                self.domains_ingested.get(domain, 0) + count
            )
        return self
