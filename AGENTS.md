# AGENTS.md — Codex Handoff for ABLE

> Read this file when starting a session on this repo. It tells you what ABLE is,
> what was just shipped, what to verify, and what to work on next.
> For full architecture details, see CODE_HANDOFF.md.

## What is ABLE?

**ABLE** (Autonomous Business & Learning Engine) is a Python asyncio service that runs:
- A **Telegram bot** (gateway) with OpenAI function-calling tool dispatch
- A **cron scheduler** with 15+ autonomous jobs (buddy care, distillation, evolution, research, wiki lint)
- **5-tier LLM routing** — complexity-scored requests -> cheapest capable model
- A **buddy companion system** (virtual pet with XP, evolution, needs)
- A **distillation pipeline** — harvests training data from 13 sources for local fine-tuning
- An **evolution daemon** — background process that auto-tunes routing weights
- A **gateway robustness stack** — context compaction, tool result persistence, activity timeout, execution monitoring

**Repo**: `github.com/iamthetonyb/ablemon`
**Deploy**: Push to `main` -> GitHub Actions builds Docker image -> pushes to GHCR -> deploys to DO server via SSH
**Entry point**: `able/start.py` -> `ABLEGateway().run()`

## Architecture

```
User (Telegram/CLI/Studio) -> Gateway -> TrustGate -> Scanner -> Enricher -> ComplexityScorer -> Provider
                                 |                                              |
                           ToolRegistry.dispatch()                    T1: GPT 5.4 Mini ($0 OAuth)
                                 |                                    T2: GPT 5.4 ($0 OAuth)
                           23 tools registered                        T3: MiniMax M2.7 (background)
                           (buddy, tenant, github,                    T4: Claude Opus 4.6 (premium)
                            infra, web tools)                         T5: Ollama local (free)
```

### Gateway Robustness Stack (shipped 2026-04-09)

| Layer | Component | Purpose |
|-------|-----------|---------|
| Context | `ContextCompactor` | Strip-thinking + extractive summary at 80% capacity, death spiral prevention (max 3 attempts) |
| Context | `ToolResultStorage` | 3-layer large output defense: self-truncate -> persist-to-disk -> enforce-turn-budget |
| Progress | `ExecutionMonitor` | Spinning/thrashing/error-loop detection (<1ms heuristics) |
| Progress | Repeated call guard | Pre-dispatch fingerprint check blocks identical consecutive tool calls |
| Timeout | Activity-based | 20-iteration budget, idle pressure at 60s, extends for active agents |
| Recovery | Thinking prefill | Re-run when model produces thinking but no output (max 2 retries) |
| Recovery | 413 auto-compress | Catch provider context-length errors, auto-compact, retry |
| Notification | Completion queue | Cron jobs with `notify_on_complete` push results to gateway |

### Key directories

- `able/core/gateway/` — Telegram handler, tool definitions, tool registry, execution monitor, tool result storage
- `able/core/routing/` — Complexity scorer, provider registry, prompt enricher
- `able/core/buddy/` — Companion system (model, renderer, XP, battles)
- `able/core/evolution/` — Self-evolving routing weight daemon + cumulative research
- `able/core/distillation/` — Training data harvesting + export (13 source adapters)
- `able/core/providers/` — OpenAI OAuth, Anthropic, OpenRouter, NIM, Ollama
- `able/core/session/` — Context compactor, session versioning (Merkle DAG)
- `able/scheduler/cron.py` — CronScheduler with 15+ default jobs + background notification queue
- `able/memory/` — SQLite + vector hybrid memory, research index (FTS5 + BM25)
- `able/tools/` — Browser, search, GitHub, DigitalOcean, Vercel, Trilium, XCrawl, Graphify

### Docker profiles

- **slim** (default, ~350MB): Gateway + Telegram + cron + LLM routing + buddy
- **full** (~2GB+): + playwright, sentence-transformers, stripe billing
- **observability**: + Phoenix (tracing) + TriliumNext (knowledge base)

### Config files

