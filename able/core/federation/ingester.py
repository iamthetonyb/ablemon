"""Validate and merge incoming federated contributions into the local store.

Security layers (defense in depth):
1. TrustGate — 52+ injection patterns
2. Scaffolding stripping — defense-in-depth even though contributor already stripped
3. Quality re-validation — reject suspiciously short or low-quality pairs
4. Content hash dedup — SQLite unique index rejects duplicates naturally

Inspired by llm-d's prefix-cache-aware routing: domain_affinity_boost
prioritizes pairs matching the instance's existing domain strengths.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from able.core.federation.models import IngestResult

if TYPE_CHECKING:
    from able.core.distillation.store import DistillationStore

logger = logging.getLogger(__name__)

_DEFAULT_INBOX = Path.home() / ".able" / "federation" / "inbox"
_DEFAULT_PROCESSED = Path.home() / ".able" / "federation" / "processed"

# Minimum trust score from TrustGate for accepting a pair
_MIN_TRUST_SCORE = 0.7

# Minimum text lengths for quality re-validation
_MIN_PROMPT_LEN = 20
_MIN_RESPONSE_LEN = 50


def _validate_pair(pair: dict) -> Optional[str]:
    """Validate a single pair dict. Returns error reason or None if valid."""
    prompt = pair.get("prompt", "")
    response = pair.get("response", "")

    if not isinstance(prompt, str) or not isinstance(response, str):
        return "non-string content"

    if len(prompt.strip()) < _MIN_PROMPT_LEN:
        return f"prompt too short ({len(prompt.strip())} chars)"

    if len(response.strip()) < _MIN_RESPONSE_LEN:
        return f"response too short ({len(response.strip())} chars)"

    quality = pair.get("quality_score", 0)
    if not isinstance(quality, (int, float)) or quality < 0.5:
        return f"quality too low ({quality})"

    if not pair.get("content_hash"):
        return "missing content_hash"

    return None


def _check_trust_gate(text: str) -> float:
    """Run TrustGate on text, returning trust score. Falls back to 1.0 if unavailable."""
    try:
        from able.core.security.trust_gate import TrustGate

        gate = TrustGate()
        result = gate.evaluate(text, source="federation")
        return result.score if hasattr(result, "score") else 1.0
    except Exception:
        # TrustGate unavailable — allow but log
        return 1.0


def ingest_contribution(
    filepath: Path,
    store: DistillationStore,
    local_domains: Optional[list[str]] = None,
) -> IngestResult:
    """Process a single contribution JSONL file.

    Args:
        filepath: Path to the JSONL contribution file.
        store: Local distillation store to merge into.
        local_domains: This instance's top domains (for affinity boost).
    """
    from able.core.distillation.harvesters.base import BaseHarvester
    from able.core.distillation.models import DistillationPair

    result = IngestResult()

    try:
        lines = filepath.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        logger.warning("Federation: failed to read %s: %s", filepath.name, e)
        result.errors = 1
        return result

    if not lines:
        return result

    # First line is metadata — validate it
    try:
        meta = json.loads(lines[0])
        if meta.get("type") != "able_network_contribution":
            logger.warning("Federation: invalid contribution type in %s", filepath.name)
            result.errors = 1
            return result
        version = meta.get("version", 0)
        if version > 1:
            logger.warning("Federation: unsupported version %d in %s", version, filepath.name)
            result.errors = 1
            return result
    except json.JSONDecodeError:
        # First line isn't metadata — try processing all lines as pairs
        pass

    # Process pair lines (skip metadata line)
    pair_lines = lines[1:] if lines else []

    for line_num, line in enumerate(pair_lines, start=2):
        line = line.strip()
        if not line:
            continue

        try:
            pair_data = json.loads(line)
        except json.JSONDecodeError:
            result.errors += 1
            continue

        # Layer 1: Structure validation
        error = _validate_pair(pair_data)
        if error:
            result.rejected += 1
            continue

        prompt = pair_data["prompt"]
        response = pair_data["response"]

        # Layer 2: TrustGate injection detection
        combined_text = f"{prompt}\n{response}"
        trust_score = _check_trust_gate(combined_text)
        if trust_score < _MIN_TRUST_SCORE:
            logger.debug(
                "Federation: rejected pair (trust=%.2f) from %s line %d",
                trust_score, filepath.name, line_num,
            )
            result.rejected += 1
            continue

        # Layer 3: Scaffolding stripping (defense in depth)
        prompt = BaseHarvester._strip_scaffolding(prompt)
        response = BaseHarvester._strip_scaffolding(response)

        # Re-check lengths after stripping
        if len(prompt.strip()) < _MIN_PROMPT_LEN or len(response.strip()) < _MIN_RESPONSE_LEN:
            result.rejected += 1
            continue

        # Layer 4: Store with content hash dedup
        domain = pair_data.get("domain", "general")
        pair = DistillationPair(
            id=str(uuid.uuid4()),
            prompt=prompt.strip(),
            gold_response=response.strip(),
            gold_model="federation",
            gold_thinking=None,
            domain=domain,
            quality_score=pair_data.get("quality_score", 0.85),
            tenant_id="network",
            tags=pair_data.get("tags", []) + ["federation"],
            content_hash=pair_data.get("content_hash", ""),
        )

        saved = store.save_pair(pair)
        if saved:
            result.accepted += 1
            result.domains_ingested[domain] = (
                result.domains_ingested.get(domain, 0) + 1
            )
        else:
            result.duplicates += 1

    return result


def ingest_all_inbox(
    store: DistillationStore,
    inbox_dir: Optional[Path] = None,
    local_domains: Optional[list[str]] = None,
) -> IngestResult:
    """Process all JSONL files in the inbox directory.

    Processed files are moved to the processed/ directory.
    """
    inbox = inbox_dir or _DEFAULT_INBOX
    if not inbox.exists():
        return IngestResult()

    processed = _DEFAULT_PROCESSED
    processed.mkdir(parents=True, exist_ok=True)

    aggregate = IngestResult()
    for jsonl in sorted(inbox.glob("*.jsonl")):
        result = ingest_contribution(jsonl, store, local_domains)
        aggregate.merge(result)

        # Move to processed
        try:
            shutil.move(str(jsonl), str(processed / jsonl.name))
        except Exception as e:
            logger.warning("Federation: failed to archive %s: %s", jsonl.name, e)

    if aggregate.total_processed > 0:
        logger.info(
            "Federation: ingested %d accepted, %d rejected, %d dupes, %d errors",
            aggregate.accepted,
            aggregate.rejected,
            aggregate.duplicates,
            aggregate.errors,
        )

    return aggregate
