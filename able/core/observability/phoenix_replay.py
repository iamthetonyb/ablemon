"""
Phoenix Historical Replay — upload existing ABLE data to Phoenix so the
dashboard shows your full history, not just new-run-onwards.

Sources replayed (in order of richness):
  1. data/interaction_log.db  — every routed request: tier, provider, latency,
                                tokens, domain, complexity_score, audit_score
  2. data/traces.jsonl         — raw ABLETracer spans (routing, tool calls, etc.)
  3. data/batch_trajectories.jsonl — synthetic training pairs from batch runner

All historical records are emitted as OTel spans through the same
OTelSpanExporter used at runtime, so they appear in Phoenix with the same
rich ABLE attributes and OpenInference semantic conventions.

Usage:
    # Replay everything from the last 30 days (default)
    python -m able.core.observability.phoenix_replay

    # Replay only interaction log, last 7 days
    python -m able.core.observability.phoenix_replay --source interaction_log --days 7

    # Replay all sources, no time limit
    python -m able.core.observability.phoenix_replay --days 0

    # Check Phoenix connectivity only
    python -m able.core.observability.phoenix_replay --check

    # Dry run — count records without sending
    python -m able.core.observability.phoenix_replay --dry-run

Replay speed:
    Phoenix ingests ~500 spans/sec over HTTP.  At default --batch-size 50
    and --delay-ms 100, a 10k-record interaction log replays in ~20s.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Replay state tracking (prevents duplicate sends) ────────────────────────

_REPLAY_STATE_FILE = "data/.phoenix_replay_state.json"


def _load_replay_state(data_dir: str = "data") -> Dict[str, str]:
    """Load the last-replayed marker per source."""
    p = Path(data_dir) / ".phoenix_replay_state.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_replay_state(state: Dict[str, str], data_dir: str = "data"):
    """Persist replay markers so next run skips already-sent data."""
    p = Path(data_dir) / ".phoenix_replay_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def _ensure_phoenix(endpoint: str) -> bool:
    """Check if Phoenix is reachable at the given endpoint."""
    import urllib.request, urllib.error
    health_url = endpoint.replace("/v1/traces", "").rstrip("/") + "/healthz"
    try:
        with urllib.request.urlopen(health_url, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def _setup_phoenix_otel(endpoint: str, project: str = "able") -> bool:
    """
    Register the global OTel tracer provider pointing at Phoenix.
    Returns True if successful.
    """
    try:
        from phoenix.otel import register  # type: ignore[import-untyped]
        register(project_name=project, endpoint=endpoint)
        logger.info("OTel tracer provider registered → %s", endpoint)
        return True
    except ImportError:
        logger.error("arize-phoenix-otel not installed. Run: pip install -r able/requirements-observability.txt")
        return False
    except Exception as exc:
        logger.error("Failed to register Phoenix OTel provider: %s", exc)
        return False


def clear_phoenix_project(endpoint: str = "http://localhost:6006/v1/traces", project: str = "able") -> bool:
    """
    Delete all spans from the Phoenix project so we can re-replay cleanly.
    Uses the Phoenix REST API to find and purge the project.
    """
    import urllib.request, urllib.error
    base = endpoint.replace("/v1/traces", "").rstrip("/")

    try:
        # List projects to find ours
        req = urllib.request.Request(f"{base}/v1/projects", method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            projects = json.loads(r.read())

        project_id = None
        for p in projects.get("data", projects if isinstance(projects, list) else []):
            name = p.get("name", "")
            if name == project:
                project_id = p.get("id")
                break

        if not project_id:
            logger.warning("Phoenix project '%s' not found — nothing to clear", project)
            return True  # Nothing to clear is success

        # Delete the project (Phoenix will recreate it on next span ingest)
        req = urllib.request.Request(
            f"{base}/v1/projects/{project_id}",
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            logger.info("Phoenix project '%s' (id=%s) deleted", project, project_id)
            return True

    except urllib.error.HTTPError as e:
        # Try alternative: clear traces endpoint
        logger.warning("Project delete failed (%s), trying trace purge", e)
        try:
            # Some Phoenix versions support purging spans directly
            req = urllib.request.Request(
                f"{base}/v1/projects/{project_id}/traces",
                method="DELETE",
            )
            with urllib.request.urlopen(req, timeout=10):
                logger.info("Phoenix traces purged for project '%s'", project)
                return True
        except Exception as e2:
            logger.error("Trace purge also failed: %s", e2)
            return False
    except Exception as e:
        logger.error("Failed to clear Phoenix project: %s", e)
        return False


# ── Replay from interaction_log.db ───────────────────────────────────────────

def replay_interaction_log(
    db_path: str,
    since_dt: Optional[datetime],
    dry_run: bool = False,
    batch_size: int = 50,
    delay_ms: int = 100,
) -> int:
    """
    Convert interaction_log rows to OTel LLM spans and send to Phoenix.
    Returns number of spans emitted.
    """
    from able.core.observability.instrumentors import Span, OTelSpanExporter

    p = Path(db_path)
    if not p.exists():
        logger.warning("interaction_log.db not found at %s — skipping", db_path)
        return 0

    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row

    # Discover available columns (schema may differ across versions)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(interaction_log)").fetchall()}

    query = "SELECT * FROM interaction_log WHERE success = 1"
    params: list = []
    if since_dt:
        query += " AND timestamp >= ?"
        params.append(since_dt.isoformat())
    query += " ORDER BY timestamp ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    logger.info("interaction_log: %d rows to replay (since=%s)", len(rows), since_dt)

    if dry_run:
        logger.info("[dry-run] Would replay %d interaction_log spans", len(rows))
        return len(rows)

    exporter = OTelSpanExporter()
    if not exporter.is_available:
        logger.error("OTel exporter not available — is Phoenix running?")
        return 0

    emitted = 0
    batch: List[Span] = []

    for row in rows:
        # Parse timestamp → float
        try:
            ts_str = row["timestamp"]
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            ts = datetime.fromisoformat(ts_str).timestamp()
        except Exception:
            ts = time.time()

        latency_s = (row["latency_ms"] if "latency_ms" in columns and row["latency_ms"] else 0.0) / 1000
        start_t = ts - latency_s
        end_t = ts

        attrs: dict = {}
        for col in ("actual_provider", "selected_provider", "model_id"):
            if col in columns and row[col]:
                attrs["model"] = row[col]
                break

        for col in ("raw_input", "message_preview"):
            if col in columns and row[col]:
                attrs["input_text"] = str(row[col])[:2000]
                break
        if "raw_output" in columns and row["raw_output"]:
            attrs["output_text"] = str(row["raw_output"])[:2000]

        for col in ("selected_tier", "tier"):
            if col in columns and row[col] is not None:
                attrs["tier"] = int(row[col])
                break
        if "complexity_score" in columns and row["complexity_score"] is not None:
            attrs["complexity_score"] = float(row["complexity_score"])
        if "domain" in columns and row["domain"]:
            attrs["domain"] = row["domain"]
        if "actual_provider" in columns and row["actual_provider"]:
            attrs["provider"] = row["actual_provider"]
        if "latency_ms" in columns and row["latency_ms"]:
            attrs["latency_ms"] = float(row["latency_ms"])
        if "fallback_used" in columns and row["fallback_used"] is not None:
            attrs["fallback_used"] = bool(row["fallback_used"])
        if "input_tokens" in columns and row["input_tokens"]:
            attrs["input_tokens"] = int(row["input_tokens"])
        if "output_tokens" in columns and row["output_tokens"]:
            attrs["output_tokens"] = int(row["output_tokens"])
        if "audit_score" in columns and row["audit_score"] is not None:
            attrs["routing_decision"] = f"audit={row['audit_score']:.1f}"
            attrs["audit_score"] = float(row["audit_score"])

        # AGI-relevant attributes — thinking, confidence, feedback, tools
        if "thinking_tokens_preserved" in columns and row["thinking_tokens_preserved"]:
            attrs["thinking_preserved"] = True
        if "thinking_content" in columns and row["thinking_content"]:
            attrs["has_thinking"] = True
            attrs["thinking_preview"] = str(row["thinking_content"])[:500]
        if "response_confidence" in columns and row["response_confidence"] is not None:
            attrs["response_confidence"] = float(row["response_confidence"])
        if "quality_score" in columns and row["quality_score"] is not None:
            attrs["quality_score"] = float(row["quality_score"])
        if "feedback_signal" in columns and row["feedback_signal"]:
            attrs["feedback_signal"] = row["feedback_signal"]
        if "correction_detected" in columns and row["correction_detected"]:
            attrs["correction_detected"] = True
        if "guidance_needed" in columns and row["guidance_needed"]:
            attrs["guidance_needed"] = float(row["guidance_needed"])
        if "tools_called" in columns and row["tools_called"]:
            attrs["tools_called"] = row["tools_called"]
        if "conversation_depth" in columns and row["conversation_depth"]:
            attrs["conversation_depth"] = int(row["conversation_depth"])
        if "enrichment_level" in columns and row["enrichment_level"]:
            attrs["enrichment_level"] = row["enrichment_level"]
        if "channel" in columns and row["channel"]:
            attrs["channel"] = row["channel"]
        if "cost_usd" in columns and row["cost_usd"]:
            attrs["cost_usd"] = float(row["cost_usd"])
        if "session_id" in columns and row["session_id"]:
            attrs["session_id"] = row["session_id"]

        # Use interaction ID as trace_id for grouping
        row_id = str(row["id"]) if "id" in columns else f"row_{emitted}"
        span = Span(
            trace_id=row_id.replace("-", "")[:32],
            span_id=row_id.replace("-", "")[:16],
            name=f"interaction.{attrs.get('domain', 'default')}",
            kind="llm",
            attributes=attrs,
            start_time=start_t,
            end_time=end_t,
            status="ok",
        )
        batch.append(span)

        if len(batch) >= batch_size:
            for s in batch:
                exporter.export(s)
            emitted += len(batch)
            batch.clear()
            if delay_ms > 0:
                time.sleep(delay_ms / 1000)

    # Flush remaining
    for s in batch:
        exporter.export(s)
    emitted += len(batch)

    # Force OTel batch export flush
    try:
        from opentelemetry import trace as _otel  # type: ignore[import-untyped]
        provider = _otel.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=10_000)
    except Exception:
        pass

    logger.info("interaction_log: emitted %d/%d spans to Phoenix", emitted, len(rows))
    return emitted


# ── Replay from data/traces.jsonl ────────────────────────────────────────────

def replay_traces_jsonl(
    jsonl_path: str,
    since_dt: Optional[datetime],
    dry_run: bool = False,
    batch_size: int = 50,
    delay_ms: int = 100,
) -> int:
    """
    Re-emit ABLETracer spans from the JSONL fallback file to Phoenix.
    Returns number of spans emitted.
    """
    from able.core.observability.instrumentors import Span, OTelSpanExporter

    p = Path(jsonl_path)
    if not p.exists():
        logger.warning("traces.jsonl not found at %s — skipping", jsonl_path)
        return 0

    records: List[dict] = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if since_dt:
        filtered = []
        for r in records:
            try:
                if r.get("start_time", 0) >= since_dt.timestamp():
                    filtered.append(r)
            except Exception:
                pass
        records = filtered

    logger.info("traces.jsonl: %d spans to replay", len(records))

    if dry_run:
        logger.info("[dry-run] Would replay %d JSONL spans", len(records))
        return len(records)

    exporter = OTelSpanExporter()
    if not exporter.is_available:
        logger.error("OTel exporter not available — is Phoenix running?")
        return 0

    emitted = 0
    for i, r in enumerate(records):
        try:
            span = Span(
                trace_id=r.get("trace_id", ""),
                span_id=r.get("span_id", ""),
                name=r.get("name", "unknown"),
                kind=r.get("kind", "llm"),
                attributes=r.get("attributes", {}),
                start_time=float(r.get("start_time", 0)),
                end_time=float(r.get("end_time") or r.get("start_time", 0) + 1),
                status=r.get("status", "ok"),
                parent_span_id=r.get("parent_span_id"),
                events=r.get("events", []),
            )
            exporter.export(span)
            emitted += 1
        except Exception as exc:
            logger.debug("Skipping malformed span record: %s", exc)

        if (i + 1) % batch_size == 0 and delay_ms > 0:
            time.sleep(delay_ms / 1000)

    # Flush
    try:
        from opentelemetry import trace as _otel  # type: ignore[import-untyped]
        provider = _otel.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=10_000)
    except Exception:
        pass

    logger.info("traces.jsonl: emitted %d/%d spans to Phoenix", emitted, len(records))
    return emitted


# ── Replay from data/batch_trajectories.jsonl ────────────────────────────────

def replay_batch_trajectories(
    jsonl_path: str,
    since_dt: Optional[datetime],
    dry_run: bool = False,
    batch_size: int = 50,
    delay_ms: int = 100,
) -> int:
    """
    Emit batch trajectory records as OTel LLM spans to Phoenix.
    Returns number of spans emitted.
    """
    from able.core.observability.instrumentors import Span, OTelSpanExporter

    p = Path(jsonl_path)
    if not p.exists():
        logger.info("batch_trajectories.jsonl not found — nothing to replay yet")
        return 0

    records: List[dict] = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if since_dt:
        filtered = []
        for r in records:
            try:
                ts_str = r.get("timestamp", "")
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                ts = datetime.fromisoformat(ts_str).timestamp()
                if ts >= since_dt.timestamp():
                    filtered.append(r)
            except Exception:
                filtered.append(r)  # include if can't parse
        records = filtered

    logger.info("batch_trajectories.jsonl: %d records to replay", len(records))

    if dry_run:
        logger.info("[dry-run] Would replay %d batch trajectory spans", len(records))
        return len(records)

    exporter = OTelSpanExporter()
    if not exporter.is_available:
        logger.error("OTel exporter not available — is Phoenix running?")
        return 0

    emitted = 0
    for i, r in enumerate(records):
        try:
            ts_str = r.get("timestamp", "")
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            end_t = datetime.fromisoformat(ts_str).timestamp() if ts_str else time.time()
            latency_s = float(r.get("latency_ms", 1000)) / 1000
            start_t = end_t - latency_s

            span = Span(
                trace_id=str(r.get("id", f"batch_{i}")).replace("-", "")[:32] or f"batch{i:08d}",
                span_id=f"bt{i:014d}",
                name=f"batch.{r.get('domain', 'default')}",
                kind="llm",
                attributes={
                    "model": r.get("model", ""),
                    "input_text": str(r.get("prompt", ""))[:2000],
                    "output_text": str(r.get("response", ""))[:2000],
                    "input_tokens": int(r.get("input_tokens", 0)),
                    "output_tokens": int(r.get("output_tokens", 0)),
                    "tier": int(r.get("tier", 1)),
                    "complexity_score": float(r.get("complexity_score", 0)),
                    "domain": r.get("domain", "default"),
                    "provider": r.get("provider", ""),
                    "latency_ms": float(r.get("latency_ms", 0)),
                    "source": "batch_runner",
                },
                start_time=start_t,
                end_time=end_t,
                status="ok",
            )
            exporter.export(span)
            emitted += 1
        except Exception as exc:
            logger.debug("Skipping batch record %d: %s", i, exc)

        if (i + 1) % batch_size == 0 and delay_ms > 0:
            time.sleep(delay_ms / 1000)

    # Flush
    try:
        from opentelemetry import trace as _otel  # type: ignore[import-untyped]
        provider = _otel.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=10_000)
    except Exception:
        pass

    logger.info("batch_trajectories: emitted %d/%d spans to Phoenix", emitted, len(records))
    return emitted


# ── Replay from data/distillation.db ────────────────────────────────────────

def replay_distillation_pairs(
    db_path: str,
    since_dt: Optional[datetime],
    dry_run: bool = False,
    batch_size: int = 50,
    delay_ms: int = 100,
) -> int:
    """
    Emit distillation training pairs as OTel LLM spans to Phoenix.
    These represent the highest-quality curated data in ABLE — each pair has
    a quality score, domain, thinking trace, and gold model attribution.
    Returns number of spans emitted.
    """
    from able.core.observability.instrumentors import Span, OTelSpanExporter

    p = Path(db_path)
    if not p.exists():
        logger.warning("distillation.db not found at %s — skipping", db_path)
        return 0

    import sqlite3 as _sql
    conn = _sql.connect(str(p))
    conn.row_factory = _sql.Row

    query = "SELECT * FROM distillation_pairs"
    params: list = []
    if since_dt:
        query += " WHERE created_at >= ?"
        params.append(since_dt.isoformat())
    query += " ORDER BY created_at ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    logger.info("distillation_pairs: %d rows to replay (since=%s)", len(rows), since_dt)

    if dry_run:
        logger.info("[dry-run] Would replay %d distillation pair spans", len(rows))
        return len(rows)

    exporter = OTelSpanExporter()
    if not exporter.is_available:
        logger.error("OTel exporter not available — is Phoenix running?")
        return 0

    emitted = 0
    batch: List[Span] = []

    for i, row in enumerate(rows):
        try:
            ts_str = row["created_at"] or ""
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            end_t = datetime.fromisoformat(ts_str).timestamp() if ts_str else time.time()
        except Exception:
            end_t = time.time()
        start_t = end_t - 1.0  # 1s nominal duration

        domain = row["domain"] or "default"
        has_thinking = bool(row["gold_thinking"])

        span = Span(
            trace_id=str(row["id"]).replace("-", "")[:32] or f"dist{i:08d}",
            span_id=f"dp{i:014d}",
            name=f"distillation.{domain}",
            kind="llm",
            attributes={
                "model": row["gold_model"] or "",
                "input_text": str(row["prompt"] or "")[:2000],
                "output_text": str(row["gold_response"] or "")[:2000],
                "domain": domain,
                "quality_score": float(row["quality_score"] or 0),
                "has_thinking": has_thinking,
                "tenant_id": row["tenant_id"] or "default",
                "corpus_version": row["corpus_version"] or "",
                "source": "distillation_corpus",
                "tags": row["tags"] or "",
            },
            start_time=start_t,
            end_time=end_t,
            status="ok",
        )
        batch.append(span)

        if len(batch) >= batch_size:
            for s in batch:
                exporter.export(s)
            emitted += len(batch)
            batch.clear()
            if delay_ms > 0:
                time.sleep(delay_ms / 1000)

    # Flush remaining
    for s in batch:
        exporter.export(s)
    emitted += len(batch)

    # Force OTel batch export flush
    try:
        from opentelemetry import trace as _otel
        provider = _otel.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=10_000)
    except Exception:
        pass

    logger.info("distillation_pairs: emitted %d/%d spans to Phoenix", emitted, len(rows))
    return emitted


# ── Replay cron execution history ──────────────────────────────────────────

def replay_cron_executions(
    db_path: str,
    since_dt: Optional[datetime],
    dry_run: bool = False,
    batch_size: int = 50,
    delay_ms: int = 100,
) -> int:
    """
    Emit cron execution records as OTel spans to Phoenix.
    Provides visibility into scheduler health, job durations, and failure patterns.
    """
    from able.core.observability.instrumentors import Span, OTelSpanExporter

    p = Path(db_path)
    if not p.exists():
        logger.warning("cron_executions.db not found at %s — skipping", db_path)
        return 0

    import sqlite3 as _sql
    conn = _sql.connect(str(p))
    conn.row_factory = _sql.Row

    query = "SELECT * FROM executions"
    params: list = []
    if since_dt:
        query += " WHERE started_at >= ?"
        params.append(since_dt.timestamp())
    query += " ORDER BY started_at ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    logger.info("cron_executions: %d rows to replay (since=%s)", len(rows), since_dt)

    if dry_run:
        logger.info("[dry-run] Would replay %d cron execution spans", len(rows))
        return len(rows)

    exporter = OTelSpanExporter()
    if not exporter.is_available:
        logger.error("OTel exporter not available — is Phoenix running?")
        return 0

    emitted = 0
    for i, row in enumerate(rows):
        start_t = float(row["started_at"] or time.time())
        duration = float(row["duration_s"] or 0)
        end_t = float(row["finished_at"] or (start_t + duration))
        success = bool(row["success"]) if row["success"] is not None else None

        span = Span(
            trace_id=str(row["id"]).replace("-", "")[:32] or f"cron{i:08d}",
            span_id=f"cr{i:014d}",
            name=f"cron.{row['job_name']}",
            kind="tool",
            attributes={
                "job_name": row["job_name"],
                "trigger": row["trigger"] or "scheduled",
                "attempt": int(row["attempt"] or 1),
                "duration_s": duration,
                "success": success,
                "error": row["error"] or "",
                "source": "cron_scheduler",
            },
            start_time=start_t,
            end_time=end_t,
            status="ok" if success else "error",
        )
        exporter.export(span)
        emitted += 1

        if (i + 1) % batch_size == 0 and delay_ms > 0:
            time.sleep(delay_ms / 1000)

    try:
        from opentelemetry import trace as _otel
        provider = _otel.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=10_000)
    except Exception:
        pass

    logger.info("cron_executions: emitted %d/%d spans to Phoenix", emitted, len(rows))
    return emitted


# ── Evolution Cycles ─────────────────────────────────────────────────────────

def replay_evolution_cycles(
    evo_dir: str = "data/evolution_cycles",
    dry_run: bool = False,
    skip_first_n: int = 0,
    **_kwargs,
) -> int:
    """Replay evolution daemon weight-tuning cycles as spans."""
    evo_path = Path(evo_dir)
    if not evo_path.exists():
        logger.info("evolution_cycles dir not found at %s", evo_dir)
        return 0

    files = sorted(evo_path.glob("evo_*.json"))
    if skip_first_n > 0:
        files = files[skip_first_n:]
    if not files:
        return 0

    if dry_run:
        logger.info("evolution_cycles: %d new cycles (dry run, skipped %d)", len(files), skip_first_n)
        return len(files)

    from opentelemetry import trace as _otel
    tracer = _otel.get_tracer("able.evolution")
    emitted = 0

    for f in files:
        try:
            data = json.loads(f.read_text())
            ts_str = data.get("timestamp", "")

            with tracer.start_as_current_span("evolution.weight_cycle") as span:
                span.set_attribute("evolution.cycle_file", f.name)
                span.set_attribute("evolution.timestamp", ts_str)

                # Extract weight changes if present
                changes = data.get("changes", data.get("weight_changes", {}))
                if isinstance(changes, dict):
                    for k, v in list(changes.items())[:20]:
                        if isinstance(v, (int, float)):
                            span.set_attribute(f"evolution.weight.{k}", v)

                span.set_attribute("evolution.raw_json", json.dumps(data)[:2000])
                emitted += 1
        except Exception as exc:
            logger.debug("Skip evolution file %s: %s", f.name, exc)

    try:
        provider = _otel.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=5_000)
    except Exception:
        pass

    logger.info("evolution_cycles: emitted %d/%d spans", emitted, len(files))
    return emitted


# ── Research Reports ─────────────────────────────────────────────────────────

def replay_research_reports(
    report_dir: str = "data/research_reports",
    dry_run: bool = False,
    skip_files: Optional[set] = None,
    **_kwargs,
) -> int:
    """Replay research scout reports as spans."""
    report_path = Path(report_dir)
    # Also check operator path
    operator_path = Path.home() / ".able" / "reports" / "research"

    files = []
    for d in [report_path, operator_path]:
        if d.exists():
            files.extend(d.glob("research_*.json"))

    # Dedup by filename AND skip already-replayed files
    seen = set(skip_files or set())
    unique_files = []
    for f in files:
        if f.name not in seen:
            seen.add(f.name)
            unique_files.append(f)

    if not unique_files:
        return 0
    if dry_run:
        logger.info("research_reports: %d new reports (dry run)", len(unique_files))
        return len(unique_files)

    from opentelemetry import trace as _otel
    tracer = _otel.get_tracer("able.research")
    emitted = 0

    for f in sorted(unique_files):
        try:
            data = json.loads(f.read_text())

            with tracer.start_as_current_span("research.report") as span:
                span.set_attribute("research.file", f.name)
                span.set_attribute("research.timestamp", data.get("timestamp", ""))
                span.set_attribute("research.total_findings", data.get("total_findings", 0))
                span.set_attribute("research.high_priority_count", data.get("high_priority_count", 0))
                span.set_attribute("research.queries_run", data.get("search_queries_run", 0))
                span.set_attribute("research.error_count", len(data.get("errors", [])))

                # Include top findings as attributes
                findings = data.get("findings", [])
                for i, finding in enumerate(findings[:5]):
                    span.set_attribute(f"research.finding.{i}.title", finding.get("title", "")[:100])
                    span.set_attribute(f"research.finding.{i}.relevance", finding.get("relevance", ""))
                    span.set_attribute(f"research.finding.{i}.source", finding.get("source", ""))

                emitted += 1
        except Exception as exc:
            logger.debug("Skip research file %s: %s", f.name, exc)

    try:
        provider = _otel.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=5_000)
    except Exception:
        pass

    logger.info("research_reports: emitted %d/%d spans", emitted, len(unique_files))
    return emitted


# ── High-level runner ─────────────────────────────────────────────────────────

def run_replay(
    days: int = 30,
    sources: Optional[List[str]] = None,
    dry_run: bool = False,
    batch_size: int = 50,
    delay_ms: int = 100,
    endpoint: str = "http://localhost:6006/v1/traces",
    data_dir: str = "data",
    force: bool = False,
) -> dict:
    """
    Replay all configured sources to Phoenix.

    Idempotent: tracks what was last replayed per source in
    data/.phoenix_replay_state.json. Re-running only sends NEW data
    unless --force is used.

    Args:
        days:       How many days back to replay (0 = all time).
        sources:    Which sources to replay: "interaction_log", "traces", "batch".
                    None = all.
        dry_run:    Count records only, don't send.
        batch_size: Spans per batch before sleeping.
        delay_ms:   Sleep between batches (ms) — throttle to respect Phoenix limits.
        endpoint:   Phoenix OTLP HTTP ingest URL.
        data_dir:   Directory containing ABLE data files.
        force:      Ignore replay state and re-send everything (use after clearing Phoenix).
    """
    sources = sources or ["interaction_log", "traces", "batch", "distillation", "cron", "evolution", "research"]
    since_dt = (
        datetime.now(timezone.utc) - timedelta(days=days)
        if days > 0
        else None
    )
    data = Path(data_dir)

    # Load replay state for idempotency
    replay_state = {} if force else _load_replay_state(data_dir)
    if replay_state and not force:
        logger.info(
            "Replay state loaded — will only send new data since last replay. "
            "Use --force to re-send everything."
        )

    if not dry_run:
        # Verify Phoenix is up before starting
        if not _ensure_phoenix(endpoint):
            logger.error(
                "Phoenix not reachable at %s — start it with: "
                "docker compose --profile observability up -d",
                endpoint,
            )
            return {"error": "Phoenix unreachable", "endpoint": endpoint}

        # Register OTel provider → Phoenix
        if not _setup_phoenix_otel(endpoint):
            return {"error": "OTel provider registration failed"}

    totals: dict = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    if "interaction_log" in sources:
        # Use replay state to only send rows newer than last replay
        effective_since = since_dt
        last_replay = replay_state.get("interaction_log")
        if last_replay and not force:
            last_dt = datetime.fromisoformat(last_replay)
            if effective_since is None or last_dt > effective_since:
                effective_since = last_dt

        totals["interaction_log"] = replay_interaction_log(
            str(data / "interaction_log.db"),
            since_dt=effective_since,
            dry_run=dry_run,
            batch_size=batch_size,
            delay_ms=delay_ms,
        )
        if not dry_run:
            replay_state["interaction_log"] = now_iso

    if "traces" in sources:
        effective_since = since_dt
        last_replay = replay_state.get("traces")
        if last_replay and not force:
            last_dt = datetime.fromisoformat(last_replay)
            if effective_since is None or last_dt > effective_since:
                effective_since = last_dt

        totals["traces"] = replay_traces_jsonl(
            str(data / "traces.jsonl"),
            since_dt=effective_since,
            dry_run=dry_run,
            batch_size=batch_size,
            delay_ms=delay_ms,
        )
        if not dry_run:
            replay_state["traces"] = now_iso

    if "batch" in sources:
        effective_since = since_dt
        last_replay = replay_state.get("batch")
        if last_replay and not force:
            last_dt = datetime.fromisoformat(last_replay)
            if effective_since is None or last_dt > effective_since:
                effective_since = last_dt

        totals["batch"] = replay_batch_trajectories(
            str(data / "batch_trajectories.jsonl"),
            since_dt=effective_since,
            dry_run=dry_run,
            batch_size=batch_size,
            delay_ms=delay_ms,
        )
        if not dry_run:
            replay_state["batch"] = now_iso

    if "distillation" in sources:
        effective_since = since_dt
        last_replay = replay_state.get("distillation")
        if last_replay and not force:
            last_dt = datetime.fromisoformat(last_replay)
            if effective_since is None or last_dt > effective_since:
                effective_since = last_dt

        totals["distillation"] = replay_distillation_pairs(
            str(data / "distillation.db"),
            since_dt=effective_since,
            dry_run=dry_run,
            batch_size=batch_size,
            delay_ms=delay_ms,
        )
        if not dry_run:
            replay_state["distillation"] = now_iso

    if "cron" in sources:
        effective_since = since_dt
        last_replay = replay_state.get("cron")
        if last_replay and not force:
            last_dt = datetime.fromisoformat(last_replay)
            if effective_since is None or last_dt > effective_since:
                effective_since = last_dt

        totals["cron"] = replay_cron_executions(
            str(data / "cron_executions.db"),
            since_dt=effective_since,
            dry_run=dry_run,
            batch_size=batch_size,
            delay_ms=delay_ms,
        )
        if not dry_run:
            replay_state["cron"] = now_iso

    if "evolution" in sources:
        # Evolution and research use file-based replay — track by file count
        last_evo_count = int(replay_state.get("evolution_count", 0)) if not force else 0
        totals["evolution"] = replay_evolution_cycles(
            str(data / "evolution_cycles"),
            dry_run=dry_run,
            skip_first_n=last_evo_count,
        )
        if not dry_run:
            # Update count so next run skips these
            new_count = last_evo_count + totals["evolution"]
            replay_state["evolution_count"] = str(new_count)

    if "research" in sources:
        last_research_files = set(json.loads(replay_state.get("research_files", "[]"))) if not force else set()
        totals["research"] = replay_research_reports(
            str(data / "research_reports"),
            dry_run=dry_run,
            skip_files=last_research_files,
        )
        if not dry_run:
            # Track which files were replayed
            report_path = Path(data_dir) / "research_reports"
            operator_path = Path.home() / ".able" / "reports" / "research"
            all_files = set()
            for d in [report_path, operator_path]:
                if d.exists():
                    all_files.update(f.name for f in d.glob("research_*.json"))
            replay_state["research_files"] = json.dumps(sorted(all_files | last_research_files))

    # Save replay state for next run
    if not dry_run:
        _save_replay_state(replay_state, data_dir)

    total_spans = sum(totals.values())
    logger.info(
        "Phoenix replay complete: %d spans total | %s",
        total_spans,
        " | ".join(f"{k}={v}" for k, v in totals.items()),
    )
    return {"total_spans": total_spans, "by_source": totals, "dry_run": dry_run}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="able.core.observability.phoenix_replay",
        description="Replay ABLE historical data to Phoenix observability dashboard.",
    )
    p.add_argument(
        "--days",
        type=int,
        default=30,
        help="How many days back to replay (0 = all time, default: 30)",
    )
    p.add_argument(
        "--source",
        choices=["interaction_log", "traces", "batch", "distillation", "cron", "all"],
        default="all",
        help="Which data source to replay (default: all)",
    )
    p.add_argument(
        "--endpoint",
        default="http://localhost:6006/v1/traces",
        help="Phoenix OTLP HTTP ingest URL",
    )
    p.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing ABLE data files (default: data/)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Spans per batch before throttle sleep (default: 50)",
    )
    p.add_argument(
        "--delay-ms",
        type=int,
        default=100,
        help="Sleep between batches in ms (default: 100)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count records only — don't send to Phoenix",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Check Phoenix connectivity and exit",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Ignore replay state — re-send everything (use after clearing Phoenix project)",
    )
    p.add_argument(
        "--clear-project",
        action="store_true",
        help="Delete the Phoenix project and recreate it (removes all spans, starts fresh)",
    )
    return p


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    args = _build_parser().parse_args()

    if args.check:
        ok = _ensure_phoenix(args.endpoint)
        print(f"Phoenix at {args.endpoint}: {'REACHABLE ✓' if ok else 'UNREACHABLE ✗'}")
        if not ok:
            print("Start with: docker compose --profile observability up -d")
        sys.exit(0 if ok else 1)

    if getattr(args, 'clear_project', False):
        ok = clear_phoenix_project(args.endpoint)
        if ok:
            print("Phoenix project cleared. Run again with --force to re-replay all data.")
            # Also clear replay state so --force isn't strictly needed
            state_path = Path(args.data_dir) / ".phoenix_replay_state.json"
            if state_path.exists():
                state_path.unlink()
                print("Replay state cleared.")
        else:
            print("Failed to clear Phoenix project.", file=sys.stderr)
        sys.exit(0 if ok else 1)

    sources = None if args.source == "all" else [args.source]
    result = run_replay(
        days=args.days,
        sources=sources,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        delay_ms=args.delay_ms,
        endpoint=args.endpoint,
        data_dir=args.data_dir,
        force=getattr(args, 'force', False),
    )

    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    label = "[DRY RUN] " if args.dry_run else ""
    print(f"{label}Replayed {result['total_spans']} spans to Phoenix")
    for src, n in result.get("by_source", {}).items():
        print(f"  {src}: {n}")
    print(f"\nView at: {args.endpoint.replace('/v1/traces', '')}")
