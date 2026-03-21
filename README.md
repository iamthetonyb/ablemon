# ATLAS — Autonomous Task & Learning Agent System

A self-evolving AI agent that operates 24/7 across Telegram, Discord, web dashboard, and CLI. Multi-model routing, persistent memory, autonomous scheduling, and a growing skill library — all running on your own infrastructure.

---

## What It Does

- **Multi-model intelligence** — 5-tier routing system that scores every request and selects the optimal model. Simple questions hit fast, cheap models. Complex reasoning escalates automatically. Premium models are budget-gated.
- **Self-evolving** — Background daemon continuously tunes routing weights based on real interaction outcomes. The system gets smarter over time without manual tuning.
- **Persistent memory** — SQLite + vector hybrid memory across sessions. Conversations, learnings, objectives, and client context survive restarts.
- **Autonomous operations** — Scheduled briefings, security pentests, self-reflection, learnings extraction — all run autonomously on cron with SQLite-backed execution logging and missed job recovery.
- **Skill system** — 25+ modular skills (copywriting, research, security audit, GitHub, Vercel deploy, VPS provisioning) that auto-trigger on intent detection.
- **Agent swarm** — Complex tasks auto-spawn parallel sub-agents (researcher, analyst, coder, critic, planner) with consensus building.
- **Multi-channel** — Telegram, Discord, Slack, web dashboard (Next.js), CLI, webhooks.
- **Security-first** — Trust gate scoring on every message, prompt injection detection, encrypted secrets, automated penetration testing, full audit trail.

---

## Model Routing

Requests are complexity-scored (0.0–1.0) and routed to the best model for the job:

| Tier | Model | When |
|------|-------|------|
| 1 | GPT 5.4 Mini (xhigh reasoning) | Default — fast, high quality |
| 2 | GPT 5.4 (xhigh reasoning) | Complex tasks, deep reasoning |
| 3 | MiniMax M2.7 | Background only — evolution daemon |
| 4 | Claude Opus 4.6 | Premium — budget-gated |
| 5 | Qwen 3.5 27B (local) | Offline fallback |

T1 and T2 route through your ChatGPT subscription at $0 per token. T5 runs locally via Ollama with YaRN context extension for long-form analysis.

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/iamthetonyb/ABLE.git && cd ABLE
cd atlas && pip install -r requirements.txt

# 2. Connect your ChatGPT subscription (T1/T2 routing)
python3 scripts/atlas-auth.py

# 3. Set API keys for fallback/premium providers
cp .env.example .env  # Add ANTHROPIC_API_KEY, OPENROUTER_API_KEY, etc.

# 4. Pull local model (offline fallback)
ollama pull qwen3.5:27b-q3_K_M

# 5. Run
python3 start.py
```

---

## Architecture

```
User → TrustGate → Scanner → Auditor → Enricher → Scorer → Provider
                                                      |
                                           Logger → Evolution Daemon
```

### Core Systems

| System | What It Does |
|--------|-------------|
| **Complexity Scorer** | Rule-based scoring (<5ms, no LLM calls) with domain-specific adjustments |
| **Prompt Enricher** | Expands vague inputs into actionable domain-specific criteria |
| **Evolution Daemon** | M2.7-powered background cycles that tune scoring weights from real data |
| **Persistent Scheduler** | SQLite-backed cron with retry, backoff, and missed job recovery |
| **Agent Swarm** | 9-role parallel execution for complex tasks (auto-triggered at score >= 0.6) |
| **Skill Engine** | Auto-triggering modular capabilities with trust levels and progressive disclosure |

### Web Dashboard (ATLAS Studio)

Next.js 16.2 dashboard for system management — provider health, tool gating, audit viewer, chat interface.

```bash
cd atlas-studio && npm install && npm run dev
```

---

## Qwen 3.5 Local Capabilities

The Tier 5 local fallback uses Qwen 3.5 via Ollama with:

- **YaRN context extension** — 32K base extended to 262K+ tokens for long documents
- **Video/long-form analysis** — Process transcripts, research papers, and extended content locally
- **MoE architecture** — 235B total parameters, 22B active per forward pass
- **Configurable thinking** — Off / low / medium / high / ultra reasoning modes

Pull the models: `ollama pull qwen3.5:27b-q3_K_M && ollama pull qwen3.5:9b`

---

## Autonomous Operations

These run on schedule without user prompting:

| Job | Schedule | What |
|-----|----------|------|
| Morning Briefing | 9am daily | System health, goals, provider status |
| Evening Check-in | 9pm daily | Day summary, activity review |
| GitHub Digest | 1pm daily | Repository activity scan |
| Learnings Extraction | 3am daily | Pattern mining from conversations |
| Self-Reflection | Sunday midnight | Performance audit, improvement plans |
| Security Pentest | Monday 4am | 60+ automated attack vectors |

All jobs are SQLite-persisted with retry logic. Missed jobs auto-recover on restart.

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| *OpenAI OAuth* | `python3 scripts/atlas-auth.py` — connects ChatGPT subscription |
| `ANTHROPIC_API_KEY` | Claude Opus 4.6 (premium tier) |
| `OPENROUTER_API_KEY` | MiMo fallback + M2.7 evolution daemon |
| `NVIDIA_API_KEY` | Nemotron 120B (free T1 fallback) |
| `TELEGRAM_BOT_TOKEN` | Telegram channel |

---

## License

Private repository. All rights reserved.
