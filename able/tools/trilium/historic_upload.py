"""
Historic Data Upload to TriliumNext
Loads all ABLE historic data into the knowledge base as structured notes.
"""

import asyncio
import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


async def upload_all(client):
    """Upload all historic data to Trilium. Idempotent — skips existing categories."""
    from able.tools.trilium.client import KNOWN_PARENTS

    kb_root = KNOWN_PARENTS.get("knowledge_base") or "root"

    # Create top-level category notes (idempotent — search first)
    category_defs = [
        ("Routing History", "All interaction log entries — routing decisions, latency, costs"),
        ("Distillation Corpus", "Training pairs by domain — the fuel for fine-tuning"),
        ("Evolution Cycles", "Weight tuning history from the self-evolving daemon"),
        ("Batch Trajectories", "Synthetic corpus runs from batch trajectory runner"),
        ("Cron History", "Scheduled job execution history"),
        ("System Stats", "Aggregate statistics and dashboards"),
        ("Architecture & Logic", "System design, routing logic, data flows, module map"),
    ]

    categories = {}
    for name, desc in category_defs:
        # Check if already exists to avoid duplicates
        existing = await client.search_notes(f'note.title="{name}" note.parents.noteId={kb_root}')
        if existing:
            nid = existing[0]["noteId"] if isinstance(existing[0], dict) else existing[0].note_id
            categories[name] = nid
            logger.info("Category exists: %s (%s)", name, nid)
        else:
            note = await client.create_note(kb_root, name, f"<p>{desc}</p>")
            categories[name] = note.note_id
            await client.create_label(note.note_id, "iconClass", "bx bx-data")
            logger.info("Created category: %s (%s)", name, note.note_id)

    # 1. Interaction Log
    await _upload_interactions(client, categories["Routing History"])

    # 2. Distillation summary by domain
    await _upload_distillation_summary(client, categories["Distillation Corpus"])

    # 3. Evolution cycles
    await _upload_evolution(client, categories["Evolution Cycles"])

    # 4. Batch trajectories
    await _upload_trajectories(client, categories["Batch Trajectories"])

    # 5. Cron history
    await _upload_cron(client, categories["Cron History"])

    # 6. System stats dashboard
    await _upload_stats(client, categories["System Stats"])

    # 7. Architecture & logic documentation
    await _upload_architecture(client, categories["Architecture & Logic"])

    logger.info("Historic upload complete")
    return categories


async def _upload_interactions(client, parent_id):
    """Upload interaction log entries as individual notes."""
    db_path = DATA_DIR / "interaction_log.db"
    if not db_path.exists():
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM interaction_log ORDER BY timestamp ASC"
    ).fetchall()
    conn.close()

    for row in rows:
        ts = row["timestamp"][:19].replace("T", " ")
        title = f"[{ts}] T{row['selected_tier']} → {row['actual_provider'] or row['selected_provider']}"
        preview = (row["message_preview"] or "")[:100]

        features = {}
        try:
            features = json.loads(row["features"] or "{}")
        except Exception:
            pass

        html = f"""<table>
<tr><td><b>Time</b></td><td>{row['timestamp']}</td></tr>
<tr><td><b>Message</b></td><td>{_esc(preview)}</td></tr>
<tr><td><b>Complexity</b></td><td>{row['complexity_score']:.2f}</td></tr>
<tr><td><b>Tier</b></td><td>{row['selected_tier']}</td></tr>
<tr><td><b>Provider</b></td><td>{row['actual_provider'] or row['selected_provider']}</td></tr>
<tr><td><b>Domain</b></td><td>{row['domain']}</td></tr>
<tr><td><b>Latency</b></td><td>{row['latency_ms']:.0f}ms</td></tr>
<tr><td><b>Cost</b></td><td>${row['cost_usd']:.4f}</td></tr>
<tr><td><b>Success</b></td><td>{'Yes' if row['success'] else 'No'}</td></tr>
<tr><td><b>Channel</b></td><td>{row['channel']}</td></tr>
<tr><td><b>Enrichment</b></td><td>{row['enrichment_level']}</td></tr>
<tr><td><b>Confidence</b></td><td>{row['response_confidence'] or 'N/A'}</td></tr>
</table>"""

        if features:
            html += "<h4>Scoring Features</h4><pre>" + json.dumps(features, indent=2) + "</pre>"

        note = await client.create_note(parent_id, title, html)
        await client.create_label(note.note_id, "tier", str(row["selected_tier"]))
        await client.create_label(note.note_id, "domain", row["domain"] or "default")
        await client.create_label(note.note_id, "provider", row["actual_provider"] or "")

    logger.info("Uploaded %d interaction log entries", len(rows))


