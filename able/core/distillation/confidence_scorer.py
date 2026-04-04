"""
Response confidence scorer — derives a confidence signal (0.0–1.0) from
available provider signals.

### Signal sources by provider

| Provider | Signal quality | Method |
|----------|---------------|--------|
| Ollama   | **Real**      | Token logprobs from `/api/generate` (`logprobs: true`) |
| GPT/WHAM | Proxy         | Reasoning depth + response calibration |
| Claude   | Proxy         | Reasoning depth + response calibration |
| Fallback | Proxy         | Metadata-only heuristic |

The proxy method is honest — it does NOT claim to be logprob-derived when
real logprobs aren't available.  It IS derived from real execution signals:

  reasoning_depth  — length/structure of thinking_content (0–1)
  response_calibration — response length vs input complexity (0–1)
  audit_signal     — audit_score / 5.0, if already scored
  guidance_signal  — 1.0 – guidance_needed (lower guidance = more confident)

Composite: confidence = 0.30 * reasoning + 0.25 * calibration
                        + 0.30 * audit + 0.15 * guidance
When audit_score is missing, weights are redistributed across the other three.

### Why this matters for training

DPO pair quality is only as good as the signal.  High-confidence responses
are safe chosen samples.  Low-confidence responses need human auditing before
use.  The federation network uses this to gate contributions: instances only
share pairs where response_confidence >= 0.72 (configurable).

### Buddy level seeding

On first install, ABLE scans the user's recent interactions (across all
installed tools — CLI, Codex, Claude Code, ChatGPT, etc.) and computes a
domain-weighted confidence profile.  This seeds the buddy at a starting level
that reflects the user's actual domain expertise, not just days-since-install.

  domain_complexity_map maps each domain to an XP multiplier (1–3×).
  Starting XP = sum(avg_confidence * complexity_mult * pairs_in_domain * XP_PER_PAIR)
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Composite weight config ───────────────────────────────────────────────────

_W_REASONING   = 0.30
_W_CALIBRATION = 0.25
_W_AUDIT       = 0.30
_W_GUIDANCE    = 0.15

# Minimum chars of thinking for full reasoning_depth credit
_THINKING_FULL_LEN = 2000

# Domain complexity multipliers for buddy level seeding
DOMAIN_COMPLEXITY: Dict[str, float] = {
    "security":       3.0,
    "coding":         2.5,
    "financial":      2.5,
    "legal":          2.5,
    "research":       2.0,
    "planning":       1.8,
    "data":           1.8,
    "infrastructure": 2.2,
    "production":     2.2,
    "creative":       1.3,
    "copywriting":    1.2,
    "default":        1.0,
}

XP_PER_CONFIDENCE_PAIR = 8   # base XP per high-confidence interaction found


# ── Component scorers ─────────────────────────────────────────────────────────

def _reasoning_depth(thinking: Optional[str]) -> float:
    """0–1: how deep was the reasoning trace?"""
    if not thinking:
        return 0.0
    text = thinking.strip()
    length_score = min(len(text) / _THINKING_FULL_LEN, 1.0)
    structure_hits = sum(
        1 for kw in ("therefore", "because", "however", "first", "step", "consider", "→", "given")
        if kw.lower() in text.lower()
    )
    return min(length_score + structure_hits * 0.04, 1.0)


def _response_calibration(
    raw_input: Optional[str],
    raw_output: Optional[str],
    complexity_score: float,
) -> float:
    """0–1: was the response length calibrated to the input complexity?"""
    if not raw_input or not raw_output:
        return 0.5  # neutral if missing
    inp_len = len(raw_input)
    out_len = len(raw_output)
    if inp_len == 0:
        return 0.5

    ratio = out_len / inp_len
    # For simple tasks (complexity < 0.4) a short focused response is ideal (ratio ~1–3)
    # For complex tasks (complexity > 0.7) a thorough response is needed (ratio > 4)
    if complexity_score < 0.4:
        ideal_lo, ideal_hi = 0.5, 3.0
    elif complexity_score < 0.7:
        ideal_lo, ideal_hi = 1.0, 6.0
    else:
        ideal_lo, ideal_hi = 2.0, 12.0

    if ideal_lo <= ratio <= ideal_hi:
        return 1.0
    if ratio < ideal_lo:
        # Too short — penalise proportionally
        return max(0.2, ratio / ideal_lo)
    # Too long — mild penalty
    return max(0.4, 1.0 - (ratio - ideal_hi) / (ideal_hi * 2))


def _audit_signal(audit_score: Optional[float]) -> Optional[float]:
    """Convert 0–5 audit_score to 0–1, or None if not yet scored."""
    if audit_score is None:
        return None
    return round(max(0.0, min(5.0, float(audit_score))) / 5.0, 3)


def _guidance_signal(guidance_needed: Optional[float]) -> float:
    """Invert guidance_needed: 0.0 correction = 1.0 confidence."""
    if guidance_needed is None:
        return 0.5
    return round(1.0 - max(0.0, min(1.0, float(guidance_needed))), 3)


# ── Ollama real logprob extraction ────────────────────────────────────────────

def extract_ollama_logprob_confidence(logprobs: List[float]) -> float:
    """
    Convert Ollama token logprobs to a confidence score (0–1).

    Ollama returns log probabilities per token (negative floats; 0 = certain).
    We use the mean of the top-50% highest logprobs (avoiding extreme outliers
    from rare tokens) converted to probability space.

    Usage: pass `raw_response["logprobs"]` from Ollama's /api/generate response.
    """
    if not logprobs:
        return 0.5
    # Convert log-probs to probabilities, ignore -inf (impossible tokens)
    probs = [math.exp(lp) for lp in logprobs if lp > -20.0]
    if not probs:
        return 0.5
    # Use median to avoid being dragged down by rare-token outliers
    probs.sort(reverse=True)
    top_half = probs[: max(1, len(probs) // 2)]
    return round(sum(top_half) / len(top_half), 4)


# ── Main scorer ───────────────────────────────────────────────────────────────

def score_response_confidence(
    row: Dict[str, Any],
    *,
    ollama_logprobs: Optional[List[float]] = None,
) -> float:
    """
    Derive a response confidence score (0.0–1.0) from an interaction_log row.

    For Ollama responses where `ollama_logprobs` is provided, uses real token
    logprob probabilities.  For all other providers (GPT/WHAM, Claude, etc.)
    uses a calibrated proxy derived from reasoning depth, response calibration,
    audit score, and guidance signal.

    Args:
        row: Dict from interaction_log (any columns may be None).
        ollama_logprobs: Optional list of token log-probs from Ollama.

    Returns:
        float in [0.0, 1.0]
    """
    provider = (row.get("actual_provider") or row.get("selected_provider") or "").lower()
    is_ollama = "ollama" in provider or "qwen" in provider

    # ── Ollama: real logprobs if available ────────────────────────────────────
    if is_ollama and ollama_logprobs:
        base = extract_ollama_logprob_confidence(ollama_logprobs)
        # Blend with guidance signal (human feedback overrides model confidence)
        guidance = _guidance_signal(row.get("guidance_needed"))
        return round(0.70 * base + 0.30 * guidance, 4)

    # ── All other providers: calibrated proxy ─────────────────────────────────
    r_depth    = _reasoning_depth(row.get("thinking_content"))
    r_calib    = _response_calibration(
        row.get("raw_input"),
        row.get("raw_output"),
        float(row.get("complexity_score") or 0.3),
    )
    r_audit    = _audit_signal(row.get("audit_score"))
    r_guidance = _guidance_signal(row.get("guidance_needed"))

    if r_audit is not None:
        confidence = (
            _W_REASONING   * r_depth
            + _W_CALIBRATION * r_calib
            + _W_AUDIT       * r_audit
            + _W_GUIDANCE    * r_guidance
        )
    else:
        # No audit score yet — redistribute audit weight
        w_r = _W_REASONING   + _W_AUDIT * 0.4
        w_c = _W_CALIBRATION + _W_AUDIT * 0.3
        w_g = _W_GUIDANCE    + _W_AUDIT * 0.3
        confidence = w_r * r_depth + w_c * r_calib + w_g * r_guidance

    return round(max(0.0, min(1.0, confidence)), 4)


# ── Batch scorer (for use in auditor + harvest runner) ────────────────────────

def score_batch(rows: List[Dict[str, Any]]) -> List[float]:
    """Score a batch of interaction_log rows. Returns parallel list of scores."""
    return [score_response_confidence(r) for r in rows]


# ── Domain profile builder (for buddy level seeding) ─────────────────────────

def build_domain_confidence_profile(
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build a domain-weighted confidence profile from interaction_log rows.

    Used at first install to seed the buddy's starting level:
      - Groups interactions by domain
      - Computes avg confidence per domain
      - Returns starter XP grant + primary domains + complexity fingerprint

    Returns:
        {
            "starter_xp": int,
            "primary_domains": List[str],   # top 3 domains by pair count
            "avg_confidence": float,         # overall avg across all domains
            "domain_breakdown": {domain: {count, avg_conf, complexity_mult}},
        }
    """
    if not rows:
        return {
            "starter_xp": 0,
            "primary_domains": [],
            "avg_confidence": 0.0,
            "domain_breakdown": {},
        }

    domain_buckets: Dict[str, List[float]] = {}
    for row in rows:
        domain = row.get("domain") or "default"
        conf = score_response_confidence(row)
        domain_buckets.setdefault(domain, []).append(conf)

    starter_xp = 0
    breakdown: Dict[str, Any] = {}
    all_confs: List[float] = []

    for domain, confs in domain_buckets.items():
        avg_conf = sum(confs) / len(confs)
        mult = DOMAIN_COMPLEXITY.get(domain, 1.0)
        xp_contribution = int(avg_conf * mult * len(confs) * XP_PER_CONFIDENCE_PAIR)
        starter_xp += xp_contribution
        breakdown[domain] = {
            "count": len(confs),
            "avg_confidence": round(avg_conf, 3),
            "complexity_mult": mult,
            "xp_contribution": xp_contribution,
        }
        all_confs.extend(confs)

    # Sort domains by pair count (primary = most frequent)
    primary_domains = sorted(domain_buckets.keys(), key=lambda d: len(domain_buckets[d]), reverse=True)[:3]
    overall_avg = sum(all_confs) / len(all_confs) if all_confs else 0.0

    logger.info(
        "Domain confidence profile: %d interactions, starter_xp=%d, primary=%s, avg_conf=%.3f",
        len(rows), starter_xp, primary_domains, overall_avg,
    )

    return {
        "starter_xp": starter_xp,
        "primary_domains": primary_domains,
        "avg_confidence": round(overall_avg, 3),
        "domain_breakdown": breakdown,
    }
