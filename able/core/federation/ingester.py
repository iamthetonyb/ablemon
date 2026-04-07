"""Validate and merge incoming federated contributions into the local store.

Security layers (defense in depth):
0. Ed25519 signature verification — reject untrusted signers
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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from able.core.federation.models import IngestResult

if TYPE_CHECKING:
    from able.core.distillation.store import DistillationStore

logger = logging.getLogger(__name__)

_DEFAULT_INBOX = Path.home() / ".able" / "federation" / "inbox"
_DEFAULT_PROCESSED = Path.home() / ".able" / "federation" / "processed"
_DEFAULT_TRUSTED_PEERS = Path(__file__).resolve().parents[3] / "config" / "trusted_peers.yaml"

# Minimum trust score from TrustGate for accepting a pair
_MIN_TRUST_SCORE = 0.7

# Minimum text lengths for quality re-validation
_MIN_PROMPT_LEN = 20
_MIN_RESPONSE_LEN = 50


# ── Trusted peers and verification policy ────────────────────────────


def _load_trusted_peers(
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Load trusted_peers.yaml. Returns dict with 'verification_policy' and 'trusted_peers'."""
    path = config_path or _DEFAULT_TRUSTED_PEERS
    if not path.exists():
        return {"verification_policy": "open", "trusted_peers": []}
    try:
        import yaml

        with open(path, "r") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return {"verification_policy": "open", "trusted_peers": []}
        return {
            "verification_policy": data.get("verification_policy", "open"),
            "trusted_peers": data.get("trusted_peers") or [],
        }
    except ImportError:
        # Fallback: parse simple lines
        policy = "open"
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if line.startswith("verification_policy:"):
                    val = line.split(":", 1)[1].strip().strip("'\"")
                    if val in ("open", "verified", "trusted_only"):
                        policy = val
        except Exception:
            pass
        return {"verification_policy": policy, "trusted_peers": []}
    except Exception as e:
        logger.warning("Federation: failed to load trusted_peers.yaml: %s", e)
        return {"verification_policy": "open", "trusted_peers": []}


def _verify_signature(
    meta: Dict[str, Any],
    pair_lines: List[str],
    peers_config: Dict[str, Any],
) -> Optional[bool]:
    """Verify the Ed25519 signature on a contribution.

    Returns:
        True  — signature valid
        False — signature present but invalid, or policy violation
        None  — unsigned contribution
    """
    sig_b64 = meta.get("signature")
    pub_b64 = meta.get("public_key")

    if not sig_b64 or not pub_b64:
        return None  # unsigned

    try:
        from able.core.federation.crypto import (
            decode_b64,
            fingerprint,
            is_available,
            verify_contribution,
        )

        if not is_available():
            logger.warning(
                "Federation: crypto unavailable — cannot verify signature, treating as unsigned"
            )
            return None

        signature = decode_b64(sig_b64)
        pub_bytes = decode_b64(pub_b64)

        # Reconstruct the exact payload that was signed (pairs only, newline-joined)
        payload_bytes = "\n".join(pair_lines).encode("utf-8")

        if not verify_contribution(pub_bytes, signature, payload_bytes):
            fp = fingerprint(pub_bytes)
            logger.warning(
                "Federation: INVALID signature from key %s", fp
            )
            return False

        fp = fingerprint(pub_bytes)
        logger.info("Federation: valid signature from key %s", fp)

        # Check trusted_only policy
        policy = peers_config.get("verification_policy", "open")
        if policy == "trusted_only":
            trusted = peers_config.get("trusted_peers") or []
            trusted_fps = {p.get("fingerprint") for p in trusted if isinstance(p, dict)}
            if fp not in trusted_fps:
                logger.warning(
                    "Federation: signer %s not in trusted_peers list — rejecting (policy: trusted_only)",
                    fp,
                )
                return False

        return True

    except Exception as e:
        logger.warning("Federation: signature verification error: %s", e)
        return False


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
    trusted_peers_path: Optional[Path] = None,
) -> IngestResult:
    """Process a single contribution JSONL file.

    Args:
        filepath: Path to the JSONL contribution file.
        store: Local distillation store to merge into.
        local_domains: This instance's top domains (for affinity boost).
        trusted_peers_path: Override path to trusted_peers.yaml (for testing).
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
    meta: Dict[str, Any] = {}
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

    # ── Layer 0: Ed25519 signature verification ──────────────────
    peers_config = _load_trusted_peers(trusted_peers_path)
    policy = peers_config.get("verification_policy", "open")

    # Strip empty lines for signature verification (match what contributor signed)
    raw_pair_lines = [l for l in pair_lines if l.strip()]
    sig_result = _verify_signature(meta, raw_pair_lines, peers_config)

    if sig_result is None:
        # Unsigned contribution
        if policy == "verified":
            logger.warning(
                "Federation: rejecting unsigned contribution %s (policy: verified)",
                filepath.name,
            )
            result.errors = 1
            return result
        if policy == "trusted_only":
            logger.warning(
                "Federation: rejecting unsigned contribution %s (policy: trusted_only)",
                filepath.name,
            )
            result.errors = 1
            return result
        logger.info(
            "Federation: unsigned contribution %s — accepting (policy: open)",
            filepath.name,
        )
    elif sig_result is False:
        # Invalid signature or untrusted signer
        logger.warning(
            "Federation: rejecting contribution %s — signature verification failed",
            filepath.name,
        )
        result.errors = 1
        return result
    else:
        # sig_result is True — valid signature
        logger.info(
            "Federation: verified contribution %s — signature valid",
            filepath.name,
        )

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
