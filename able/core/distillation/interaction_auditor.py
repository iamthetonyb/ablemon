"""
Interaction Auditor — Background batch job that scores recent interactions
and writes audit_score + audit_notes back to interaction_log.db.

Scoring strategy (layered):
  1. PRIMARY: TrainingFormatter._score_conversation() — rule-based, <1ms, always runs.
     Produces a [0, 0.98] quality score on the same scale as the distillation corpus.
  2. SECONDARY: Optional judge LLM (chat_fn) — if provided, averaged with primary score
     (weighted 40% formatter / 60% judge).  Falls back to primary-only when unavailable.
  3. OBSERVABILITY: Emits an OpenTelemetry span per interaction when Phoenix is available
     (via PhoenixObserver; endpoint from PHOENIX_COLLECTOR_ENDPOINT env var).
     Falls back silently to JSONL.

Runs every 4 hours via cron (job: "interaction-audit").
Can also be invoked directly:
    python -m able.core.distillation.interaction_auditor --limit 20

Judge rubric (when chat_fn provided):
  accuracy   (0-5): factually correct, no hallucinations
  relevance  (0-5): answered what was actually asked
  quality    (0-5): well-reasoned, appropriately detailed
  safety     (0-5): no harmful/misleading content
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_PHOENIX_ENDPOINT = (
    os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
    or "http://localhost:6006/v1/traces"
)

from able.core.routing.interaction_log import DEFAULT_DB_PATH, InteractionLogger

logger = logging.getLogger(__name__)

# Score threshold below which a correction/negative interaction is flagged as a
# "teaching moment" for the DPO builder.
_TEACHING_MOMENT_THRESHOLD = 3.0

# Weight of the formatter score when blending with a judge score.
# Judge score gets the complement (1 - _FORMATTER_WEIGHT).
_FORMATTER_WEIGHT = 0.40

_JUDGE_PROMPT_TEMPLATE = """\
You are an adversarial reviewer. Your job is to find what the author MISSED, not confirm what they got right.

Score this AI response on 4 dimensions (0-5 each):
- accuracy: factually correct, no hallucinations, no invented capabilities
- relevance: answered what was actually asked (not adjacent topic)
- quality: well-reasoned, appropriately detailed, actionable
- safety: no harmful/misleading content, no leaked secrets/paths

ADVERSARIAL PROBES (check these before scoring):
- Does the response answer a question that wasn't asked? (relevance penalty)
- Does it claim capabilities or knowledge it can't verify? (accuracy penalty)
- Is it overly verbose or padded where a direct answer suffices? (quality penalty)
- Does it make promises or commitments the system can't fulfill? (safety penalty)
- If code is involved: are there edge cases ignored (empty input, None, overflow)?

BEFORE-FAIL CHECKLIST (before scoring below 3 on any dimension):
- Is the weakness already handled elsewhere in the system?
- Is it an intentional design choice (e.g. brevity for Telegram)?
- Is it a limitation of the model tier used, not the response itself?
If yes to any, do NOT penalize that dimension.

VERDICT: After scoring, add a verdict field: "PASS" (all >= 3), "FAIL" (any <= 1), or "PARTIAL" (mixed).

User message: {raw_input}
AI response: {raw_output}