async def _upload_distillation_summary(client, parent_id):
    """Upload distillation pairs grouped by domain as summary notes."""
    db_path = DATA_DIR / "distillation.db"
    if not db_path.exists():
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Get domain breakdown
    domains = conn.execute(
        "SELECT domain, COUNT(*) as cnt, AVG(quality_score) as avg_q "
        "FROM distillation_pairs GROUP BY domain ORDER BY cnt DESC"
    ).fetchall()

    # Overview note
    total = sum(d["cnt"] for d in domains)
    overview_html = f"<h3>Corpus Overview</h3><p>Total pairs: <b>{total}</b></p>"
    overview_html += "<table><tr><th>Domain</th><th>Count</th><th>Avg Quality</th></tr>"
    for d in domains:
        overview_html += f"<tr><td>{d['domain'] or '(untagged)'}</td><td>{d['cnt']}</td><td>{d['avg_q']:.2f}</td></tr>"
    overview_html += "</table>"

    overview = await client.create_note(parent_id, f"Corpus Overview — {total} Pairs", overview_html)
    await client.create_label(overview.note_id, "iconClass", "bx bx-bar-chart")

    # Per-domain notes with sample pairs
    for d in domains:
        domain = d["domain"] or "(untagged)"
        samples = conn.execute(
            "SELECT prompt, gold_model, quality_score, created_at "
            "FROM distillation_pairs WHERE domain = ? ORDER BY quality_score DESC LIMIT 5",
            (d["domain"],)
        ).fetchall()

        html = f"<h3>{domain}</h3><p>{d['cnt']} pairs, avg quality {d['avg_q']:.2f}</p>"
        html += "<h4>Top 5 by Quality</h4>"
        for s in samples:
            prompt_preview = _esc((s["prompt"] or "")[:200])
            html += f"""<div style="border:1px solid #ccc; padding:8px; margin:4px 0;">
<b>Quality:</b> {s['quality_score']:.2f} | <b>Model:</b> {s['gold_model']} | <b>Date:</b> {s['created_at'][:10]}<br/>
<i>{prompt_preview}</i>
</div>"""

        note = await client.create_note(parent_id, f"{domain} — {d['cnt']} pairs", html)
        await client.create_label(note.note_id, "domain", domain)
        await client.create_label(note.note_id, "pairCount", str(d["cnt"]))

    conn.close()
    logger.info("Uploaded distillation summary for %d domains", len(domains))


async def _upload_evolution(client, parent_id):
    """Upload evolution cycle snapshots."""
    evo_dir = DATA_DIR / "evolution_cycles"
    if not evo_dir.exists():
        return

    for f in sorted(evo_dir.glob("evo_*.json")):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue

        ts = data.get("timestamp", f.stem)
        title = f"Evolution Cycle — {ts[:19] if len(ts) > 19 else f.stem}"

        html = "<h3>Weight Changes</h3><pre>" + json.dumps(data, indent=2)[:3000] + "</pre>"

        note = await client.create_note(parent_id, title, html)
        await client.create_label(note.note_id, "cycleFile", f.name)

    logger.info("Uploaded %d evolution cycles", len(list(evo_dir.glob("evo_*.json"))))


