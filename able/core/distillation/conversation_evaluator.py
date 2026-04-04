"""
ConversationChainEvaluator — holistic multi-turn conversation quality evaluation.

Inspired by deepeval's ConversationalTestCase approach:
  - Groups interaction_log turns by session_id into ConversationSession objects
  - Evaluates full sessions as a unit (not turn-by-turn in isolation)
  - Sliding-window context: a correction on turn 5 uses turns 1-4 as setup
  - Produces ConversationEvalResult with rich metrics for DPO pair building

Key metrics per session:
  win_rate         (0–1): proportion of turns with positive outcome / no correction
  guidance_ratio   (0–1): turns that needed user guidance / total turns (lower = better)
  reasoning_depth  (0–1): avg quality of thinking_content traces across session
  coherence_score  (0–1): estimated via audit_score consistency across turns
  session_quality  (0–1): weighted composite of the above

DPO pair output:
  For each session with mixed quality, produces a ConversationDPOPair:
    prompt   = full conversation history up to the divergence point
    chosen   = the good response (high audit_score, no correction)
    rejected = the response that needed correction (with context)

Usage:
    evaluator = ConversationChainEvaluator()
    results   = evaluator.evaluate_recent(since_hours=24)
    pairs     = evaluator.build_conversation_dpo_pairs(results)
    count     = evaluator.export_pairs(output_path="data/distillation_conv_dpo.jsonl")

Runs via cron (job: "conversation-eval") every 4 hours, offset from interaction-audit.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from able.core.routing.interaction_log import DEFAULT_DB_PATH, InteractionLogger

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Minimum turns for a session to be worth evaluating as a chain.
_MIN_SESSION_TURNS = 2

# Audit-score threshold for a turn to be considered a "win" (no guidance needed).
_WIN_SCORE_FLOOR = 3.5

# guidance_needed threshold above which a turn is a "loss" (needed correction).
_LOSS_GUIDANCE_THRESHOLD = 0.5

# Weight breakdown for composite session_quality:
_W_WIN_RATE       = 0.35
_W_REASONING      = 0.30
_W_COHERENCE      = 0.20
_W_GUIDANCE       = 0.15   # (1 - guidance_ratio) contributes here


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ConversationTurn:
    """Single turn extracted from interaction_log."""
    record_id:        str
    depth:            int            # 0-indexed position in session
    role_input:       str            # user message
    role_output:      str            # AI response
    thinking:         Optional[str]  # reasoning trace
    tools_called:     List[str]      # tools fired during this response
    audit_score:      float          # 0–5, or 0.0 if not audited yet
    guidance_needed:  float          # 0.0–1.0 guidance richness signal
    feedback_signal:  Optional[str]  # positive | negative | correction
    correction_text:  Optional[str]  # what user said when correcting
    domain:           str


@dataclass
class ConversationSession:
    """Full reconstructed session from multiple turns."""
    session_id:   str
    turns:        List[ConversationTurn]
    source:       str   # channel (cli/telegram/discord)
    tenant_id:    str


@dataclass
class ConversationEvalResult:
    """Evaluation result for a single session."""
    session_id:       str
    turn_count:       int
    win_rate:         float   # proportion of turns that landed right first try
    guidance_ratio:   float   # proportion of turns needing user correction
    reasoning_depth:  float   # avg thinking trace quality (0–1)
    coherence_score:  float   # audit_score consistency across turns
    session_quality:  float   # weighted composite
    has_dpo_signal:   bool    # True if session has both good + bad turns
    guidance_moments: List[int]   # depth indices where guidance was needed
    win_turns:        List[int]   # depth indices of first-try wins


@dataclass
class ConversationDPOPair:
    """DPO training pair built from a full conversation chain."""
    session_id:            str
    prompt_history:        str   # ChatML multi-turn prompt (context up to divergence)
    chosen_response:       str   # the good turn's response
    chosen_thinking:       Optional[str]
    chosen_turn_depth:     int
    rejected_response:     str   # the turn that needed correction
    rejected_thinking:     Optional[str]
    rejected_turn_depth:   int
    session_quality:       float
    domain:                str
    guidance_correction:   Optional[str]   # what user said when correcting


# ── Core evaluator ────────────────────────────────────────────────────────────

class ConversationChainEvaluator:
    """Evaluates interaction_log sessions as full conversation chains.

    Unlike the turn-level InteractionAuditor, this evaluator:
      - Groups turns by session_id to reconstruct the full dialogue
      - Computes session-level quality metrics (win_rate, guidance_ratio, etc.)
      - Identifies "guidance moments" — turns where the AI needed human help
      - Builds DPO pairs from conversation chains with mixed quality
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        il = InteractionLogger(db_path)
        self._db_path = il.db_path
        self._il = il

    # ── Session reconstruction ────────────────────────────────────────────────

    def _reconstruct_session(
        self, session_id: str
    ) -> Optional[ConversationSession]:
        """Load all turns for a session from interaction_log."""
        rows = self._il.get_session_turns(session_id)
        if len(rows) < _MIN_SESSION_TURNS:
            return None

        turns: List[ConversationTurn] = []
        for i, row in enumerate(rows):
            tools_raw = row.get("tools_called") or "[]"
            try:
                tools = json.loads(tools_raw) if isinstance(tools_raw, str) else []
            except (json.JSONDecodeError, TypeError):
                tools = []

            turns.append(ConversationTurn(
                record_id=row.get("id", ""),
                depth=row.get("conversation_depth", i),
                role_input=row.get("raw_input") or "",
                role_output=row.get("raw_output") or "",
                thinking=row.get("thinking_content"),
                tools_called=tools,
                audit_score=float(row.get("audit_score") or 0.0),
                guidance_needed=float(row.get("guidance_needed") or 0.0),
                feedback_signal=row.get("feedback_signal"),
                correction_text=row.get("feedback_text"),
                domain=row.get("domain") or "default",
            ))

        if not turns:
            return None

        # Infer source/tenant from first turn's channel/tenant_id
        first_row = rows[0]
        return ConversationSession(
            session_id=session_id,
            turns=turns,
            source=first_row.get("channel") or "cli",
            tenant_id=first_row.get("tenant_id") or "default",
        )

    # ── Metrics ───────────────────────────────────────────────────────────────

    def _reasoning_depth_score(self, thinking: Optional[str]) -> float:
        """Estimate reasoning quality from a thinking trace (0–1).

        Uses simple heuristics — length and presence of structure markers.
        The InteractionAuditor's LLM judge provides the authoritative score
        for individual turns; this is a fast proxy for session-level aggregation.
        """
        if not thinking:
            return 0.0
        text = thinking.strip()
        length_score = min(len(text) / 2000, 1.0)   # 2000 chars ≈ good depth
        structure_bonus = 0.0
        for marker in ("therefore", "because", "however", "first,", "step", "consider"):
            if marker.lower() in text.lower():
                structure_bonus += 0.05
        return min(length_score + structure_bonus, 1.0)

    def _evaluate_session(self, session: ConversationSession) -> ConversationEvalResult:
        """Compute quality metrics for a full conversation session."""
        turns = session.turns
        n = len(turns)

        win_turns: List[int] = []
        guidance_moments: List[int] = []
        reasoning_scores: List[float] = []
        audit_scores: List[float] = []

        for t in turns:
            # Win: high audit score AND no guidance needed AND positive/no feedback
            is_win = (
                t.audit_score >= _WIN_SCORE_FLOOR
                and t.guidance_needed < _LOSS_GUIDANCE_THRESHOLD
                and t.feedback_signal != "negative"
                and not (t.feedback_signal == "correction")
            )
            if is_win:
                win_turns.append(t.depth)

            # Guidance moment: user had to step in
            needs_guidance = (
                t.guidance_needed >= _LOSS_GUIDANCE_THRESHOLD
                or t.feedback_signal in ("negative", "correction")
                or (t.correction_text and len(t.correction_text) > 10)
            )
            if needs_guidance:
                guidance_moments.append(t.depth)

            reasoning_scores.append(self._reasoning_depth_score(t.thinking))
            if t.audit_score > 0:
                audit_scores.append(t.audit_score / 5.0)   # normalise to 0–1

        win_rate       = len(win_turns) / n
        guidance_ratio = len(guidance_moments) / n
        reasoning_depth = statistics.mean(reasoning_scores) if reasoning_scores else 0.0
        coherence_score = (1.0 - statistics.stdev(audit_scores)
                           if len(audit_scores) >= 2 else
                           (audit_scores[0] if audit_scores else 0.5))
        coherence_score = max(0.0, min(1.0, coherence_score))

        session_quality = (
            _W_WIN_RATE     * win_rate
            + _W_REASONING  * reasoning_depth
            + _W_COHERENCE  * coherence_score
            + _W_GUIDANCE   * (1.0 - guidance_ratio)
        )

        has_dpo_signal = bool(win_turns) and bool(guidance_moments)

        return ConversationEvalResult(
            session_id=session.session_id,
            turn_count=n,
            win_rate=win_rate,
            guidance_ratio=guidance_ratio,
            reasoning_depth=reasoning_depth,
            coherence_score=coherence_score,
            session_quality=round(session_quality, 4),
            has_dpo_signal=has_dpo_signal,
            guidance_moments=guidance_moments,
            win_turns=win_turns,
        )

    # ── DPO pair construction ─────────────────────────────────────────────────

    def _build_chat_ml_history(
        self, turns: List[ConversationTurn], up_to_depth: int
    ) -> str:
        """Build a ChatML multi-turn prompt string up to (not including) up_to_depth."""
        parts: List[str] = []
        for t in turns:
            if t.depth >= up_to_depth:
                break
            if t.role_input:
                parts.append(f"<|im_start|>user\n{t.role_input}<|im_end|>")
            if t.role_output:
                parts.append(f"<|im_start|>assistant\n{t.role_output}<|im_end|>")
        parts.append("<|im_start|>user\n")   # open slot for the divergence prompt
        return "\n".join(parts)

    def _build_dpo_pair(
        self,
        session: ConversationSession,
        result: ConversationEvalResult,
    ) -> Optional[ConversationDPOPair]:
        """Build one DPO pair from a session with mixed quality.

        Strategy: find the first guidance moment (rejected turn) and the
        closest win turn in the same domain.  Use turns before the rejected
        turn as the conversation context (prompt_history).
        """
        turns_by_depth = {t.depth: t for t in session.turns}

        if not result.guidance_moments or not result.win_turns:
            return None

        # Pick the first guidance moment as the rejected sample
        rej_depth = result.guidance_moments[0]
        rej_turn  = turns_by_depth.get(rej_depth)
        if rej_turn is None or not rej_turn.role_output:
            return None

        # Pick the best win turn (highest audit_score) as the chosen sample
        win_turns_sorted = sorted(
            [turns_by_depth[d] for d in result.win_turns if d in turns_by_depth],
            key=lambda t: t.audit_score,
            reverse=True,
        )
        if not win_turns_sorted:
            return None
        chosen_turn = win_turns_sorted[0]

        # Conversation history up to the rejected turn (provides context)
        prompt_history = self._build_chat_ml_history(session.turns, rej_depth)
        # Append the user prompt at the divergence point
        if rej_turn.role_input:
            prompt_history = prompt_history.rstrip("\n<|im_start|>user\n")
            prompt_history += f"<|im_start|>user\n{rej_turn.role_input}<|im_end|>\n<|im_start|>assistant\n"

        return ConversationDPOPair(
            session_id=session.session_id,
            prompt_history=prompt_history,
            chosen_response=chosen_turn.role_output,
            chosen_thinking=chosen_turn.thinking,
            chosen_turn_depth=chosen_turn.depth,
            rejected_response=rej_turn.role_output,
            rejected_thinking=rej_turn.thinking,
            rejected_turn_depth=rej_depth,
            session_quality=result.session_quality,
            domain=rej_turn.domain or chosen_turn.domain,
            guidance_correction=rej_turn.correction_text,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate_recent(self, since_hours: int = 24) -> List[ConversationEvalResult]:
        """Evaluate all sessions with ≥2 turns from the last since_hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        session_ids = self._il.get_recent_sessions(
            since_iso=cutoff.isoformat(),
            min_turns=_MIN_SESSION_TURNS,
        )
        results: List[ConversationEvalResult] = []
        for sid in session_ids:
            session = self._reconstruct_session(sid)
            if session is None:
                continue
            try:
                result = self._evaluate_session(session)
                results.append(result)
            except Exception as e:
                logger.warning("Failed to evaluate session %s: %s", sid, e)
        logger.info(
            "ConversationChainEvaluator: %d sessions evaluated (last %dh)",
            len(results), since_hours,
        )
        return results

    def build_conversation_dpo_pairs(
        self, results: List[ConversationEvalResult]
    ) -> List[ConversationDPOPair]:
        """Build DPO pairs from sessions that have both wins and guidance moments."""
        pairs: List[ConversationDPOPair] = []
        for result in results:
            if not result.has_dpo_signal:
                continue
            session = self._reconstruct_session(result.session_id)
            if session is None:
                continue
            pair = self._build_dpo_pair(session, result)
            if pair is not None:
                pairs.append(pair)
        logger.info(
            "ConversationChainEvaluator: %d DPO pairs from %d sessions",
            len(pairs), len(results),
        )
        return pairs

    def export_pairs(
        self,
        output_path: str = "data/distillation_conv_dpo.jsonl",
        since_hours: int = 24,
    ) -> int:
        """Evaluate + build pairs + write to JSONL.  Returns count written."""
        results = self.evaluate_recent(since_hours=since_hours)
        pairs   = self.build_conversation_dpo_pairs(results)
        if not pairs:
            logger.info("ConversationChainEvaluator: no pairs to export")
            return 0

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with out.open("a", encoding="utf-8") as fh:
            for pair in pairs:
                record = {
                    "prompt":               pair.prompt_history,
                    "chosen":               pair.chosen_response,
                    "chosen_thinking":      pair.chosen_thinking,
                    "rejected":             pair.rejected_response,
                    "rejected_thinking":    pair.rejected_thinking,
                    "guidance_correction":  pair.guidance_correction,
                    "source":               "conversation_chain",
                    "session_id":           pair.session_id,
                    "domain":               pair.domain,
                    "session_quality":      round(pair.session_quality, 4),
                    "chosen_turn_depth":    pair.chosen_turn_depth,
                    "rejected_turn_depth":  pair.rejected_turn_depth,
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1

        logger.info("ConversationChainEvaluator: wrote %d pairs to %s", written, output_path)
        return written
