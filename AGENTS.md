# AGENTS.md — Codex Handoff for ABLE

> Read this file when starting a session on this repo. It tells you what ABLE is,
> what was just shipped, what to verify, and what to work on next.

## What is ABLE?

**ABLE** (Autonomous Business & Learning Engine) is a Python asyncio service that runs:
- A **Telegram bot** (gateway) with OpenAI function-calling tool dispatch
- A **cron scheduler** with 12+ autonomous jobs (buddy care, memory consolidation, etc.)
- **5-tier LLM routing** — complexity-scored requests → cheapest capable model
- A **buddy companion system** (virtual pet with XP, evolution, needs)
- A **distillation pipeline** — harvests training data for local fine-tuning
- An **evolution daemon** — background process that auto-tunes routing weights

**Repo**: `github.com/iamthetonyb/ablemon`  
**Deploy**: Push to `main` → GitHub Actions builds Docker image → pushes to GHCR → deploys to DO server via SSH  
**Entry point**: `able/start.py` → `ABLEGateway().run()`

## Architecture

```
User (Telegram) → Gateway → TrustGate → Scanner → Enricher → ComplexityScorer → Provider
                    ↓                                              ↓
              ToolRegistry.dispatch()                    T1: GPT 5.4 Mini ($0 OAuth)
                    ↓                                    T2: GPT 5.4 ($0 OAuth)
              23 tools registered                        T3: MiniMax M2.7 (background)
              (buddy, tenant, github,                    T4: Claude Opus 4.6 (premium)
               infra, web tools)                         T5: Ollama local (free)
```

### Key directories
- `able/core/gateway/` — Telegram handler, tool definitions, tool registry
- `able/core/routing/` — Complexity scorer, provider registry, prompt enricher
- `able/core/buddy/` — Companion system (model, renderer, XP, battles)
- `able/core/evolution/` — Self-evolving routing weight daemon
- `able/core/distillation/` — Training data harvesting + export
- `able/core/providers/` — OpenAI OAuth, Anthropic, OpenRouter, NIM, Ollama
- `able/scheduler/cron.py` — CronScheduler with 12 default jobs
- `able/memory/embeddings/` — Vector store with auto provider (openai → ollama → hash)
- `able/tools/` — Browser, search, GitHub, DigitalOcean, Vercel, webhooks

### Docker profiles
- **slim** (default, ~350MB): Gateway + Telegram + cron + LLM routing + buddy
- **full** (~2GB+): + playwright, sentence-transformers, stripe billing

### Config files
- `config/routing_config.yaml` — 5-tier provider definitions
- `config/scorer_weights.yaml` — Complexity scoring weights (M2.7-tunable)
- `able/.env` / `able/.env.example` — All env vars (API keys, tokens)

## What Was Just Shipped (2026-04-07) — Execution Monitor, RustPython Research

### Execution Monitor (PentAGI-Inspired)

- **New file**: `able/core/gateway/execution_monitor.py` — pure heuristic progress analysis for the tool loop
- **Wired into gateway.py**: Records every tool call, analyzes after each iteration, injects targeted intervention messages
- Detects: spinning (same tool+args 3x), thrashing (A-B-A-B 4x), output repetition (>70% Jaccard similarity), error loops (3+ consecutive failures)
- When `should_terminate`: breaks the tool loop immediately (spinning >8 iters, errors >10 iters)
- Complements Hermes budget pressure — monitor is targeted ("you're stuck on web_search"), budget is blunt ("stop calling tools")

### RustPython WASM Research

- Investigated RustPython for sandboxed skill execution (WASM + WASI target)
- Key finding: WASI + wasmtime gives memory caps, CPU fuel limits, no filesystem/network unless explicitly mapped
- RustPython supports `Interpreter::without_stdlib()` for zero-capability builds, feature flags gate `os`/`socket`/`subprocess`
- Performance: ~5-15x slower than CPython for tight loops, adequate for sandboxed skill scripts
- Practical path: compile RustPython to WASI → run from Python 3.14 via wasmtime-py

---

## What Was Just Shipped (2026-04-06) — Research Pipeline, Knowledge Base, Security Hardening, Edge Compute

### Research Pipeline Overhaul (Karpathy LLM Wiki + Feynman + OMEGA)

