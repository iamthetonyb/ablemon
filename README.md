# ATLAS — Autonomous Task & Learning Agent System

An executive-level AI agent operating via Telegram with a secure multi-agent pipeline, persistent memory, billing tracking, and AGI-oriented features.

---

## Quick Start

### Option A — Docker (Recommended for production / Digital Ocean)

```bash
cd atlas-v2
cp .env.example .env
# Edit .env with your credentials (see Configuration below)
docker-compose up -d
docker logs -f atlas-v2
```

### Option B — Local Python

```bash
cd atlas-v2
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python start.py
```

---

## Configuration

Edit `atlas-v2/.env` with these required values:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
ATLAS_OWNER_TELEGRAM_ID=your_telegram_user_id
OLLAMA_API_KEY=your_ollama_key          # Optional
NVIDIA_API_KEY=your_nvidia_key          # Optional (free tier AI)
ANTHROPIC_API_KEY=your_claude_key       # Optional (premium reasoning)
```

**Never commit `.env` to git** — it's in `.gitignore`.

---

## Architecture

```
ATLAS/
├── atlas-v2/          ← Main application (V2 — use this)
│   ├── start.py       ← Entry point
│   ├── core/          ← Agent pipeline, security, AGI engine
│   ├── channels/      ← Telegram, Discord, Slack adapters
│   ├── memory/        ← Hybrid SQLite + vector memory
│   ├── skills/        ← Skill library and executor
│   ├── tools/         ← Browser, shell, search, MCP bridge
│   ├── billing/       ← Usage tracking and invoices
│   ├── security/      ← AES-256 encryption, malware scanner
│   └── audit/         ← Audit logs, alerts, traces
│
├── atlas_system/      ← System-level docs and setup helpers
│   ├── CUSTOMIZATION.md  ← Full setup and personalization guide
│   ├── AGENTS.md         ← Multi-agent orchestration reference
│   ├── SECURITY.md       ← Threat patterns and response
│   └── skills/           ← Skill templates
│
├── SOUL.md            ← Core identity and behavioral directives
├── CLAUDE.md          ← Full operator configuration (Claude Code)
└── atlas_gateway.py   ← Legacy V1 gateway (reference only)
```

### Message Pipeline

```
Telegram → UnifiedGateway → Scanner → Auditor → TrustGate → Executor → AI Backend → Response
```

### AI Provider Chain

```
1. NVIDIA NIM (kimi-k2.5)    → Free tier
2. OpenRouter (kimi-k2.5)    → Fallback
3. Anthropic (claude-sonnet) → Complex reasoning
4. Ollama (local)            → Offline fallback
```

---

## Server Deployment (Digital Ocean)

From your local machine (after SSH key is set up):

```bash
bash deploy-to-server.sh
```

Manual steps:
```bash
ssh root@YOUR_SERVER_IP
git clone https://github.com/iamthetonyb/AIDE.git /opt/atlas
cd /opt/atlas/AIDE/atlas-v2
cp .env.example .env && nano .env
docker-compose up -d
docker logs -f atlas-v2
```

---

## Health Check

Once running, verify:
- `curl http://localhost:8080/health` → `{"status": "ok"}`
- Send `/start` to your Telegram bot

---

## Key Files

| File | Purpose |
|------|---------|
| `SOUL.md` | Behavioral identity (read every session) |
| `CLAUDE.md` | Full system configuration for Claude Code |
| `atlas_system/CUSTOMIZATION.md` | Personalization and setup guide |
| `atlas-v2/config/gateway.json` | Non-secret runtime settings |
| `atlas-v2/.env` | Secrets (never commit) |