async def _upload_trajectories(client, parent_id):
    """Upload batch trajectory runs."""
    traj_path = DATA_DIR / "batch_trajectories.jsonl"
    if not traj_path.exists():
        return

    entries = []
    for line in traj_path.read_text().splitlines():
        try:
            entries.append(json.loads(line))
        except Exception:
            continue

    for entry in entries:
        title = f"Trajectory — {entry.get('domain', '?')} — {entry.get('prompt', '')[:50]}"
        html = f"""<table>
<tr><td><b>Domain</b></td><td>{entry.get('domain', '?')}</td></tr>
<tr><td><b>Model</b></td><td>{entry.get('model', '?')}</td></tr>
<tr><td><b>Status</b></td><td>{entry.get('status', '?')}</td></tr>
<tr><td><b>Duration</b></td><td>{entry.get('duration_s', '?')}s</td></tr>
</table>
<h4>Prompt</h4><pre>{_esc(entry.get('prompt', '')[:500])}</pre>
<h4>Response Preview</h4><pre>{_esc(entry.get('response', '')[:1000])}</pre>"""

        note = await client.create_note(parent_id, title, html)
        await client.create_label(note.note_id, "domain", entry.get("domain", ""))

    logger.info("Uploaded %d batch trajectories", len(entries))


async def _upload_cron(client, parent_id):
    """Upload cron execution history."""
    db_path = DATA_DIR / "cron_executions.db"
    if not db_path.exists():
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM executions ORDER BY started_at ASC").fetchall()
    conn.close()

    for row in rows:
        status = "Success" if row["success"] else "Failed"
        started = str(row["started_at"] or "")
        if isinstance(row["started_at"], (int, float)):
            started = datetime.fromtimestamp(row["started_at"]).isoformat()
        finished = str(row["finished_at"] or "")
        if isinstance(row["finished_at"], (int, float)):
            finished = datetime.fromtimestamp(row["finished_at"]).isoformat()
        title = f"[{started[:16]}] {row['job_name']} — {status}"

        html = f"""<table>
<tr><td><b>Job</b></td><td>{row['job_name']}</td></tr>
<tr><td><b>Started</b></td><td>{started}</td></tr>
<tr><td><b>Finished</b></td><td>{finished}</td></tr>
<tr><td><b>Success</b></td><td>{status}</td></tr>
<tr><td><b>Trigger</b></td><td>{row['trigger']}</td></tr>
<tr><td><b>Attempt</b></td><td>{row['attempt']}</td></tr>
<tr><td><b>Duration</b></td><td>{row['duration_s'] or 0:.1f}s</td></tr>
</table>"""

        if row["error"]:
            html += f"<h4>Error</h4><pre>{_esc(str(row['error'])[:500])}</pre>"
        if row["output_preview"]:
            html += f"<h4>Output</h4><pre>{_esc(str(row['output_preview'])[:500])}</pre>"

        note = await client.create_note(parent_id, title, html)
        await client.create_label(note.note_id, "jobName", row["job_name"])
        await client.create_label(note.note_id, "success", str(row["success"]))

    logger.info("Uploaded %d cron executions", len(rows))


