"""Export and anonymize local distillation pairs for network sharing."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from able.core.federation.models import ContributionPackage

if TYPE_CHECKING:
    from able.core.distillation.store import DistillationStore

logger = logging.getLogger(__name__)

# Only share pairs above this quality threshold
NETWORK_QUALITY_FLOOR = 0.85

# ── PII patterns ─────────────────────────────────────────────────────

_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    # Phone numbers (US-style)
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[PHONE]"),
    # IP addresses
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[IP]"),
    # Greeting + name patterns
    (re.compile(r"(?:Dear|Hi|Hello|Hey)\s+[A-Z][a-z]+"), "[GREETING]"),
    # Home directory paths (macOS + Linux)
    (re.compile(r"/(?:home|Users)/[a-zA-Z0-9._-]+/"), "/[USER]/"),
    # Windows user paths
    (re.compile(r"C:\\Users\\[a-zA-Z0-9._-]+\\"), "C:\\\\[USER]\\\\"),
    # API key patterns
    (re.compile(r"(?:sk-|pk-|api_|token_)[A-Za-z0-9]{10,}"), "[API_KEY]"),
    # SSH keys
    (re.compile(r"(?:ssh-rsa|ssh-ed25519)\s+[A-Za-z0-9+/=]{20,}"), "[SSH_KEY]"),
    # Bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}"), "Bearer [TOKEN]"),
    # AWS access keys
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[AWS_KEY]"),
]


def _scrub_pii(text: str) -> str:
    """Remove personally identifiable information from text."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def scrub_for_network(
    prompt: str,
    response: str,
    domain: str,
    quality_score: float,
    content_hash: str,
    tags: list[str],
    response_confidence: Optional[float] = None,
) -> Optional[dict]:
    """Scrub a pair for network sharing. Returns None if pair becomes empty."""
    from able.core.distillation.harvesters.base import BaseHarvester

    # Strip scaffolding first (defense in depth)
    prompt = BaseHarvester._strip_scaffolding(prompt)
    response = BaseHarvester._strip_scaffolding(response)

    # Strip PII
    prompt = _scrub_pii(prompt)
    response = _scrub_pii(response)

    # Reject if too short after scrubbing
    if len(prompt.strip()) < 20 or len(response.strip()) < 50:
        return None

    record: dict = {
        "prompt": prompt.strip(),
        "response": response.strip(),
        "domain": domain,
        "quality_score": round(quality_score, 3),
        "content_hash": content_hash,
        "tags": [t for t in tags if t not in ("tenant_specific",)],
        "contributed_at": datetime.now(timezone.utc).isoformat(),
    }
    # Include confidence when available — lets receiving instances filter by
    # data quality.  Real logprob-derived (Ollama) vs proxy (GPT/Claude) is
    # transparent to recipients via the value distribution.
    if response_confidence is not None:
        record["response_confidence"] = round(response_confidence, 4)
    return record


def export_contribution(
    store: DistillationStore,
    instance_id: str,
    output_dir: Optional[Path] = None,
    since: Optional[datetime] = None,
    min_quality: float = NETWORK_QUALITY_FLOOR,
) -> Optional[ContributionPackage]:
    """Export local high-quality pairs as a JSONL contribution file.

    Args:
        store: The local distillation store.
        instance_id: This instance's UUID.
        output_dir: Where to write the JSONL. Default: ~/.able/federation/outbox/
        since: Only export pairs created after this timestamp.
        min_quality: Minimum quality score threshold.

    Returns:
        ContributionPackage if pairs were exported, None if nothing to share.
    """
    out = output_dir or (Path.home() / ".able" / "federation" / "outbox")
    out.mkdir(parents=True, exist_ok=True)

    # Query local pairs above quality threshold
    pairs = store.get_pairs(
        min_quality=min_quality,
        since=since,
        limit=100_000,
    )

    if not pairs:
        logger.info("Federation: no new pairs above quality %.2f to contribute", min_quality)
        return None

    # Scrub and package
    scrubbed: list[dict] = []
    domain_counts: dict[str, int] = {}

    for pair in pairs:
        # Skip network-sourced pairs (don't re-share what we ingested)
        if pair.tenant_id == "network":
            continue

        record = scrub_for_network(
            prompt=pair.prompt,
            response=pair.gold_response,
            domain=pair.domain,
            quality_score=pair.quality_score,
            content_hash=pair.content_hash,
            tags=pair.tags,
        )
        if record:
            scrubbed.append(record)
            domain_counts[pair.domain] = domain_counts.get(pair.domain, 0) + 1

    if not scrubbed:
        logger.info("Federation: all pairs filtered during scrubbing")
        return None

    # Write JSONL with metadata header
    ts = datetime.now(timezone.utc)
    filename = f"contribution-{ts.strftime('%Y%m%d-%H%M%S')}-{instance_id[:8]}.jsonl"
    filepath = out / filename

    metadata = {
        "type": "able_network_contribution",
        "version": 1,
        "instance_id": instance_id,
        "pair_count": len(scrubbed),
        "domains": domain_counts,
        "created_at": ts.isoformat(),
    }

    with open(filepath, "w") as f:
        f.write(json.dumps(metadata) + "\n")
        for record in scrubbed:
            f.write(json.dumps(record) + "\n")

    logger.info(
        "Federation: exported %d pairs across %d domains to %s",
        len(scrubbed),
        len(domain_counts),
        filepath.name,
    )

    return ContributionPackage(
        path=filepath,
        pair_count=len(scrubbed),
        domains=domain_counts,
        instance_id=instance_id,
        created_at=ts,
    )