- **6-phase cumulative research** (`weekly_research.py`): Follow-up → open questions → stale rotation → goal-aware → system evolution auto-discovery → growth mining. Each run builds on the last 10 reports.
- **XCrawl integration** (`able/tools/xcrawl/client.py`): Full structured content extraction for high-priority findings (replaces snippet-only search results)
- **Source grounding** (`able/core/evolution/source_grounder.py`): Feynman pattern — HEAD request URL verification + secondary search cross-verification → `#verified`/`#broken-link`/`#contested` tags
- **Knowledge graph** (`able/tools/graphify/builder.py`): NetworkX + Louvain community detection → interactive D3 HTML visualization + mermaid diagrams for Trilium
- **Semantic search index** (`able/memory/research_index.py`): SQLite FTS5 + BM25 + recency/relevance/verification boost — scales wiki queries without loading index into context
- **Wiki lint** (`able/tools/trilium/wiki_lint.py`): Weekly quality check — orphans, stale notes, duplicates, missing sources, low confidence → auto-filed to Trilium
- **Deep research skill** (`able/skills/library/deep-research/`): Multi-agent with source grounding, XCrawl, knowledge graph, Trilium filing

### TriliumNext Knowledge Base

- Docker service in `observability` profile (port 8081)
- ETAPI client with async context manager, search, CRUD, attributes, relations
- Wiki skill: `/wiki search`, `/wiki add`, `/wiki recent`, `/wiki clips`
- Historic upload: architecture docs, provider configs, evolution history
- Research ingestion: per-finding notes with mermaid topic map + cross-references + web clipper linking
- Cron: `wiki-lint` Sundays at 11am (after weekly research)

### Phoenix Observability Fixes

- **Idempotent replay** (`phoenix_replay.py`): State tracking via `data/.phoenix_replay_state.json` — no duplicate spans
- **Cron tracer fix** (`tracer.py`): Properly detects no-op provider (was always returning True). Cron scheduler now explicitly initializes Phoenix at startup.
- Added `--force` and `--clear-project` flags to replay CLI

### Security Hardening

- **Egress inspector** (`egress_inspector.py`): Pre-hook URL/IP extraction before CommandGuard
- **YAML tool permissions** (`config/tool_permissions.yaml`): `always_allow`/`ask_before`/`never_allow` sections
- **Provider smoke test** (`provider_registry.py`): Canary "ABLE_OK" verification per provider
- **Codex cross-audit** (`codex_audit.py`): 3-layer fallback (codex → claude → rule-based), rules always supplement
- **Interaction auditor**: Verification agent adversarial probes + VERDICT field

### CVC Context Management

- **Context compactor** (`context_compactor.py`): Summarizes oldest 60% at 80% window capacity via T1
- **Session versioning** (`context_versioning.py`): Merkle DAG snapshots with branch-on-escalation rollback

### Edge Inference & Distributed Compute

- **ANE optimizer** (`ane_optimizer.py`): Per-chip M1-M4 profiles, battery-aware routing (ANE prefill + GPU decode)
- **Compute mesh** (`compute_mesh.py`): mDNS discovery, capability reporting, idle-aware job scheduling

### Key Files

| File | What |
|------|------|
| `able/tools/trilium/client.py` | TriliumNext ETAPI client |
| `able/tools/xcrawl/client.py` | XCrawl structured scraping |
| `able/tools/graphify/builder.py` | Knowledge graph builder |
| `able/core/evolution/source_grounder.py` | Feynman source verification |
| `able/memory/research_index.py` | FTS5 semantic search index |
| `able/tools/trilium/wiki_lint.py` | Wiki quality checker |
| `able/core/providers/ane_optimizer.py` | Apple Silicon optimizer |
| `able/core/federation/compute_mesh.py` | Distributed training mesh |

---

## What Was Just Shipped (2026-04-04 session 2) — Gemma 4, Hermes patterns, Phoenix dashboard

### Model Upgrades