async def _upload_stats(client, parent_id):
    """Create an aggregate stats dashboard note."""
    stats = {}

    # Interaction stats
    db = DATA_DIR / "interaction_log.db"
    if db.exists():
        conn = sqlite3.connect(str(db))
        stats["interactions"] = conn.execute("SELECT COUNT(*) FROM interaction_log").fetchone()[0]
        stats["tier_breakdown"] = dict(conn.execute(
            "SELECT selected_tier, COUNT(*) FROM interaction_log GROUP BY selected_tier"
        ).fetchall())
        stats["domain_breakdown"] = dict(conn.execute(
            "SELECT domain, COUNT(*) FROM interaction_log GROUP BY domain"
        ).fetchall())
        stats["avg_latency"] = conn.execute(
            "SELECT AVG(latency_ms) FROM interaction_log WHERE latency_ms > 0"
        ).fetchone()[0] or 0
        stats["total_cost"] = conn.execute(
            "SELECT SUM(cost_usd) FROM interaction_log"
        ).fetchone()[0] or 0
        conn.close()

    # Distillation stats
    db = DATA_DIR / "distillation.db"
    if db.exists():
        conn = sqlite3.connect(str(db))
        stats["distillation_pairs"] = conn.execute("SELECT COUNT(*) FROM distillation_pairs").fetchone()[0]
        stats["avg_quality"] = conn.execute("SELECT AVG(quality_score) FROM distillation_pairs").fetchone()[0] or 0
        conn.close()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = f"""<h2>ABLE System Stats</h2>
<p><i>Generated: {now}</i></p>
<table>
<tr><td><b>Total Interactions</b></td><td>{stats.get('interactions', 0)}</td></tr>
<tr><td><b>Distillation Pairs</b></td><td>{stats.get('distillation_pairs', 0)}</td></tr>
<tr><td><b>Avg Latency</b></td><td>{stats.get('avg_latency', 0):.0f}ms</td></tr>
<tr><td><b>Total Cost</b></td><td>${stats.get('total_cost', 0):.4f}</td></tr>
<tr><td><b>Avg Quality Score</b></td><td>{stats.get('avg_quality', 0):.2f}</td></tr>
</table>
<h3>Tier Breakdown</h3><pre>{json.dumps(stats.get('tier_breakdown', {}), indent=2)}</pre>
<h3>Domain Breakdown</h3><pre>{json.dumps(stats.get('domain_breakdown', {}), indent=2)}</pre>"""

    note = await client.create_note(parent_id, f"System Dashboard — {now}", html)
    await client.create_label(note.note_id, "iconClass", "bx bx-dashboard")

    logger.info("Created stats dashboard")


