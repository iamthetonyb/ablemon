# Able Your AI To Get Smarter Every Day

ABLE is an AI agent that lives on your server, talks to you on Telegram, and **learns from every conversation**.

The more you use it, the sharper it gets — routing smarter, answering faster, building training data from your real work to fine-tune local models that cost you nothing to run.

---

## What makes ABLE different

**It routes to the cheapest model that can handle the job.**  
Simple questions hit GPT 5.4 Mini through your ChatGPT subscription — $0 per token. Complex tasks escalate. You never pay for more than you need.

**It learns from your real interactions.**  
Every conversation is evaluated, scored, and turned into training data. Wins get reinforced. Corrections get captured. Over time it builds a fine-tuned local model that sounds like your actual use case.

**It doesn't crash when conversations get long.**  
A 10-layer robustness stack handles context overflow, stuck tool loops, and provider disconnects automatically. Thinking blocks get compressed (not stripped) to preserve training data while freeing context. Large outputs get persisted to disk, idle agents get pressure, spinning agents get killed. You don't notice any of it.

**It has a companion that grows with it.**  
Your buddy starts at a level based on how you already use AI. Feed it, train it, watch it evolve. It's not decorative — it reflects real system activity.

**It shares (safely) and gets shared with.**  
A federation network exchanges anonymized high-quality pairs between ABLE instances. Your system benefits from everyone's best conversations. PII is stripped before anything leaves.

**It secures itself.**  
Every message scores through a TrustGate before execution. Commands go through a CommandGuard with YAML-configurable policies. Egress inspection catches data exfiltration. Tool permissions are three-tier: always allow, ask first, never allow.

---

## 5 minutes to running

```bash
# 1. Clone
git clone https://github.com/iamthetonyb/ablemon.git && cd ablemon

# 2. Configure
cp able/.env.example able/.env
# Open able/.env — add TELEGRAM_BOT_TOKEN and ABLE_OWNER_TELEGRAM_ID

# 3. Start
docker compose up -d

# 4. Confirm it's alive
curl http://localhost:8080/health
```

Message your bot. It's running.

---

## Free AI through your ChatGPT subscription

If you have a ChatGPT Plus or Pro subscription, ABLE can route through it — no API key, no per-token cost.

```bash
python able/scripts/able-auth.py
```

One browser login. Token saved. T1 and T2 (70–80% of all requests) now cost $0.

For server deploys: set `OPENAI_OAUTH_AUTH_JSON` as a GitHub secret.

---

## How it routes

Every message gets complexity-scored in under 5ms. No LLM call needed.

| Complexity | Tier | Model | Cost |
|-----------|------|-------|------|
| < 0.4 | T1 | GPT 5.4 Mini (ChatGPT sub) → Gemma 4 31B (NIM free) | **$0** |
| 0.4–0.7 | T2 | GPT 5.4 (ChatGPT sub) → Qwen 3.6 Plus → Gemma 4 26B → MiMo | **$0** |
| 0.5–0.7 | T2.5 | Sonnet + Opus advisor (API fallback only) | ~$3/$15/M |
| > 0.7 | T4 | Managed Agents Opus → Claude Code CLI → Opus API | **$0** (Max sub) |
| Background | T3 | MiniMax M2.7 | $0.30/$1.20/M |
| Offline | T5 | Gemma 4 E4B (primary) → Gemma 4 31B cloud → Qwen 3.5 9B local | **Free** |

The routing weights self-tune every 6 hours using a background daemon.

---

## The training pipeline

ABLE turns your conversations into training data automatically.

**Every 4 hours:**
- Scores recent interactions (reasoning quality, routing accuracy, tool correctness)
- Flags wins (AI got it right first try) and corrections (you had to guide it)
- Builds DPO training pairs: chosen responses vs rejected ones

**Every night at 2am:**
- Harvests conversations from 13 sources — Claude Code, Codex, ChatGPT, Gemini, Grok, Cursor, Windsurf, Manus, Perplexity, claude.ai exports, and more
- Drop any AI tool's export into `~/.able/external_sessions/` and it gets picked up automatically
- Builds a distillation corpus with deduplication and quality filtering

**Weekly research scout (Karpathy LLM Wiki pattern):**
- 6-phase cumulative research that builds on previous findings
- Auto-discovers new system components and researches improvements
- Source verification, knowledge graph, mermaid topic maps
- Everything filed to TriliumNext with cross-references

**When you're ready to fine-tune:**
- Training pairs are in `data/distillation_*.jsonl`
- Primary target: **Gemma 4 E4B** (5.1B params, fits free Colab T4, Apache 2.0)
- Unsloth notebooks auto-generated for E4B, Nano 9B, Student 27B, Gemma 4 31B
- Standalone Python trainers + MLX scripts for Apple Silicon included
- Fine-tuned model plugs into T5 (local), promotes to T1 when it passes eval
- Apple Silicon ANE-aware: battery mode uses Neural Engine prefill + GPU decode

---

## The buddy system

Think Pokémon but for business use cases, your buddy starts at a level based on your existing AI interaction history. Install ABLE and it reads your patterns — domains you work in, complexity you deal with — and sets the starting level accordingly.