- **T5 primary**: Gemma 4 31B replaces Qwen 3.5 27B. Better on reasoning/coding, Apache 2.0, day-0 Ollama. `ollama pull gemma4:31b`. New Modelfile at `config/ollama/Modelfile.gemma4-31b` (Gemma 4 uses different chat template — `start_of_turn/end_of_turn`, not ChatML).
- **T1 fallback**: Gemma 4 31B on NIM (free tier) replaces Nemotron 120B (was scoring 1/5). Run `able/evals/` to validate before treating as default.
- **T2 fallback A**: Gemma 4 26B A4B on OpenRouter added ($0.13/$0.40/M — 8x cheaper than MiMo). MiMo retained as fallback B.
- **Qwen 3.5 9B** stays as edge/multilingual T5 — Qwen wins on multilingual and is already fine-tuned.
- **Qwen 3.6**: API-only, closed weights — do not adopt. Watch `QwenLM/Qwen3.6` HF org for open-weight release.

### Phoenix Observability Dashboard

- `docker-compose.yml`: Added `phoenix` service under `--profile observability` (`arizephoenix/phoenix:latest`, ports 6006/4317).
- `phoenix_setup.py`: Now connects to external Phoenix server (env var `PHOENIX_COLLECTOR_ENDPOINT`) instead of trying to launch inline. Correct for Docker Compose peer-service pattern.
- `interaction_auditor.py`: Hardcoded endpoint strings replaced with `_PHOENIX_ENDPOINT` constant (reads from env var).
- `docs/PHOENIX.md`: Updated to `docker compose --profile observability up -d` setup.

**To start**: `docker compose --profile observability up -d` → **http://localhost:6006**

### Hermes Agent Patterns (NousResearch research)

Four patterns from `NousResearch/hermes-agent` integrated:

1. **Iteration budget pressure** (`gateway.py`): At ≥12/15 tool iterations (80%), injects `[⚠️ BUDGET: N remaining. Stop calling tools. Synthesize final answer NOW.]` into the tool result. Prevents runaway tool loops.
2. **GOAP tool planning directive** (`gateway.py` system prompt): "Multi-Step Tool Planning" section — model plans goal + call sequence + stopping condition before first tool call.
3. **Tool availability checks** (`tool_registry.py`): `ToolDef.availability_check: Optional[Callable[[], bool]]`. `get_definitions()` runs it per tool, silently excludes tools whose deps (env vars, services) aren't available. Prevents hallucinated calls.
4. **Availability checks wired** to `github_tools.py` (`GITHUB_TOKEN`/`GH_TOKEN`) and `infra_tools.py` (`DO_API_KEY`, `VERCEL_TOKEN`).

### Still To Do (Hermes — Medium Effort)

- **Context compression middleware**: Port `ContextCompressor` from hermes-agent (~200 lines). 5-phase: prune old tool outputs → protect head/tail → summarize middle via cheap model → reassemble → sanitize orphaned tool pairs. Wire into provider base class. Needed for long Telegram sessions.
- **Batch trajectory generator**: Run ABLE's own stack against a prompt dataset in parallel, capture ChatML trajectories → feed into DPO builder. Accelerates distillation corpus from ~20 → hundreds of pairs.
- **Autonomous skill creation**: After 5+ tool-call tasks, trigger a post-task skill serialization step.

### Root Character + UI fixes (session 1)

- Root poses redesigned (`renderer.py`): `╲│╱` tree-branch arms, sway shifts whole design as unit
- Shimmer minimum 2.5s display (`chat.py`): `asyncio.gather(sleep(2.5), gateway_task)`
- Tagline double-quote fix (`renderer.py`): `catch_phrase.strip('"\'')` before wrapping

---

## What Was Just Shipped (2026-04-04) — RLHF + Distillation Pipeline

### Conversation-Chain Evaluation + Full-Signal RLHF

**Goal**: Capture the entire reasoning + prompt + response loop as training data — including when the user had to guide the model, why, and wins where the AI nailed it first try.

#### New files
- `able/core/distillation/conversation_evaluator.py` — `ConversationChainEvaluator`
  - Groups interaction_log turns by `session_id` into full conversations
  - Metrics per session: `win_rate`, `guidance_ratio`, `reasoning_depth`, `coherence_score`, `session_quality`
  - Builds multi-turn DPO pairs in ChatML format: first guidance moment = rejected, best win = chosen, prior turns = prompt context
  - Output: `data/distillation_conv_dpo.jsonl` with `chosen_thinking`, `rejected_thinking`, `guidance_correction`