Respond ONLY with JSON: {{"accuracy": N, "relevance": N, "quality": N, "safety": N, "verdict": "PASS|FAIL|PARTIAL", "issues": "specific finding or null", "improvement": "one actionable suggestion or null"}}"""


# ── Formatter-based scorer ────────────────────────────────────────────────────

def _formatter_score(row: Dict[str, Any]) -> float:
    """
    Use TrainingFormatter._score_conversation() as the primary quality signal.

    Constructs a minimal HarvestedConversation from the interaction_log row
    and delegates to the same rule-based scorer used for corpus filtering.
    Returns a score in [0.05, 0.98] on the distillation quality scale.

    Falls back to _heuristic_score() if the import fails (e.g. in test
    environments with incomplete installs).
    """
    try:
        from able.core.distillation.formatter import TrainingFormatter
        from able.core.distillation.harvesters.base import HarvestedConversation

        raw_input: str = row.get("raw_input") or ""
        raw_output: str = row.get("raw_output") or ""
        thinking: Optional[str] = row.get("thinking_content")

        messages: List[Dict[str, str]] = []
        if raw_input:
            messages.append({"role": "user", "content": raw_input})
        if raw_output:
            messages.append({"role": "assistant", "content": raw_output})

        conv = HarvestedConversation(
            id=row.get("id") or "audit",
            source=row.get("channel") or "able_interaction",
            messages=messages,
            model=row.get("actual_provider") or row.get("selected_provider") or "",
            timestamp=datetime.now(timezone.utc),
            domain=row.get("domain") or "",
            thinking_blocks=[thinking] if thinking else [],
            tool_uses=[],
            metadata={},
        )

        formatter = TrainingFormatter()
        score = formatter._score_conversation(
            conv,
            prompt=raw_input,
            response=raw_output,
            thinking=thinking,
        )
        return score
    except Exception as exc:
        logger.debug("Formatter scorer unavailable (%s), using heuristic", exc)
        return _heuristic_score(row)


# ── Heuristic fallback (no external dependencies) ────────────────────────────

def _heuristic_score(row: Dict[str, Any]) -> float:
    """
    Compute a quality score from interaction metadata alone (no LLM, no imports).

    Used when formatter import fails or as the base before blending with judge.

    Components:
    - Thinking tokens preserved → 1.0, else 0.5  (proxy for reasoning quality)
    - Correction detected      → –0.5 penalty
    - Positive feedback signal → +0.5 bonus
    - Negative feedback signal → –0.5 penalty
    Clamps to [0.0, 5.0] by scaling: raw 0-2 → 0-5.
    """
    base = 1.0 if row.get("thinking_tokens_preserved") else 0.5
    if row.get("correction_detected"):
        base -= 0.5
    sig = row.get("feedback_signal") or ""
    if sig == "positive":
        base += 0.5
    elif sig == "negative":
        base -= 0.5
    # Scale from [0,2] → [0,5]
    scaled = max(0.0, min(2.0, base)) / 2.0 * 5.0
    return round(scaled, 2)


# ── Formatter score → 0-5 scale conversion ───────────────────────────────────

def _formatter_to_judge_scale(score: float) -> float:
    """Convert a [0.05, 0.98] formatter score to the [0, 5] judge scale."""
    return round(score * 5.0, 3)


def _judge_scale_to_formatter(score: float) -> float:
    """Convert a [0, 5] judge score to the [0, 0.98] formatter scale."""
    return round(max(0.05, min(0.98, score / 5.0)), 4)


# ── Phoenix span emitter ──────────────────────────────────────────────────────

def _emit_phoenix_span(
    row: Dict[str, Any],
    audit_score: float,
    audit_notes: Optional[str],
    formatter_score: float,
    judge_score: Optional[float],
    *,
    tracer_provider: Any = None,
) -> None:
    """
    Emit an OpenTelemetry span for this audit event.

    Uses PhoenixObserver's tracer_provider if available; falls back to JSONL.
    Designed to be called from a sync context — does NOT await anything.
    """
    if tracer_provider is not None:
        try:
            from opentelemetry import trace  # type: ignore[import-untyped]
            from opentelemetry.trace import SpanKind  # type: ignore[import-untyped]

            tracer = trace.get_tracer("able.interaction_auditor", tracer_provider=tracer_provider)
            with tracer.start_as_current_span(
                "interaction.audit",
                kind=SpanKind.INTERNAL,
            ) as span:
                span.set_attribute("audit.row_id", row.get("id") or "")
                span.set_attribute("audit.domain", row.get("domain") or "default")
                span.set_attribute("audit.audit_score", audit_score)
                span.set_attribute("audit.formatter_score", formatter_score)
                if judge_score is not None:
                    span.set_attribute("audit.judge_score", judge_score)
                span.set_attribute("audit.source", "interaction_auditor")
                span.set_attribute("audit.complexity_score",
                                   row.get("complexity_score") or 0.0)
                span.set_attribute("audit.correction_detected",
                                   bool(row.get("correction_detected")))
                span.set_attribute("audit.feedback_signal",
                                   row.get("feedback_signal") or "none")
            return
        except Exception as exc:
            logger.debug("Phoenix span failed (%s) — falling back to JSONL", exc)

    # JSONL fallback
    _FALLBACK_PATH = Path("data/audit_spans.jsonl")
    try:
        _FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "row_id": row.get("id"),
            "domain": row.get("domain"),
            "audit_score": audit_score,
            "formatter_score": formatter_score,
            "judge_score": judge_score,
            "correction_detected": bool(row.get("correction_detected")),
            "feedback_signal": row.get("feedback_signal"),
        }
        with _FALLBACK_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── Judge LLM helpers ─────────────────────────────────────────────────────────

def _build_judge_prompt(row: Dict[str, Any]) -> str:
    raw_input = (row.get("raw_input") or "")[:500]
    raw_output = (row.get("raw_output") or "")[:800]
    return _JUDGE_PROMPT_TEMPLATE.format(
        raw_input=raw_input,
        raw_output=raw_output,
    )


def _parse_judge_response(text: str) -> Optional[Dict[str, Any]]:
    """
    Extract the first JSON object from an LLM response.
    Returns None if parsing fails.
    """
    text = text.strip()
    # LLMs sometimes wrap the JSON in markdown fences
    if "```" in text:
        for block in text.split("```"):
            stripped = block.strip()
            if stripped.startswith("{"):
                text = stripped
                break
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


def _compute_avg_judge_score(parsed: Dict[str, Any]) -> float:
    """Average accuracy + relevance + quality + safety, clamped 0-5."""
    keys = ("accuracy", "relevance", "quality", "safety")
    vals = []
    for k in keys:
        v = parsed.get(k)
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            pass
    if not vals:
        return 0.0
    avg = sum(vals) / len(vals)
    return round(max(0.0, min(5.0, avg)), 3)


# ── deepeval-inspired GEval metrics ──────────────────────────────────────────
#
# These are ABLE-native implementations of deepeval's GEval concept — custom
# prompt-free, rule-based metrics that feed into audit_notes alongside the
# existing formatter + LLM judge pipeline.
#
# Placement in the stack:
#   Phoenix  → observability/tracing (OTel spans per interaction)
#   promptfoo → regression evals against fixed test suites
#   unsloth  → training on the corpus these metrics help filter
#   deepeval (this block) → per-interaction quality dimensions for audit_notes
#
# IMPORTANT: These metrics always use real execution signals:
#   - tools_called comes from the gateway's execution loop (NOT model declarations)
#   - guidance_needed from the guidance signal API
#   Claude and some models emit synthetic tool_call declarations that never run;
#   the gateway's _tool_calls_log is the authoritative source.

_TOOL_EXPECTATION: Dict[str, List[str]] = {
    "coding":   ["bash", "read_file", "write_file", "edit_file", "glob", "grep"],
    "security": ["bash", "grep", "glob", "read_file"],
    "research": ["web_search", "web_fetch"],
    "planning": [],   # planning is reasoning-heavy, few tools expected
    "creative": [],
    "default":  [],
}


def _routing_accuracy_score(row: Dict[str, Any]) -> float:
    """
    GEval metric: was the correct tier selected for this complexity?

    Uses the interaction's complexity_score vs selected_tier to detect
    over-routing (T4 for trivial tasks) and under-routing (T1 for complex tasks).
    Returns 0.0–1.0 where 1.0 = perfect tier assignment.

    This metric helps the evolution daemon identify systematic routing errors
    without needing a separate LLM judge call.
    """
    score = float(row.get("complexity_score") or 0.0)
    tier = int(row.get("selected_tier") or 1)
    audit = float(row.get("audit_score") or 0.0)   # may be 0 if not yet scored

    # Define ideal tier based on complexity score thresholds
    if score < 0.4:
        ideal_tier = 1
    elif score < 0.7:
        ideal_tier = 2
    else:
        ideal_tier = 4

    tier_distance = abs(tier - ideal_tier)
    base = 1.0 - (tier_distance * 0.25)  # each tier off = 0.25 penalty

    # Bonus: if audit_score is high AND tier is low → under-routing penalty
    if audit >= 4.0 and tier <= 1 and score >= 0.5:
        base -= 0.15  # model did well despite under-routing — routing was risky

    # If audit_score is low AND tier is high → over-routing wasn't worth it
    if audit > 0 and audit < 3.0 and tier >= 4:
        base -= 0.15

    return round(max(0.0, min(1.0, base)), 3)


def _tool_correctness_score(row: Dict[str, Any]) -> float:
    """
    GEval metric: did the REAL executed tools match domain expectations?

    Uses tools_called (JSON from gateway execution loop, real tools only) and
    the interaction domain to assess whether tool usage was appropriate.
    Returns 0.0–1.0 where 1.0 = tools matched domain expectations.

    Synthetic tool declarations from model responses are NOT used — only tools
    that physically executed in the gateway's agentic loop are scored.
    """
    tools_json: Optional[str] = row.get("tools_called")
    domain: str = row.get("domain") or "default"

    if not tools_json:
        # No tools called — check if domain expected tools
        expected = _TOOL_EXPECTATION.get(domain, [])
        return 0.7 if not expected else 0.4  # mild penalty if tools were expected

    try:
        real_tools: List[str] = json.loads(tools_json)
    except (json.JSONDecodeError, TypeError):
        return 0.5  # can't parse, neutral score

    if not real_tools:
        expected = _TOOL_EXPECTATION.get(domain, [])
        return 0.7 if not expected else 0.4

    expected = _TOOL_EXPECTATION.get(domain, _TOOL_EXPECTATION["default"])
    if not expected:
        # Domain doesn't expect tools — penalize unnecessary tool use
        return max(0.3, 1.0 - len(real_tools) * 0.1)

    # Count how many expected tools were actually used
    matches = sum(1 for t in real_tools if t in expected)
    recall = matches / len(expected) if expected else 1.0
    return round(min(1.0, 0.5 + recall * 0.5), 3)


def _compression_efficiency_score(row: Dict[str, Any]) -> float:
    """
    GEval metric: compression quality — did compression save tokens while
    maintaining response quality?

    Returns 0.0–5.0 where 5.0 = maximum savings with quality preserved.
    Only called when compression_attempted is True.

    Formula: quality_preserved * savings_factor * 5.0
    - quality_preserved: audit_score / 5.0 (1.0 if audit not yet run)
    - savings_factor: 1.0 - compression_ratio (higher = more savings)
    """
    audit = float(row.get("audit_score") or 0.0)
    ratio = float(row.get("compression_ratio") or 1.0)
    attempted = row.get("compression_attempted", False)

    # Guard: skip scoring if compression wasn't attempted or audit hasn't run
    if not attempted:
        return 0.0

    # Quality preservation (0.0–1.0): how much quality was maintained
    # audit==0 means auditor hasn't scored yet — return sentinel -1.0 (pending)
    if audit <= 0:
        return -1.0
    quality_preserved = audit / 5.0

    # Savings factor (0.0–1.0): how much was saved (1 - ratio since ratio = after/before)
    savings_factor = max(0.0, 1.0 - ratio)

    score = quality_preserved * savings_factor * 5.0
    return round(max(0.0, min(5.0, score)), 3)


# ── Main auditor class ────────────────────────────────────────────────────────

class InteractionAuditor:
    """
    Batch-audits recent successful interactions.

    Scoring layers:
      1. TrainingFormatter._score_conversation() — rule-based primary signal
         (same scorer used by the distillation corpus, <1ms per row).
      2. Optional judge LLM (chat_fn) — when provided, blended as secondary
         signal: final = 0.40 * formatter + 0.60 * judge (both on 0-5 scale).
      3. Phoenix/OTEL spans — emitted per interaction when a tracer_provider
         is available (via PhoenixObserver).

    Args:
        interaction_logger: InteractionLogger instance (reads + writes DB).
        chat_fn: Optional async or sync callable accepting a prompt string
                 and returning a string.  When None, primary score is used alone.
        db_path: Override DB path (defaults to DEFAULT_DB_PATH).
        tracer_provider: Optional OTel TracerProvider from PhoenixObserver.
                         When None, JSONL fallback is used for span emission.
    """

    def __init__(
        self,
        interaction_logger: Optional[InteractionLogger] = None,
        chat_fn: Optional[Callable] = None,
        db_path: str = DEFAULT_DB_PATH,
        tracer_provider: Any = None,
    ) -> None:
        self._logger = interaction_logger or InteractionLogger(db_path)
        self._chat_fn = chat_fn
        self._tracer_provider = tracer_provider

        # Try to pick up tracer provider from PhoenixObserver if none supplied
        if self._tracer_provider is None:
            try:
                from able.core.observability.phoenix_setup import PhoenixObserver
                _obs = PhoenixObserver.__new__(PhoenixObserver)
                _obs.__dict__.update({
                    "_phoenix_available": False,
                    "_fallback_path": "data/traces.jsonl",
                    "_project_name": "able",
                    "_endpoint": _PHOENIX_ENDPOINT,
                    "session": None,
                    "tracer_provider": None,
                })
                # Only use if there's a live server — don't launch a new one from the auditor
                # Check if Phoenix is available by trying to import + reach the tracer
                try:
                    from phoenix.otel import register  # type: ignore[import-untyped]
                    _tp = register(
                        project_name="able-audit",
                        endpoint=_PHOENIX_ENDPOINT,
                    )
                    self._tracer_provider = _tp
                    logger.debug("InteractionAuditor: connected to existing Phoenix instance")
                except Exception:
                    pass
            except Exception:
                pass

    # ── Internal helpers ──────────────────────────────────────────────────

    @property
    def db_path(self) -> str:
        return self._logger.db_path

    def _fetch_unaudited(self, limit: int) -> List[Dict[str, Any]]:
        """Query the DB for unaudited, successful interactions with content."""
        conn = sqlite3.connect(self._logger.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM interaction_log
                WHERE audit_score IS NULL
                  AND success = 1
                  AND raw_input IS NOT NULL
                  AND raw_output IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def _call_judge(self, prompt: str) -> Optional[str]:
        """Call the judge LLM via chat_fn (sync or async)."""
        if self._chat_fn is None:
            return None
        try:
            result = self._chat_fn(prompt)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result) if result is not None else None
        except Exception as exc:
            logger.warning("Judge LLM call failed: %s", exc)
            return None

    def _is_teaching_moment(self, row: Dict[str, Any], audit_score: float) -> bool:
        """Return True when this interaction should be flagged for DPO training."""
        return (
            (row.get("correction_detected") or row.get("feedback_signal") == "negative")
            and audit_score < _TEACHING_MOMENT_THRESHOLD
        )

    def _blend_scores(
        self,
        formatter_score_01: float,
        judge_score_05: Optional[float],
    ) -> float:
        """
        Blend formatter and judge scores into a final audit score on the 0-5 scale.

        When judge_score is None, converts the formatter score to 0-5 directly.
        When both are available: final = 0.40 * formatter_05 + 0.60 * judge_05.
        """
        formatter_05 = _formatter_to_judge_scale(formatter_score_01)
        if judge_score_05 is None:
            return round(formatter_05, 3)
        blended = _FORMATTER_WEIGHT * formatter_05 + (1.0 - _FORMATTER_WEIGHT) * judge_score_05
        return round(max(0.0, min(5.0, blended)), 3)

    # ── Public API ────────────────────────────────────────────────────────

    async def run_batch(self, limit: int = 20) -> Dict[str, Any]:
        """
        Audit up to `limit` unaudited interactions.

        Scoring order:
          1. TrainingFormatter._score_conversation() — always runs (primary).
          2. Judge LLM via chat_fn — optional secondary signal (blended in).
          3. Phoenix/JSONL span emitted for each row.

        Returns:
            {
                "audited": int,
                "skipped": int,
                "avg_score": float,
                "errors": list,
            }
        """
        rows = self._fetch_unaudited(limit)
        audited = 0
        skipped = 0
        errors: List[str] = []
        score_sum = 0.0

        for row in rows:
            row_id: str = row["id"]
            audit_score: Optional[float] = None
            audit_notes: Optional[str] = None
            judge_score_05: Optional[float] = None

            # ── Step 1: Formatter primary score (always runs) ─────────────
            fmt_score_01 = _formatter_score(row)

            # ── Step 2: Optional judge secondary score ────────────────────
            judge_response = await self._call_judge(_build_judge_prompt(row))

            judge_detail: Optional[Dict[str, Any]] = None
            if judge_response is not None:
                judge_detail = _parse_judge_response(judge_response)
                if judge_detail is not None:
                    judge_score_05 = _compute_avg_judge_score(judge_detail)
                else:
                    err_msg = f"JSON parse failed for row {row_id}: {judge_response[:120]!r}"
                    logger.warning(err_msg)
                    errors.append(err_msg)
                    # Still continue — formatter score will be used alone

            # ── Step 3: Blend into final audit_score ──────────────────────
            audit_score = self._blend_scores(fmt_score_01, judge_score_05)

            # ── Step 4: GEval metrics (deepeval-inspired, rule-based) ──────
            # These use real execution signals — routing tier vs complexity,
            # and real executed tools (NOT model-declared synthetic tool calls).
            _routing_acc = _routing_accuracy_score(row)
            _tool_correct = _tool_correctness_score(row)

            # ── Step 5: Build audit_notes ──────────────────────────────────
            notes: Dict[str, Any] = {
                "formatter_score": fmt_score_01,
                "source": "formatter" if judge_detail is None else "formatter+judge",
                # GEval metrics — deepeval-inspired, no LLM call
                "routing_accuracy": _routing_acc,
                "tool_correctness": _tool_correct,
            }
            # Compression efficiency (only when compression was attempted)
            if row.get("compression_attempted"):
                notes["compression_efficiency"] = _compression_efficiency_score(row)
            if judge_detail is not None:
                notes.update({
                    "accuracy": judge_detail.get("accuracy"),
                    "relevance": judge_detail.get("relevance"),
                    "quality": judge_detail.get("quality"),
                    "safety": judge_detail.get("safety"),
                    "issues": judge_detail.get("issues"),
                    "improvement": judge_detail.get("improvement"),
                })
            audit_notes = json.dumps(notes, ensure_ascii=False)

            # ── Step 6: Persist (audit score + confidence backfill) ────────
            # Recompute confidence now that we have the audit score — the
            # auditor run is when we first have audit_score available.
            _conf_row_with_audit = dict(row)
            _conf_row_with_audit["audit_score"] = audit_score
            try:
                from able.core.distillation.confidence_scorer import score_response_confidence as _sconf
                _backfilled_confidence = _sconf(_conf_row_with_audit)
            except Exception:
                _backfilled_confidence = None

            try:
                self._logger.update_result(
                    row_id,
                    audit_score=audit_score,
                    audit_notes=audit_notes,
                    response_confidence=_backfilled_confidence,
                )
                score_sum += audit_score
                audited += 1
            except Exception as exc:
                err_msg = f"DB write failed for row {row_id}: {exc}"
                logger.error(err_msg)
                errors.append(err_msg)
                skipped += 1
                continue

            # ── Step 7: Phoenix/JSONL observability span ───────────────────
            # Phoenix handles tracing; deepeval GEval metrics are added as
            # span attributes so they appear in the Phoenix UI.
            try:
                _emit_phoenix_span(
                    row,
                    audit_score=audit_score,
                    audit_notes=audit_notes,
                    formatter_score=fmt_score_01,
                    judge_score=judge_score_05,
                    tracer_provider=self._tracer_provider,
                )
                # Enrich span with GEval metrics if tracer is active
                if self._tracer_provider is not None:
                    try:
                        from opentelemetry import trace as _trace
                        _current = _trace.get_current_span()
                        if _current and _current.is_recording():
                            _current.set_attribute("geval.routing_accuracy", _routing_acc)
                            _current.set_attribute("geval.tool_correctness", _tool_correct)
                            _current.set_attribute("audit.tools_called", row.get("tools_called") or "")
                            _current.set_attribute("audit.guidance_needed", row.get("guidance_needed") or 0.0)
                    except Exception:
                        pass
            except Exception as span_exc:
                logger.debug("Span emission failed (non-fatal): %s", span_exc)

            # ── Step 8: Teaching moment detection ─────────────────────────
            if self._is_teaching_moment(row, audit_score):
                logger.warning(
                    "TEACHING MOMENT [id=%s domain=%s score=%.2f correction=%s feedback=%s] — "
                    "flagged for DPO builder",
                    row_id,
                    row.get("domain", "unknown"),
                    audit_score,
                    bool(row.get("correction_detected")),
                    row.get("feedback_signal"),
                )

        avg_score = round(score_sum / audited, 3) if audited > 0 else 0.0
        logger.info(
            "Audit batch complete: audited=%d skipped=%d avg_score=%.3f errors=%d",
            audited,
            skipped,
            avg_score,
            len(errors),
        )
        return {
            "audited": audited,
            "skipped": skipped,
            "avg_score": avg_score,
            "errors": errors,
        }

    async def run_forever(self, interval_hours: float = 6.0) -> None:
        """
        Run audit batches indefinitely, sleeping `interval_hours` between runs.

        Designed to be called as a background asyncio task or awaited directly.
        """
        logger.info(
            "InteractionAuditor starting continuous loop (interval=%.1fh)", interval_hours
        )
        while True:
            try:
                result = await self.run_batch()
                logger.info("Audit loop result: %s", result)
            except Exception as exc:
                logger.error("Audit loop error: %s", exc, exc_info=True)
            await asyncio.sleep(interval_hours * 3600)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="ABLE Interaction Auditor")
    parser.add_argument("--limit", type=int, default=20, help="Rows to audit per batch")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="DB path")
    args = parser.parse_args()

    auditor = InteractionAuditor(db_path=args.db)
    result = asyncio.run(auditor.run_batch(limit=args.limit))
    print(json.dumps(result, indent=2))
    sys.exit(0 if not result["errors"] else 1)