async def _upload_architecture(client, parent_id):
    """Upload system architecture, routing logic, and module map as structured notes."""
    project_root = DATA_DIR.parent

    # 1. Routing Pipeline
    routing_html = """<h2>ABLE Routing Pipeline</h2>
<pre>
User Input → TrustGate → Scanner → Auditor → PromptEnricher → ComplexityScorer → Provider
                                                                      │
                                                           InteractionLogger → EvolutionDaemon
</pre>
<h3>Tier Mapping</h3>
<table>
<tr><th>Score Range</th><th>Tier</th><th>Provider</th><th>Cost</th></tr>
<tr><td>&lt; 0.4</td><td>T1</td><td>GPT 5.4 Mini (xhigh via OAuth)</td><td>$0</td></tr>
<tr><td>0.4 – 0.7</td><td>T2</td><td>GPT 5.4 (xhigh via OAuth)</td><td>$0</td></tr>
<tr><td>&gt; 0.7</td><td>T4</td><td>Claude Opus 4.6 (budget-gated)</td><td>$15/$75 per M</td></tr>
<tr><td>background</td><td>T3</td><td>MiniMax M2.7 (OpenRouter)</td><td>$0.30/$1.20 per M</td></tr>
<tr><td>offline</td><td>T5</td><td>Ollama Qwen 3.5 27B/9B</td><td>FREE</td></tr>
</table>
<h3>Key Files</h3>
<ul>
<li><code>able/core/routing/complexity_scorer.py</code> — Rule-based scoring (&lt;5ms)</li>
<li><code>able/core/routing/provider_registry.py</code> — 5-tier provider chain</li>
<li><code>able/core/routing/prompt_enricher.py</code> — Vague-to-actionable expansion</li>
<li><code>config/routing_config.yaml</code> — Provider registry config</li>
<li><code>config/scorer_weights.yaml</code> — M2.7-tunable weights</li>
</ul>"""
    n = await client.create_note(parent_id, "Routing Pipeline", routing_html)
    await client.create_label(n.note_id, "iconClass", "bx bx-git-branch")

    # 2. Security Stack
    security_html = """<h2>Security Architecture</h2>
<h3>Defense Layers</h3>
<ol>
<li><b>TrustGate</b> — Message trust scoring 0.0-1.0 (SAFE &gt;0.85, REJECT &lt;0.4)</li>
<li><b>CommandGuard</b> — Allowlist + dangerous pattern detection + YAML overrides</li>
<li><b>EgressInspector</b> — URL/IP/S3 exfiltration detection before shell execution</li>
<li><b>MalwareScanner</b> — Scans skills before registration</li>
<li><b>tool_permissions.yaml</b> — 3-tier: always_allow / ask_before / never_allow</li>
</ol>
<h3>Codex Cross-Audit</h3>
<p>Pre-deploy code review: codex CLI → claude CLI → rule-based static analysis.
Detects: secrets, SQL injection, eval/exec, bare excepts. Verdict: PASS/FAIL/PARTIAL.</p>
<h3>Key Files</h3>
<ul>
<li><code>able/core/security/trust_gate.py</code></li>
<li><code>able/core/security/command_guard.py</code></li>
<li><code>able/core/security/egress_inspector.py</code></li>
<li><code>able/tools/codex_audit.py</code></li>
<li><code>config/tool_permissions.yaml</code></li>
</ul>"""
    n = await client.create_note(parent_id, "Security Stack", security_html)
    await client.create_label(n.note_id, "iconClass", "bx bx-shield")

    # 3. Distillation Pipeline
    distillation_html = """<h2>Distillation Pipeline</h2>
<pre>
Harvest (8 sources) → Score → Filter → Export JSONL → Fine-tune (Unsloth/H100) → GGUF → Ollama
</pre>
<h3>Components</h3>
<table>
<tr><th>Component</th><th>File</th><th>Role</th></tr>
<tr><td>Confidence Scorer</td><td>confidence_scorer.py</td><td>0-1 score (real logprobs for Ollama, proxy for others)</td></tr>
<tr><td>Interaction Auditor</td><td>interaction_auditor.py</td><td>Per-interaction GEval + formatting + judge scoring</td></tr>
<tr><td>Conversation Evaluator</td><td>conversation_evaluator.py</td><td>Session-level eval + multi-turn DPO pairs</td></tr>
<tr><td>DPO Builder</td><td>dpo_builder.py</td><td>Turn-level + chain DPO pair export</td></tr>
<tr><td>Federation</td><td>federation/contributor.py</td><td>Cross-instance corpus sharing</td></tr>
</table>
<h3>Target Models</h3>
<ul>
<li>Server: Qwen 3.5 27B UD-Q4_K_XL (17.6GB)</li>
<li>Edge: Qwen 3.5 9B UD-IQ2_M (3.65GB) / Q4_K_XL (5.97GB)</li>
</ul>"""
    n = await client.create_note(parent_id, "Distillation Pipeline", distillation_html)
    await client.create_label(n.note_id, "iconClass", "bx bx-brain")

    # 4. Evolution Daemon
    evo_html = """<h2>Self-Evolution Daemon</h2>
<p>Background async daemon using M2.7 to continuously improve routing accuracy.</p>
<h3>5-Step Cycle (every 6 hours)</h3>
<ol>
<li><b>Collect</b> — Gather metrics from interaction log (24h window)</li>
<li><b>Analyze</b> — M2.7 pattern detection (or rule-based fallback)</li>
<li><b>Improve</b> — Generate bounded weight changes (max 20% per value)</li>
<li><b>Validate</b> — Sanity checks: bounds, rate limits, tier gap ≥0.15</li>
<li><b>Deploy</b> — Write scorer_weights.yaml, versioned backup, hot-reload</li>
</ol>
<h3>Safety Constraints</h3>
<ul>
<li>Max 20% change per weight per cycle</li>
<li>Weights stay in [0.0, 1.0]</li>
<li>Minimum 20 interactions required to trigger</li>
<li>All changes auditable via versioned backups</li>
</ul>"""
    n = await client.create_note(parent_id, "Evolution Daemon", evo_html)
    await client.create_label(n.note_id, "iconClass", "bx bx-refresh")

    # 5. Context Management (CVC)
    cvc_html = """<h2>CVC Context Management</h2>
<h3>Context Compactor</h3>
<p>When conversation reaches 80% of model context limit, summarizes oldest 60% via
extractive summarization. Preserves tool calls, user requests, key conclusions.</p>
<h3>Context Versioning (Merkle DAG)</h3>
<p>Saves SHA-256 snapshots of conversation state at decision boundaries.
Enables rollback if expensive model calls fail or return low confidence.</p>
<ul>
<li>Auto-snapshot before tier escalations (T1→T2, T2→T4)</li>
<li>Auto-snapshot before tool calls that modify state</li>
<li>Rollback restores full message history from snapshot</li>
</ul>
<h3>Key Files</h3>
<ul>
<li><code>able/core/session/context_compactor.py</code></li>
<li><code>able/core/session/context_versioning.py</code></li>
</ul>"""
    n = await client.create_note(parent_id, "CVC Context Management", cvc_html)
    await client.create_label(n.note_id, "iconClass", "bx bx-git-merge")

    # 6. Observability
    obs_html = """<h2>Observability Stack</h2>
<h3>Components</h3>
<table>
<tr><th>Tool</th><th>Purpose</th><th>Port</th></tr>
<tr><td>Arize Phoenix</td><td>LLM trace dashboard with OpenInference</td><td>6006</td></tr>
<tr><td>TriliumNext</td><td>Knowledge base / wiki for compiled knowledge</td><td>8081</td></tr>
<tr><td>ABLETracer</td><td>Span-based tracing with JSONL + OTel dual export</td><td>—</td></tr>
</table>
<h3>Telemetry Sources</h3>
<ul>
<li>interaction_log.db — all routed requests</li>
<li>traces.jsonl — raw ABLETracer spans</li>
<li>batch_trajectories.jsonl — synthetic training runs</li>
<li>distillation.db — curated training pairs</li>
<li>cron_executions.db — scheduler job history</li>
<li>evolution_cycles/ — weight tuning snapshots</li>
<li>research_reports/ — weekly research findings</li>
</ul>
<h3>Phoenix Replay</h3>
<p>Idempotent replay system sends historical data to Phoenix with state tracking.
Tracks last-replayed timestamp per source to prevent duplicate spans.</p>"""
    n = await client.create_note(parent_id, "Observability Stack", obs_html)
    await client.create_label(n.note_id, "iconClass", "bx bx-line-chart")

    # 7. Module Map
    modules_html = """<h2>ABLE Module Map</h2>
<pre>
able/
├── core/
│   ├── gateway/          ← HTTP + Telegram entry points
│   ├── routing/          ← Complexity scorer, provider registry, enricher
│   ├── evolution/        ← Self-evolving daemon, weekly research
│   ├── providers/        ← OpenAI, Anthropic, OpenRouter, NIM, Ollama
│   ├── security/         ← TrustGate, CommandGuard, EgressInspector
│   ├── session/          ← Context compactor, versioning (CVC)
│   ├── distillation/     ← Training pipeline, harvesters, DPO builder
│   ├── federation/       ← Cross-instance corpus sharing
│   ├── observability/    ← Phoenix replay, tracer, instrumentors
│   ├── swarm/            ← Agent swarm coordination
│   └── agents/           ← Scanner, Auditor, Executor
├── tools/
│   ├── trilium/          ← TriliumNext ETAPI client + wiki skill
│   ├── codex_audit.py    ← Cross-audit with codex/claude/rules
│   ├── search/           ← DuckDuckGo, Brave, Perplexity
│   ├── shell/            ← Secure shell with egress inspection
│   └── mcp/              ← MCP server bridge
├── skills/               ← 25+ registered skills
├── memory/               ← SQLite + vector + knowledge graph
├── scheduler/            ← Cron with ABLE jobs
└── channels/             ← Telegram, Discord, Slack adapters
</pre>"""
    n = await client.create_note(parent_id, "Module Map", modules_html)
    await client.create_label(n.note_id, "iconClass", "bx bx-sitemap")

    logger.info("Uploaded architecture & logic documentation")


def _esc(text: str) -> str:
    """HTML-escape text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def main():
    from able.tools.trilium.client import TriliumClient
    logging.basicConfig(level=logging.INFO)

    async with TriliumClient() as client:
        if not await client.is_available():
            print("TriliumNext not available at", client.base_url)
            return
        cats = await upload_all(client)
        print(f"Upload complete. Categories: {list(cats.keys())}")


if __name__ == "__main__":
    asyncio.run(main())