#### Updated files
- `able/core/routing/interaction_log.py` — 3 new schema columns + 3 new methods
  - New columns: `guidance_needed REAL` (0.0=win, 0.5=partial, 1.0=full correction), `tools_called TEXT` (JSON, real executed tools only), `conversation_depth INTEGER`
  - New methods: `set_guidance_signal()`, `get_session_turns()`, `get_recent_sessions()`
  - `update_result()` now accepts `tools_called` and `conversation_depth`

- `able/core/gateway/gateway.py` — Populates real tool signals in interaction log
  - `tools_called`: derived from `_tool_calls_log` (gateway execution loop — **actual tools that ran**, not model declarations)
  - `conversation_depth`: count of prior outbound turns from transcript history
  - **Claude and models emit synthetic tool_call declarations that never execute** — gateway's `_tool_calls_log` is the authoritative real-execution source

- `able/core/distillation/dpo_builder.py` — Two new methods
  - `build_conversation_pairs(since_hours)` — calls `ConversationChainEvaluator`, returns multi-turn pairs
  - `export_conversation_jsonl(output_path, since_hours)` — writes to JSONL
  - Import: `ConversationChainEvaluator` added at top

- `able/core/distillation/interaction_auditor.py` — deepeval-inspired GEval metrics
  - `_routing_accuracy_score(row)` — was the right tier selected? (complexity_score vs tier vs audit_score)
  - `_tool_correctness_score(row)` — did real executed tools match domain? (uses tools_called, NOT synthetic declarations)
  - Both added to `audit_notes` JSON + Phoenix span attributes
  - **Stack roles preserved**: Phoenix=observability, promptfoo=regression evals, unsloth=training, GEval=per-interaction scoring dimensions

- `able/scheduler/cron.py` — New cron job
  - `conversation-eval` at `0 2,6,10,14,18,22 * * *` (every 4h offset from `interaction-audit` at `0 */4`)
  - Calls `DPOBuilder().export_conversation_jsonl()`, awards buddy XP per pair

### Verification checklist

```bash
# 1. New schema columns applied
python -c "
import sqlite3
conn = sqlite3.connect('data/interaction_log.db')
cols = [r[1] for r in conn.execute(\"PRAGMA table_info(interaction_log)\")]
expected = ['guidance_needed','tools_called','conversation_depth','response_confidence']
for c in expected:
    assert c in cols, f'missing {c}'
print('Schema OK:', [c for c in cols if c in expected])
"

# 2. Conversation evaluator imports clean
python -c "
from able.core.distillation.conversation_evaluator import ConversationChainEvaluator
from able.core.distillation.dpo_builder import DPOBuilder
e = ConversationChainEvaluator()
b = DPOBuilder()
print('ConversationChainEvaluator OK')
print('DPOBuilder.build_conversation_pairs:', hasattr(b, 'build_conversation_pairs'))
"

# 3. Cron job registered
python -c "
from able.scheduler.cron import CronScheduler, register_default_jobs
sched = CronScheduler()
register_default_jobs(sched)
assert 'conversation-eval' in sched.jobs, 'missing conversation-eval job'
print('conversation-eval job at:', sched.jobs['conversation-eval'].schedule)
"

# 4. GEval metrics compute without error
python -c "
from able.core.distillation.interaction_auditor import _routing_accuracy_score, _tool_correctness_score
row = {'complexity_score': 0.6, 'selected_tier': 2, 'audit_score': 4.2, 'domain': 'coding', 'tools_called': '[\"bash\",\"read_file\"]'}
print('routing_accuracy:', _routing_accuracy_score(row))
print('tool_correctness:', _tool_correctness_score(row))
"

# 5. Confidence scorer works across provider types
python -c "
from able.core.distillation.confidence_scorer import score_response_confidence, build_domain_confidence_profile
# GPT proxy
row_gpt = {'actual_provider': 'gpt-5.4-mini', 'raw_input': 'how do i write a for loop in python?', 'raw_output': 'Here is a for loop example...' * 10, 'thinking_content': 'The user wants a simple example. Let me consider the best approach...', 'complexity_score': 0.3, 'guidance_needed': 0.0, 'audit_score': 4.5}
print('GPT confidence:', score_response_confidence(row_gpt))
# Ollama real logprobs
from able.core.distillation.confidence_scorer import extract_ollama_logprob_confidence
import math
fake_logprobs = [math.log(0.95), math.log(0.88), math.log(0.72), math.log(0.91)]
print('Ollama logprob confidence:', extract_ollama_logprob_confidence(fake_logprobs))
# Domain profile
rows = [{'domain': 'security', 'complexity_score': 0.8, 'thinking_content': 'I need to carefully analyze...', 'raw_input': 'audit this', 'raw_output': 'analysis result' * 50, 'guidance_needed': 0.0, 'audit_score': 4.8, 'actual_provider': 'claude'}]
profile = build_domain_confidence_profile(rows)
print('Domain profile:', profile)
"

# 6. Buddy level seeding (dry run with no DB — should not crash)
python -c "
from able.core.buddy.xp import seed_buddy_level_from_harvest
result = seed_buddy_level_from_harvest()
print('Seed result (None if no buddy):', result)
"
```

