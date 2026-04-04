# ABLE — Your AI That Gets Smarter Every Day

ABLE is an AI agent that lives on your server, talks to you on Telegram, and **learns from every conversation**.

The more you use it, the sharper it gets — routing smarter, answering faster, building training data from your real work to fine-tune local models that cost you nothing to run.

---

## What makes ABLE different

**It routes to the cheapest model that can handle the job.**  
Simple questions hit GPT 5.4 Mini through your ChatGPT subscription — $0 per token. Complex tasks escalate. You never pay for more than you need.

**It learns from your real interactions.**  
Every conversation is evaluated, scored, and turned into training data. Wins get reinforced. Corrections get captured. Over time it builds a fine-tuned local model that sounds like your actual use case.

**It has a companion that grows with it.**  
Your buddy starts at a level based on how you already use AI. Feed it, train it, watch it evolve. It's not decorative — it reflects real system activity.

**It shares (safely) and gets shared with.**  
A federation network exchanges anonymized high-quality pairs between ABLE instances. Your system benefits from everyone's best conversations. PII is stripped before anything leaves.

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
| < 0.4 | T1 | GPT 5.4 Mini (ChatGPT sub) | **$0** |
| 0.4–0.7 | T2 | GPT 5.4 (ChatGPT sub) | **$0** |
| > 0.7 | T4 | Claude Opus 4.6 | $15/$75/M |
| Background | T3 | MiniMax M2.7 | $0.30/$1.20/M |
| Offline | T5 | Ollama local | **Free** |

The routing weights self-tune every 6 hours using a background daemon.

---

## The training pipeline

ABLE turns your conversations into training data automatically.

**Every 4 hours:**
- Scores recent interactions (reasoning quality, routing accuracy, tool correctness)
- Flags wins (AI got it right first try) and corrections (you had to guide it)
- Builds DPO training pairs: chosen responses vs rejected ones

**Every night at 2am:**
- Harvests conversations from CLI, Claude Code, Codex, ChatGPT, and any tool you've connected
- Builds a distillation corpus with deduplication and quality filtering

**When you're ready to fine-tune:**
- Training pairs are in `data/distillation_*.jsonl`
- Unsloth notebooks generated automatically for Qwen 3.5 9B / 27B
- Fine-tuned model plugs into T5 (local), promotes to T1 when it passes eval

---

## The buddy system

Your buddy starts at a level based on your existing AI interaction history. Install ABLE and it reads your patterns — domains you work in, complexity you deal with — and sets the starting level accordingly.

It earns XP from real system work:
- Every completed interaction
- Every distillation pair harvested  
- Every evolution daemon cycle
- Every gstack sprint skill run

It evolves through 3 stages. It has needs. It gets stronger when the system is healthy.

---

## Deploy

Push to `main` → GitHub Actions builds → pushes to GHCR → deploys via SSH.

```bash
# Manual deploy to any server
./deploy-to-server.sh <server_ip> [ssh_key_path]
```

Docker auto-installs on the server. Pre-built image pulls from GHCR. Done.

---

## Environment variables

| Variable | What it's for |
|----------|---------------|
| `TELEGRAM_BOT_TOKEN` | Your bot (from @BotFather) |
| `ABLE_OWNER_TELEGRAM_ID` | Your Telegram user ID |
| `OPENROUTER_API_KEY` | Fallback models + evolution daemon |
| `ANTHROPIC_API_KEY` | Claude Opus 4.6 (T4 premium tier) |
| `NVIDIA_API_KEY` | Nemotron 120B (free T1 fallback) |

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
├── core/routing/        Complexity scorer + provider chain
├── core/buddy/          Companion system (XP, evolution, renderer)
├── core/evolution/      Self-tuning routing weights (6h daemon)
├── core/distillation/   Training data pipeline (harvest → score → export)
├── core/federation/     Network sharing (anonymized high-quality pairs)
├── scheduler/           12+ cron jobs (audit, harvest, evolution, buddy care)
└── tools/               Browser, search, GitHub, DigitalOcean, Vercel
```

---

## License

Private.
