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
from typing import Any, Iterable

from able.core.distillation.models import DistillationPair, TrainingPair
from able.core.distillation.reasoning_extractor import ReasoningExtractor

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
            corpus_dir or os.path.expanduser("~/.able/distillation/corpus")
        )
        self.quality_threshold = quality_threshold
        self.max_domain_pct = max_domain_pct
        self.reasoning_extractor = ReasoningExtractor()

    # ── Public API ────────────────────────────────────────────────────

    def build_nightly(
        self, pairs: list[dict] | list[TrainingPair] | list[DistillationPair], tenant_id: str = "default"
    ) -> CorpusBuildResult:
        """Incremental nightly build. Merges new pairs into latest corpus version."""
        existing = self._load_latest_pairs(tenant_id)
        merged = existing + pairs
        return self._build(merged, tenant_id)

    def build_full(
        self, pairs: list[dict] | list[TrainingPair] | list[DistillationPair], tenant_id: str = "default"
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

    def _build(
        self,
        pairs: list[dict] | list[TrainingPair] | list[DistillationPair],
        tenant_id: str,
        max_domain_pct: float | None = None,
    ) -> CorpusBuildResult:
        """Core build: filter -> deduplicate -> balance -> split -> write.

        Args:
            max_domain_pct: Override domain cap for this build. None uses
                self.max_domain_pct (0.30). Tenant builds should pass a
                higher value so the tenant's core domain isn't trimmed.
        """
        canonical_pairs = self._coerce_pairs(pairs)

        if not canonical_pairs:
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

        filtered = self._filter_pairs(canonical_pairs)
        deduped = self._deduplicate(filtered)
        cap = max_domain_pct if max_domain_pct is not None else self.max_domain_pct
        balanced = self._balance_domains(deduped, max_pct=cap)
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

    def _balance_domains(
        self, pairs: list[dict], max_pct: float | None = None
    ) -> list[dict]:
        """Enforce max domain percentage. Over-represented domains are sampled down."""
        if not pairs:
            return pairs

        pct = max_pct if max_pct is not None else self.max_domain_pct

        by_domain: dict[str, list[dict]] = defaultdict(list)
        for p in pairs:
            domain = p.get("domain", "default")
            by_domain[domain].append(p)

        max_per_domain = int(len(pairs) * pct)
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

        self._write_jsonl(version_dir / "train_pairs.jsonl", train)
        self._write_jsonl(version_dir / "val_pairs.jsonl", val)
        self._write_jsonl(version_dir / "test_pairs.jsonl", test)

        self._write_chatml_jsonl(version_dir / "train.jsonl", train)
        self._write_chatml_jsonl(version_dir / "val.jsonl", val)
        self._write_chatml_jsonl(version_dir / "test.jsonl", test)

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

    def _write_chatml_jsonl(self, path: Path, records: list[dict]) -> None:
        """Write ChatML exports for model training."""
        from able.core.distillation.formatter import TrainingFormatter

        formatter = TrainingFormatter()
        with open(path, "w") as f:
            for record in records:
                pair = TrainingPair.from_corpus_record(record)
                f.write(json.dumps(formatter.to_chatml(pair), separators=(",", ":")) + "\n")

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
        split_names = (
            ("train_pairs.jsonl", "train.jsonl"),
            ("val_pairs.jsonl", "val.jsonl"),
            ("test_pairs.jsonl", "test.jsonl"),
        )
        for preferred, fallback in split_names:
            split_path = latest / preferred
            if not split_path.exists():
                split_path = latest / fallback
            if split_path.exists():
                with open(split_path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                pairs.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
        return self._coerce_pairs(pairs)

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

    def _coerce_pairs(self, pairs: Iterable[Any]) -> list[dict]:
        """Normalize supported pair types into the builder's canonical dict shape."""
        canonical: list[dict] = []
        for pair in pairs:
            if isinstance(pair, TrainingPair):
                canonical.append(pair.to_corpus_record())
                continue
            if isinstance(pair, DistillationPair):
                canonical.append(
                    TrainingPair(
                        id=pair.id,
                        prompt=pair.prompt,
                        response=pair.gold_response,
                        domain=pair.domain,
                        quality_score=pair.quality_score,
                        source=pair.tags[0] if pair.tags else "distillation_store",
                        teacher_model=pair.gold_model,
                        thinking=pair.gold_thinking,
                        tenant_id=pair.tenant_id,
                        content_hash=pair.content_hash,
                        created_at=pair.created_at,
                    ).to_corpus_record()
                )
                continue
            record = dict(pair)
            if "conversations" in record:
                canonical.append(TrainingPair.from_chatml(record).to_corpus_record())
                continue
            if "gold_response" in record and "response" not in record:
                record["response"] = record["gold_response"]
            if "teacher_model" not in record and "model" in record:
                record["teacher_model"] = record["model"]
            record.setdefault("source", record.get("teacher_model", "unknown"))
            record.setdefault("response_accepted", True)
            record.setdefault("escalated", False)
            record.setdefault("tenant_id", "default")
            canonical.append(record)
        return canonical

    def build_tenant_with_able_base(
        self,
        tenant_id: str,
        able_share: float = 0.20,
        able_domains: list[str] | None = None,
    ) -> CorpusBuildResult:
        """Build a tenant corpus enriched with relevant ABLE core pairs.

        Following the Jackrong distillation approach:
        - Tenant's own data is the majority (~80%)
        - High-quality ABLE reasoning (self-improvement, AGI, security,
          coding) supplements general capability (~20%)
        - train_on_responses_only is enforced downstream by Axolotl config
        - Only pairs with quality >= threshold are included

        Args:
            tenant_id: The tenant to build corpus for.
            able_share: Max fraction of ABLE core pairs (default 0.20).
            able_domains: Which ABLE domains to include. Default: all.
        """
        from able.core.distillation.store import DistillationStore

        store = DistillationStore()

        # Get tenant's own high-quality pairs
        tenant_pairs = store.get_pairs(
            tenant_id=tenant_id,
            min_quality=self.quality_threshold,
            limit=100_000,
        )

        # Get ABLE core high-quality pairs
        able_pairs = store.get_pairs(
            tenant_id="default",
            min_quality=self.quality_threshold,
            limit=100_000,
        )

        # Filter ABLE by relevant domains if specified
        if able_domains:
            able_pairs = [p for p in able_pairs if p.domain in able_domains]

        # Calculate how many ABLE pairs to include
        tenant_count = len(tenant_pairs)
        if tenant_count == 0:
            logger.warning("No tenant pairs for %s, using ABLE core only", tenant_id)
            max_able = len(able_pairs)
        else:
            max_able = int(tenant_count * (able_share / (1 - able_share)))

        # Take best ABLE pairs by quality score
        able_pairs.sort(key=lambda p: p.quality_score, reverse=True)
        selected_able = able_pairs[:max_able]

        # Convert to dict format for build pipeline
        all_pairs = []
        for p in tenant_pairs:
            all_pairs.append({
                "prompt": p.prompt,
                "response": p.gold_response,
                "domain": p.domain,
                "quality_score": p.quality_score,
                "model": p.gold_model,
                "tenant_id": tenant_id,
                "source": "tenant",
            })
        for p in selected_able:
            all_pairs.append({
                "prompt": p.prompt,
                "response": p.gold_response,
                "domain": p.domain,
                "quality_score": p.quality_score,
                "model": p.gold_model,
                "tenant_id": tenant_id,  # tagged as tenant for isolation
                "source": "able_base",
            })

        logger.info(
            "Building %s corpus: %d tenant + %d ABLE base = %d total",
            tenant_id, len(tenant_pairs), len(selected_able), len(all_pairs),
        )
        # Tenant's primary domain shouldn't be capped — that's the whole
        # point of the tenant.  Use 0.80 so the domain can be up to 80% of
        # the corpus while still leaving room for ABLE enrichment diversity.
        return self._build(all_pairs, tenant_id, max_domain_pct=0.80)

    # ── Reverse flow: tenant → ABLE core ──────────────────────────

    def promote_to_core(
        self,
        tenant_id: str,
        min_quality: float = 0.90,
        relevant_domains: list[str] | None = None,
        max_pairs: int = 200,
    ) -> dict:
        """Promote high-quality tenant pairs into the ABLE core corpus.

        Bidirectional flow: ABLE enriches tenants (build_tenant_with_able_base)
        and tenants contribute discoveries back to ABLE (this method).

        Works at the corpus level, not the store level — tenant pairs are
        included directly into the next ABLE core corpus build. The store
        uses content_hash dedup across all tenants (same content = one row),
        so this method queries tenant pairs and mixes them into a core build.

        Args:
            tenant_id: Source tenant.
            min_quality: Quality floor for promotion (default 0.90 — stricter
                than the 0.80 training threshold so only the best flows back).
            relevant_domains: If set, only promote pairs from these domains.
                None means all domains are eligible.
            max_pairs: Cap on pairs promoted per call to avoid flooding core.

        Returns:
            Dict with promotion stats and the resulting corpus build.
        """
        from able.core.distillation.store import DistillationStore

        store = DistillationStore()

        # Get ABLE core pairs
        core_pairs = store.get_pairs(
            tenant_id="default",
            min_quality=self.quality_threshold,
            limit=100_000,
        )

        # Get the tenant's best pairs
        tenant_best = store.get_pairs(
            tenant_id=tenant_id,
            min_quality=min_quality,
            limit=max_pairs * 2,
        )

        if relevant_domains:
            tenant_best = [
                p for p in tenant_best if p.domain in relevant_domains
            ]

        tenant_best.sort(key=lambda p: p.quality_score, reverse=True)
        promoted = tenant_best[:max_pairs]

        # Build combined corpus: all core + best tenant contributions
        all_pairs = []
        for p in core_pairs:
            all_pairs.append({
                "prompt": p.prompt,
                "response": p.gold_response,
                "domain": p.domain,
                "quality_score": p.quality_score,
                "model": p.gold_model,
                "tenant_id": "default",
                "source": "able_core",
            })
        for p in promoted:
            all_pairs.append({
                "prompt": p.prompt,
                "response": p.gold_response,
                "domain": p.domain,
                "quality_score": p.quality_score,
                "model": p.gold_model,
                "tenant_id": "default",
                "source": f"promoted_from:{tenant_id}",
            })

        logger.info(
            "Building ABLE core corpus with %d core + %d promoted from %s",
            len(core_pairs), len(promoted), tenant_id,
        )
        result = self._build(all_pairs, tenant_id="default")

        return {
            "core_pairs": len(core_pairs),
            "promoted_from_tenant": len(promoted),
            "promoted_domains": self._count_domains(
                [{"domain": p.domain} for p in promoted]
            ),
            "corpus": {
                "version": result.version,
                "total": result.total,
                "train": result.train_count,
                "val": result.val_count,
                "test": result.test_count,
                "tier": result.tier,
                "avg_quality": result.avg_quality,
                "domains": result.domains,
            },
        }

    @staticmethod
    def _classify_tier(total: int) -> str:
        """Classify corpus tier by size."""
        if total >= 10_000:
            return "full"
        elif total >= 2_000:
            return "growth"
        return "seed"