---

## What Was Just Shipped (2026-04-03)

### Fixes
1. **Buddy tools for Telegram** — Created `able/core/gateway/tool_defs/buddy_tools.py` with 3 tools (buddy_status, buddy_feed, buddy_backpack). "How's Groot?" now routes correctly instead of hitting tenant_status.
2. **Playwright graceful degradation** — `able/tools/browser/automation.py` returns error responses instead of crashing with RuntimeError when playwright isn't installed.
3. **All stale ATLAS/AIDE/able-v2 references purged** — Zero remaining across entire codebase.
4. **Env vars renamed** — `ATLAS_OWNER_TELEGRAM_ID` → `ABLE_OWNER_TELEGRAM_ID`, `ATLAS_TIMEZONE` → `ABLE_TIMEZONE`
5. **Deploy workflow fixed** — Uses /health polling instead of unreliable `docker compose ps` grep. Kills port 8080 before starting. Both workflows green.
6. **Slim runtime OAuth fix** — `requests` is back in `requirements-core.txt`, so Docker can import the OpenAI OAuth provider in the default runtime path.
7. **Container config fix** — Docker builds now use the repo root as build context and copy the shared `config/` tree into the image, so the gateway uses `config/routing_config.yaml` instead of silently falling back to the legacy provider chain.
8. **OAuth token visibility fix** — Deploy scripts mount `auth.json` into `/home/able/.able/auth.json` and set host ownership to `1000:1000`, matching the container's `able` user. Without that, the token was present but unreadable.
9. **Buddy dispatch regression test** — Added a Telegram-path integration test that drives `process_message(..., update=...)` and verifies `buddy_status` dispatches instead of `tenant_status`.
10. **CI gateway smoke** — Added `.github/workflows/gateway-health-smoke.yml` to build the image, start the gateway, check `/health`, and assert the routing config exists in the container.

### New infrastructure
1. **Docker multi-stage Dockerfile** with slim/full profiles
2. **GHCR-based CI/CD** — `.github/workflows/deploy.yml` builds → pushes to `ghcr.io/iamthetonyb/able-gateway` → deploys via SSH
3. **Root `docker-compose.yml`** for local dev
4. **`deploy-to-server.sh`** for manual deploys to any server
5. **Split requirements** — `requirements-core.txt` (8 packages) and `requirements-full.txt`

### Optimizations
1. **Dropped psutil from core** — Replaced with `/proc` reads (zero deps, works in Docker)
2. **Ollama embedding provider** — Free semantic search without sentence-transformers
3. **Auto embedding mode** — Tries openai → ollama → sentence-transformers → hash fallback
4. **OAuth token deployed** — `OPENAI_OAUTH_AUTH_JSON` GitHub secret set, $0 GPT routing active

## Verification Checklist

Run these to verify everything is solid:

