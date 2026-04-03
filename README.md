# ABLE — Autonomous Business & Learning Engine

AI-powered gateway with Telegram bot, cron scheduler, 5-tier LLM routing, and a companion buddy system. Runs anywhere Docker runs.

## Quick Start (Docker)

```bash
# 1. Clone
git clone https://github.com/iamthetonyb/ablemon.git && cd ablemon

# 2. Configure
cp able/.env.example able/.env
# Edit able/.env — add at minimum: TELEGRAM_BOT_TOKEN, ABLE_OWNER_TELEGRAM_ID

# 3. Run
docker compose up -d

# 4. Watch logs
docker compose logs -f
```

Health check: `curl http://localhost:8080/health`

## Build Profiles

| Profile | Size | Includes |
|---------|------|----------|
| **slim** (default) | ~350MB | Gateway, Telegram, cron, LLM routing, buddy system |
| **full** | ~2GB+ | + Playwright browser, sentence-transformers, Stripe billing |

```bash
# Full profile
PROFILE=full docker compose up -d --build
```

## Deploy to Server

Push to `main` triggers automatic deploy via GitHub Actions (builds Docker image, pushes to GHCR, deploys to server via SSH).

Manual deploy to any server:
```bash
./deploy-to-server.sh <server_ip> [ssh_key]
```

Auto-installs Docker on the server, pulls the pre-built image from GHCR, and starts the container.

## Environment Variables

See [`able/.env.example`](able/.env.example) for all options. Key ones:

| Variable | Required | Notes |
|----------|----------|-------|
| `TELEGRAM_BOT_TOKEN` | Yes | From @BotFather |
| `ABLE_OWNER_TELEGRAM_ID` | Yes | Your Telegram user ID |
| `OPENROUTER_API_KEY` | Recommended | Access to many models |
| `ANTHROPIC_API_KEY` | Optional | Claude Opus 4.6 (premium tier) |
| `NVIDIA_API_KEY` | Optional | Nemotron 120B (free NIM tier) |

## Model Routing

Requests are complexity-scored and routed to the cheapest capable model:

| Score | Tier | Model | Cost |
|-------|------|-------|------|
| < 0.4 | T1 | GPT 5.4 Mini (OAuth) | $0 |
| 0.4-0.7 | T2 | GPT 5.4 (OAuth) | $0 |
| > 0.7 | T4 | Claude Opus 4.6 | $15/$75 per M |
| background | T3 | MiniMax M2.7 | $0.30/$1.20 per M |
| offline | T5 | Ollama local | Free |

## OAuth Setup (Optional — $0 GPT Access)

If you have a ChatGPT subscription, route T1/T2 through it for free:

```bash
python able/scripts/able-auth.py
```

Opens a browser for one-time OAuth. Token saved to `~/.able/auth.json`.

For server deployment, set the token as `OPENAI_OAUTH_AUTH_JSON` in GitHub Secrets.

## Project Structure

```
ablemon/
├── able/                  # Main Python application
│   ├── start.py           # Entry point
│   ├── core/gateway/      # Telegram handler + tool dispatch
│   ├── core/routing/      # Complexity scorer + provider registry
│   ├── core/buddy/        # Companion system (Groot, etc.)
│   ├── core/evolution/    # Self-evolving routing weights
│   ├── scheduler/         # Cron with 10+ default jobs
│   ├── tools/             # Browser, search, GitHub, etc.
│   └── Dockerfile         # Multi-stage slim/full builds
├── docker-compose.yml     # Local dev compose
├── deploy-to-server.sh    # Manual server deploy script
└── .github/workflows/
    └── deploy.yml         # CI/CD: build → GHCR → deploy
```

## License

Private.
