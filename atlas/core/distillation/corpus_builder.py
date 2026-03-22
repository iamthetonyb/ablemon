"""
Corpus Builder — assembles versioned training datasets from harvested conversations.

Filtering rules:
  - quality_score >= threshold (default 0.8, configurable via env)
  - response_accepted == True (if field present)
  - NOT escalated
  - Deduplicated by content hash
  - Balanced across domains (max 30% from any single domain)
  - Per-tenant isolation (tenant corpora never mixed)

Corpus tiers:
  - Seed:   500-2,000 examples
  - Growth: 2,000-10,000
  - Full:   10,000-50,000

Output layout:
  {corpus_dir}/{tenant_id}/v{NNN}/
    train.jsonl
    val.jsonl
    test.jsonl
    metadata.yaml
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from atlas.core.distillation.reasoning_extractor import ReasoningExtractor

logger = logging.getLogger(__name__)

# Allow override via environment
_DEFAULT_QUALITY_THRESHOLD = float(
    os.environ.get("DISTILLATION_CORPUS_QUALITY_THRESHOLD", "0.8")
)


@dataclass
class CorpusBuildResult:
    """Result of a corpus build operation."""

    version: str
    train_count: int
    val_count: int
    test_count: int
    total: int
    domains: dict[str, int]
    avg_quality: float
    output_dir: str
    tier: str  # "seed" | "growth" | "full"


class CorpusBuilder:
    """
    Builds versioned training datasets from harvested conversations.

    Each build produces a versioned directory with train/val/test JSONL splits
    plus a metadata.yaml describing the corpus.
    """

    def __init__(
        self,
        corpus_dir: str | None = None,
        quality_threshold: float = _DEFAULT_QUALITY_THRESHOLD,
        max_domain_pct: float = 0.30,
    ):
        self.corpus_dir = Path(
            corpus_dir or os.path.expanduser("~/.atlas/distillation/corpus")
        )
        self.quality_threshold = quality_threshold
        self.max_domain_pct = max_domain_pct
        self.reasoning_extractor = ReasoningExtractor()

    # ── Public API ────────────────────────────────────────────────────

    def build_nightly(
        self, pairs: list[dict], tenant_id: str = "default"
    ) -> CorpusBuildResult:
        """Incremental nightly build. Merges new pairs into latest corpus version."""
        existing = self._load_latest_pairs(tenant_id)
        merged = existing + pairs
        return self._build(merged, tenant_id)

    def build_full(
        self, pairs: list[dict], tenant_id: str = "default"
    ) -> CorpusBuildResult:
        """Full rebuild from all supplied pairs."""
        return self._build(pairs, tenant_id)

    def get_stats(self, tenant_id: str = "default") -> dict:
        """Get corpus stats for a tenant."""
        tenant_dir = self.corpus_dir / tenant_id
        if not tenant_dir.exists():
            return {"versions": 0, "latest": None, "total_pairs": 0}

        versions = sorted(
            [d for d in tenant_dir.iterdir() if d.is_dir() and d.name.startswith("v")],
            key=lambda d: d.name,
        )
        if not versions:
            return {"versions": 0, "latest": None, "total_pairs": 0}

        latest = versions[-1]
        meta_path = latest / "metadata.yaml"
        metadata = {}
        if meta_path.exists():
            try:
                import yaml

                metadata = yaml.safe_load(meta_path.read_text()) or {}
            except Exception:
                pass

        return {
            "versions": len(versions),
            "latest": latest.name,
            "total_pairs": metadata.get("total", 0),
            "domains": metadata.get("domains", {}),
            "avg_quality": metadata.get("avg_quality", 0.0),
            "tier": metadata.get("tier", "unknown"),
        }

    # ── Core build pipeline ──────────────────────────────────────────

    def _build(self, pairs: list[dict], tenant_id: str) -> CorpusBuildResult:
        """Core build: filter -> deduplicate -> balance -> split -> write."""
        if not pairs:
            version = self._next_version(tenant_id)
            output_dir = str(self.corpus_dir / tenant_id / version)
            return CorpusBuildResult(
                version=version,
                train_count=0,
                val_count=0,
                test_count=0,
                total=0,
                domains={},
                avg_quality=0.0,
                output_dir=output_dir,
                tier="seed",
            )

        filtered = self._filter_pairs(pairs)
        deduped = self._deduplicate(filtered)
        balanced = self._balance_domains(deduped)
        enriched = self._enrich_reasoning(balanced)

        train, val, test = self._split_dataset(enriched)
        version = self._next_version(tenant_id)

        total = len(train) + len(val) + len(test)
        domains = self._count_domains(enriched)
        qualities = [p.get("quality_score", 0.0) for p in enriched]
        avg_quality = round(
            sum(qualities) / len(qualities) if qualities else 0.0, 4
        )
        tier = self._classify_tier(total)

        output_dir = self._write_corpus(
            train, val, test, version, tenant_id,
            domains=domains, avg_quality=avg_quality, tier=tier,
        )

        return CorpusBuildResult(
            version=version,
            train_count=len(train),
            val_count=len(val),
            test_count=len(test),
            total=total,
            domains=domains,
            avg_quality=avg_quality,
            output_dir=output_dir,
            tier=tier,
        )

    # ── Filtering ────────────────────────────────────────────────────

    def _filter_pairs(self, pairs: list[dict]) -> list[dict]:
        """Apply quality, acceptance, and escalation filters."""
        result = []
        for p in pairs:
            quality = p.get("quality_score", 0.0)
            if quality < self.quality_threshold:
                continue
            if p.get("escalated", False):
                continue
            if "response_accepted" in p and not p["response_accepted"]:
                continue
            result.append(p)
        return result

    def _deduplicate(self, pairs: list[dict]) -> list[dict]:
        """Remove duplicates by content hash of prompt + response."""
        seen: set[str] = set()
        result = []
        for p in pairs:
            content = (p.get("prompt", "") + p.get("response", "")).encode("utf-8")
            h = hashlib.sha256(content).hexdigest()[:16]
            if h not in seen:
                seen.add(h)
                result.append(p)
        return result

    def _balance_domains(self, pairs: list[dict]) -> list[dict]:
        """Enforce max domain percentage. Over-represented domains are sampled down."""
        if not pairs:
            return pairs

        by_domain: dict[str, list[dict]] = defaultdict(list)
        for p in pairs:
            domain = p.get("domain", "default")
            by_domain[domain].append(p)

        max_per_domain = int(len(pairs) * self.max_domain_pct)
        if max_per_domain < 1:
            max_per_domain = 1

        result = []
        for domain, domain_pairs in by_domain.items():
            if len(domain_pairs) > max_per_domain:
                # Sort by quality descending, take the best ones
                domain_pairs.sort(
                    key=lambda x: x.get("quality_score", 0.0), reverse=True
                )
                result.extend(domain_pairs[:max_per_domain])
            else:
                result.extend(domain_pairs)

        return result

    # ── Reasoning enrichment ─────────────────────────────────────────

    def _enrich_reasoning(self, pairs: list[dict]) -> list[dict]:
        """Extract and normalize reasoning from responses."""
        for p in pairs:
            response = p.get("response", "")
            if not response:
                continue
            extraction = self.reasoning_extractor.extract(response)
            p["reasoning_method"] = extraction.method
            if extraction.thinking:
                p["thinking"] = extraction.thinking
                p["clean_answer"] = extraction.answer
                p["normalized_response"] = self.reasoning_extractor.normalize(
                    extraction.thinking, extraction.answer
                )
            else:
                p["clean_answer"] = extraction.answer
                p["normalized_response"] = extraction.answer
        return pairs

    # ── Dataset splitting ────────────────────────────────────────────

    def _split_dataset(
        self,
        pairs: list[dict],
        train_pct: float = 0.85,
        val_pct: float = 0.10,
        test_pct: float = 0.05,
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Split into train/val/test sets. Stratified by domain."""
        if not pairs:
            return [], [], []

        by_domain: dict[str, list[dict]] = defaultdict(list)
        for p in pairs:
            by_domain[p.get("domain", "default")].append(p)

        train, val, test = [], [], []
        for domain_pairs in by_domain.values():
            # Shuffle within domain for randomness
            shuffled = list(domain_pairs)
            random.shuffle(shuffled)

            n = len(shuffled)
            n_test = max(1, int(n * test_pct)) if n >= 3 else 0
            n_val = max(1, int(n * val_pct)) if n >= 2 else 0
            n_train = n - n_val - n_test

            train.extend(shuffled[:n_train])
            val.extend(shuffled[n_train : n_train + n_val])
            test.extend(shuffled[n_train + n_val :])

        return train, val, test

    # ── File I/O ─────────────────────────────────────────────────────

    def _write_corpus(
        self,
        train: list[dict],
        val: list[dict],
        test: list[dict],
        version: str,
        tenant_id: str,
        *,
        domains: dict[str, int],
        avg_quality: float,
        tier: str,
    ) -> str:
        """Write JSONL files to versioned directory.

        Layout: {corpus_dir}/{tenant_id}/v{NNN}/{train,val,test}.jsonl + metadata.yaml
        """
        version_dir = self.corpus_dir / tenant_id / version
        version_dir.mkdir(parents=True, exist_ok=True)

        self._write_jsonl(version_dir / "train.jsonl", train)
        self._write_jsonl(version_dir / "val.jsonl", val)
        self._write_jsonl(version_dir / "test.jsonl", test)

        metadata = {
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "total": len(train) + len(val) + len(test),
            "train_count": len(train),
            "val_count": len(val),
            "test_count": len(test),
            "domains": domains,
            "avg_quality": avg_quality,
            "tier": tier,
            "quality_threshold": self.quality_threshold,
            "max_domain_pct": self.max_domain_pct,
        }

        meta_path = version_dir / "metadata.yaml"
        try:
            import yaml

            meta_path.write_text(yaml.dump(metadata, default_flow_style=False, sort_keys=False))
        except ImportError:
            # Fallback to JSON if PyYAML not available
            meta_path = version_dir / "metadata.json"
            meta_path.write_text(json.dumps(metadata, indent=2))

        logger.info(
            "Corpus %s/%s written: %d train, %d val, %d test",
            tenant_id,
            version,
            len(train),
            len(val),
            len(test),
        )
        return str(version_dir)

    def _write_jsonl(self, path: Path, records: list[dict]) -> None:
        """Write records to a JSONL file."""
        with open(path, "w") as f:
            for record in records:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _load_latest_pairs(self, tenant_id: str) -> list[dict]:
        """Load all pairs from the latest version for incremental builds."""
        tenant_dir = self.corpus_dir / tenant_id
        if not tenant_dir.exists():
            return []

        versions = sorted(
            [d for d in tenant_dir.iterdir() if d.is_dir() and d.name.startswith("v")],
            key=lambda d: d.name,
        )
        if not versions:
            return []

        latest = versions[-1]
        pairs = []
        for split in ("train.jsonl", "val.jsonl", "test.jsonl"):
            split_path = latest / split
            if split_path.exists():
                with open(split_path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                pairs.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
        return pairs

    # ── Helpers ──────────────────────────────────────────────────────

    def _next_version(self, tenant_id: str) -> str:
        """Determine next version number by scanning existing dirs."""
        tenant_dir = self.corpus_dir / tenant_id
        if not tenant_dir.exists():
            return "v001"

        existing = [
            d.name
            for d in tenant_dir.iterdir()
            if d.is_dir() and d.name.startswith("v")
        ]
        if not existing:
            return "v001"

        # Parse version numbers
        max_num = 0
        for name in existing:
            try:
                num = int(name[1:])
                max_num = max(max_num, num)
            except ValueError:
                continue

        return f"v{max_num + 1:03d}"

    @staticmethod
    def _count_domains(pairs: list[dict]) -> dict[str, int]:
        """Count pairs per domain."""
        counts: dict[str, int] = defaultdict(int)
        for p in pairs:
            counts[p.get("domain", "default")] += 1
        return dict(counts)

    @staticmethod
    def _classify_tier(total: int) -> str:
        """Classify corpus tier by size."""
        if total >= 10_000:
            return "full"
        elif total >= 2_000:
            return "growth"
        return "seed"