```bash
# 1. Docker builds successfully
cd /path/to/ABLE
docker compose build

# 2. Container starts and health passes
docker compose up -d
sleep 10
curl http://localhost:8080/health

# 3. Python imports are clean and the default registry boots
docker compose exec able python -c "
from able.core.gateway.gateway import ABLEGateway, ABLE_SYSTEM_PROMPT
from able.core.gateway.tool_registry import ToolRegistry, build_default_registry
from able.core.gateway.tool_defs import buddy_tools, tenant_tools, github_tools
from able.scheduler.cron import CronScheduler, register_default_jobs
from able.core.buddy.model import load_buddy
from able.memory.embeddings.vector_store import VectorStore
print('All imports OK')
reg = build_default_registry()
print(f'Tools registered: {reg.tool_count}')
assert 'buddy_status' in ABLE_SYSTEM_PROMPT
assert 'buddy_feed' in ABLE_SYSTEM_PROMPT
"

# 4. Tool count is 23 (20 original + 3 buddy tools)
# Expected output: Tools registered: 23

# 5. No stale references
grep -r "ATLAS_\|AIDE\|able-v2" able/ --include="*.py" --include="*.yml" --include="*.yaml" --include="*.sh"
# Expected: no output

# 6. Embedding auto-provider works
docker compose exec able python -c "
from able.memory.embeddings.vector_store import VectorStore
from pathlib import Path
vs = VectorStore(Path('/tmp/test_vectors.bin'))
print(f'Provider: {vs.embedding_provider}')
emb = vs.compute_embedding('test query')
print(f'Embedding dim: {len(emb)}, non-zero: {sum(1 for x in emb if x != 0.0)}')
"

# 7. Cron jobs all registered
docker compose exec able python -c "
from able.scheduler.cron import CronScheduler, register_default_jobs
sched = CronScheduler()
register_default_jobs(sched)
print(f'Cron jobs registered: {len(sched.jobs)}')
for name, job in sched.jobs.items():
    print(f'  {name}: {job.schedule}')
"
```

## What's Next (Priority Order)

### P0 — Verify in production
- [ ] Telegram bot responds to "How's Groot?" with buddy status (not tenant error)
- [ ] Live server confirms GPT 5.4 Mini (OAuth) is the primary model when the deployed auth token is present
- [ ] Cron jobs are running (check `cron_executions.db` after 24h)
- [ ] Health endpoint returns 200

### P1 — Robustness
- [ ] Run pentest scanner again (`python -m able.security.pentest`) — verify ABLE_ env vars detected
- [x] Add integration test for buddy tool dispatch (mock Telegram update → verify buddy_status called)
- [x] Add smoke test to CI that starts gateway briefly and checks /health

### P2 — Features
- [x] gstack integration — learnings harvester, buddy XP from sprint events, cron job
- [ ] Buddy evolution triggers (battle system, stage progression)
- [ ] Federation network for distillation (cross-instance corpus sharing)
- [ ] ABLE Studio dashboard connected to live gateway
- [ ] Multi-user support (Telegram group handling, per-user buddy instances)

### P3 — Performance
- [ ] Alpine base image for Docker (saves ~50MB)
- [ ] Ollama `nomic-embed-text` setup guide for free semantic memory
- [ ] Eval suite for routing accuracy (`able/evals/`)
- [ ] Evolution daemon running on schedule (verify M2.7 weight tuning)

## Co-Working Protocol

When Claude Code and Codex work on this repo:
1. **Claude Code** handles architecture, multi-file refactors, deploy pipeline, and deep debugging
2. **Codex** handles verification, testing, isolated feature work, and PR reviews
3. Both should read this file and CLAUDE.md/ABLE.md for context
4. Commit messages follow: `type: description` (fix, feat, perf, docs, chore)
5. All commits co-authored: `Co-Authored-By: <agent> <noreply@anthropic.com>` or `Co-Authored-By: <agent> <noreply@openai.com>`
6. Push to `main` triggers deploy — verify workflows pass before pushing

## Environment

- **Python**: 3.14 (in Docker)
- **Server**: Digital Ocean (146.190.142.68)
- **Registry**: ghcr.io/iamthetonyb/able-gateway
- **Bot**: @ABLEmonBot on Telegram
- **Owner Telegram ID**: Set via `ABLE_OWNER_TELEGRAM_ID` secret
- **OAuth**: `~/.able/auth.json` (local) / `OPENAI_OAUTH_AUTH_JSON` (GitHub secret)