It earns XP from real system work:
- Every completed interaction
- Every distillation pair harvested  
- Every evolution daemon cycle
- Every gstack sprint skill run

It evolves through 3 stages. It has needs. It gets stronger when the system is healthy.

---

## Observability — see everything

```bash
# Start Phoenix + TriliumNext
docker compose --profile observability up -d
```

- **Phoenix** at `localhost:6006` — traces every LLM call, tool execution, cron job
- **TriliumNext** at `localhost:8081` — knowledge base with research findings, architecture docs, topic maps
- **Knowledge graph** at `data/research_graph.html` — interactive D3 visualization of research topology

---

## Deploy

Push to `main` → GitHub Actions builds → pushes to GHCR → deploys via SSH.

```bash
# Manual deploy to any server
./deploy-to-server.sh <server_ip> [ssh_key_path]
```

Docker auto-installs on the server. Pre-built image pulls from GHCR. Cron is leader-gated: server deploys set `ABLE_CRON_ENABLED=1`, while local/dev runs default to follower mode. Cron state lives in the `able_db` volume (`/home/able/app/able/data`) and uses per-scheduled-run claims so deploy restarts do not re-fire nightly jobs.

---

## Environment variables

| Variable | What it's for |
|----------|---------------|
| `TELEGRAM_BOT_TOKEN` | Your bot (from @BotFather) |
| `ABLE_OWNER_TELEGRAM_ID` | Your Telegram user ID |
| `OPENROUTER_API_KEY` | Fallback models + evolution daemon |
| `ANTHROPIC_API_KEY` | Claude Opus 4.6 (T4 premium tier) |
| `NVIDIA_API_KEY` | Gemma 4 31B (free T1 fallback via NIM) |
| `TRILIUM_ETAPI_TOKEN` | TriliumNext knowledge base |
| `XCRAWL_API_KEY` | XCrawl structured web scraping |

Full list: [`able/.env.example`](able/.env.example)

---

## Build profiles

| Profile | Size | Use |
|---------|------|-----|
| **slim** (default) | ~350MB | Everything you need |
| **full** | ~2GB+ | + Browser automation, semantic memory, billing |

```bash
PROFILE=full docker compose up -d --build
```

---

## What's inside

```
able/
├── core/gateway/        AI request pipeline (routing, tools, Telegram)
├── core/routing/        Complexity scorer + provider chain + effort levels + budget tracker
├── core/buddy/          Companion system (XP, evolution, renderer)
├── core/evolution/      Self-tuning weights + cumulative research scout
├── core/distillation/   Training data pipeline (harvest → score → corpus → Colab/MLX → GGUF)
├── core/federation/     Network sharing + distributed compute mesh
├── core/observability/  Phoenix tracing (every LLM call, every tool, every cron)
├── core/session/        Context compaction + compress-thinking + shared scratchpad + versioning
├── core/security/       TrustGate, CommandGuard, egress inspector
├── scheduler/           15+ cron jobs (audit, harvest, evolution, research, lint)
├── tools/trilium/       TriliumNext knowledge base (wiki, research ingestion)
├── tools/xcrawl/        Structured web scraping for deep research
├── tools/graphify/      Knowledge graph builder (D3 + mermaid)
├── tools/rtk/           Token compression (60-90% savings on tool outputs)
├── tools/media/         Media generation fallback (DALL-E, ElevenLabs, placeholder)
├── tools/               Browser, search, GitHub, DigitalOcean, Vercel, codex audit
├── memory/              SQLite + vector + layered memory + temporal graph + freshness
└── skills/              30+ auto-triggered skills (copywriting, security, deploy, research, media)
```

---

## The gateway — what happens when you send a message

50+ tools registered. Every request runs through:

```
Message -> TrustGate (safety score) -> Scanner -> Enricher (expands vague prompts)
    -> ComplexityScorer (<5ms, no LLM call) -> Provider chain -> Tool dispatch
         -> ExecutionMonitor (detects stuck loops) -> ContextCompactor (prevents overflow)
```

If the model gets stuck calling the same tool — killed. If context fills up — compacted. If the provider disconnects on a large payload — auto-retried with compression. If a tool produces 50KB of output — persisted to disk, replaced with a summary.

You don't manage any of this.

---

## Studio dashboard

```bash
cd able-studio && pnpm dev
```

Web dashboard at `localhost:3000` — buddy status, metrics, live event stream, chat routed through the full ABLE pipeline. Built on Next.js 16.2 + Neon Postgres.

---

## Security

| Layer | What it does |
|-------|-------------|
| TrustGate | Scores every message 0.0-1.0. Below 0.4 = blocked |
| CommandGuard | YAML-configured tool permissions (allow/ask/deny) |
| Egress Inspector | Catches URLs, S3 paths, git remotes before shell execution |
| Malware Scanner | Scans new skills before registration |
| Codex Cross-Audit | 3-layer code review (codex -> claude -> rule-based) |
| PII Redactor | Strips emails, phones, SSNs, API keys before external calls |
| Read Tracker | Blocks writes to unread files, blocks full rewrites on large files |
| Arg Sanitizer | Scans tool arguments for path traversal, shell injection, null bytes |

Config: `config/tool_permissions.yaml`

---

## License

Private.