- `config/routing_config.yaml` — 5-tier provider definitions + budget caps
- `config/scorer_weights.yaml` — Complexity scoring weights (evolution-tuned, versioned)
- `config/tool_permissions.yaml` — always_allow / ask_before / never_allow tool policies
- `able/.env` / `able/.env.example` — All env vars (API keys, tokens)

## What Was Just Shipped (2026-04-09)

### Plan Items 1-3: TurboQuant + Gemma 4 Distillation + DeepTeam (aeac30f)

1. **TurboQuant KV cache** — q4_0 KV + flash_attention on all Gemma 4 Modelfiles (~2x context at same VRAM). `kv_cache_config.py` recommender generates strategy per model/VRAM. Turbo variant Modelfile for aggressive KV.
2. **Gemma 4 distillation** — `able-gemma4-31b` (server, 22GB QLoRA) and `able-gemma4-e4b` (edge, 10GB QLoRA on free T4) in MODEL_REGISTRY. Unsloth LoRA defaults (r=8, alpha=8), GGUF quant targets, start_of_turn/end_of_turn chat template in quantizer + exporter.
3. **DeepTeam red teaming** — `deepteam_bridge.py` wraps ABLE gateway as `model_callback` for 50+ vulnerability categories (prompt injection, PII leakage, excessive agency, SSRF, etc.). Wired into `self_pentest.py` (gated by `ABLE_ENABLE_DEEPTEAM=1`) + weekly cron at Sunday 4am.

### Phase 0/1 Gateway Robustness (8 components, 8430245)

All wired into the gateway tool loop (`gateway.py` lines ~1090-1620). Context compactor, tool result persistence, activity timeout, repeated call guard, thinking prefill, background notifications, 413 auto-compress, disconnect reclassification.

### Test results: 828 passing, 0 failures (3 pre-existing test issues fixed this session).

### P0 — Phase 1 Item 5: Background Process Notifications

- Already shipped as part of gateway robustness stack
- `CronScheduler.completion_queue` + `_drain_completion_queue()` in gateway

### P1 — Phase 2 Architecture (Plan Items 6-10) ✓ ALL DONE

- ✓ Durable task execution framework (iteration-commit-rollback from gnhf)
- ✓ Managed Agents provider ($0.08/session-hr, SSE streaming, lossless reconnect)
- ✓ SSRF hardening (CGNAT, tar traversal, DNS rebinding, cloud metadata)
- ✓ Structured agent handoffs (Three Man Team file-based artifacts)
- ✓ Self-diagnosing behavioral benchmarks (per-model-family guidance, 10 probes, 5 failure modes)

### P2 — Production verification

- Confirm deployed container sees `/home/able/.able/auth.json`
- Confirm T1 resolves to `gpt-5.4-mini` on live server
- Send a real Telegram buddy query and verify dispatch
- Confirm CI smoke stays green
- Confirm cron duplicate-fire hardening on server: `able/data/cron_executions.db` is on the Docker `able_db` volume and `job_run_claims` contains one row per scheduled run slot (especially `nightly-research`)

Full plan: see `adaptive-purring-kernighan.md` in `.claude/plans/`

## Co-Working Protocol

1. **Claude Code** handles architecture, multi-file refactors, deploy pipeline, deep debugging
2. **Codex** handles verification, testing, isolated feature work, PR reviews
3. Both read this file and CODE_HANDOFF.md for context
4. Commit messages: `type: description` (fix, feat, perf, docs, chore)
5. All commits co-authored
6. Push to `main` triggers deploy — verify workflows pass before pushing

## Quick Verification

```bash
able --help                                     # Global command works
able chat --help                                # Chat subcommand
python3 -m pytest able/tests/ -x --timeout=60   # Full test suite
docker compose build && docker compose up -d    # Docker builds + starts
curl http://localhost:8080/health                # Health check
```

## Environment

- **Python**: 3.11+ (3.14 in Docker)
- **Server**: Digital Ocean (146.190.142.68)
- **Registry**: ghcr.io/iamthetonyb/able-gateway
- **Bot**: @ABLEmonBot on Telegram
- **Owner Telegram ID**: `ABLE_OWNER_TELEGRAM_ID` secret
- **OAuth**: `~/.able/auth.json` (local) / `OPENAI_OAUTH_AUTH_JSON` (GitHub secret)
