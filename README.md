# ABLE

ABLE is the local/operator-controlled runtime for the Autonomous Business & Learning Engine. This repo contains the Python gateway, the `able-studio` control center, the distillation pipeline, and the deployment assets used to run the packaged `able` service on the server.

## What Is In Scope

- Registry-backed tool runtime shared by the gateway and studio
- Nomad-style resource plane for services, models, storage, and optional local bundles
- Distillation pipeline with pinned 27B/9B quant targets
- Telegram gateway, approvals, routing, memory, audit, and background jobs
- ABLE Studio for audit, clients, CRM, settings, resources, collections, and setup

## Repo Layout

- `able/`: Python package and runtime entrypoint
- `able-studio/`: Next.js control center
- `config/`: routing, distillation, Ollama, and tenant config
- `docs/`: deeper system docs
- `.github/workflows/`: deploy automation
- `deploy-to-server.sh`: manual packaged deploy helper

## Quick Start

```bash
git clone https://github.com/iamthetonyb/ABLE.git
cd ABLE
bash install.sh
```

That's it. The installer checks for Python 3.11+ (installs it if missing), creates a virtual environment, installs all dependencies, and puts `able` on your PATH. After install, open a **new terminal** and type:

```bash
able              # opens chat (interactive terminal)
able chat         # same thing, explicit
able-chat         # direct chat wrapper
able serve        # start the background gateway service
```

### Commands

```bash
able                                            # Chat (default in interactive terminal)
able chat                                       # Explicit chat
able-chat                                       # Shortcut for `able chat`
able chat --session showcase --client master     # Custom session/client
able chat --control-port 8080                   # Expose /health + control-plane API during chat
able chat --no-stream                           # Wait for full response
able chat --verbose                             # Show full startup logs
able chat --auto-approve                        # Skip approval prompts
able serve                                      # Background gateway service
```

### Chat Commands

Inside the chat, these are handled locally (no model call):

| Command | What it does |
|---------|-------------|
| `/help` | Show available commands |
| `/status` | Session stats (messages, tokens, cost) |
| `/tools` | List available tools |
| `/resources` | Control plane resource inventory |
| `/eval` | Distillation corpus progress |
| `/evolve` | Run a single evolution cycle |
| `/buddy` | Your buddy's stats, needs, and mood |
| `/buddy bag` | Backpack, caught roster, badges, and profile |
| `/buddy switch <name>` | Switch the active buddy in your roster |
| `/buddy setup` | Re-run buddy onboarding/profile setup |
| `/battle <domain>` | Eval-based battle (security, code, etc.) |
| `/exit` | Quit |

On first interactive run, ABLE now requires starter selection plus a short onboarding profile. The starter affects buddy theme and bonus XP, while the onboarding stores your focus, work style, and preferred distillation lane. Non-interactive sessions skip this flow so scripted smokes do not block.

The hidden endgame unlock is **Aether**: the sixth signal, a Dragon/Psychic-style orchestrator buddy that only appears after fully completing the five public starter lines. Early badge path now includes `Trainer`, while the late-game path includes the hidden-signal unlock and a final mastery badge for fully evolving and leveling Aether.

Research scout outputs are written to `~/.able/reports/research/latest.md` and `~/.able/reports/research/latest.json`, with a JSON mirror still kept under `data/research_reports/`.

### Auth Setup (Optional)

For OpenAI T1/T2 routing via your ChatGPT subscription:

```bash
python scripts/able-auth.py
```

### Offline Mode

For pure local/offline usage with Ollama:

```bash
ollama serve
ollama create qwen3.5-27b-ud -f config/ollama/Modelfile.27b
able
```

Environment is read from your shell or `/home/able/.able/.env` in the systemd deployment. `ABLE_SERVICE_TOKEN` protects the control-plane API when set.

### Optional Observability Extras

Base installs skip Phoenix and OpenTelemetry so the CLI stays lighter. If you want the Phoenix dashboard on a local machine, install the extra explicitly:

```bash
./.venv/bin/pip install -e ".[observability]"
```

```bash
ollama serve
huggingface-cli download unsloth/Qwen3.5-27B-GGUF Qwen3.5-27B-UD-Q4_K_XL.gguf --local-dir ./models
huggingface-cli download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-UD-IQ2_M.gguf --local-dir ./models
huggingface-cli download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-UD-Q4_K_XL.gguf --local-dir ./models
ollama create qwen3.5-27b-ud -f config/ollama/Modelfile.27b
ollama create qwen3.5-9b-edge -f config/ollama/Modelfile.9b-edge
ollama create qwen3.5-9b-balanced -f config/ollama/Modelfile.9b-balanced
export OLLAMA_BASE_URL=http://127.0.0.1:11434
able chat
```

## ABLE Studio

```bash
cd able-studio
pnpm install
pnpm dev
```

Set these environment variables for studio:

- `DATABASE_URL`: Postgres/Neon database for studio state
- `ABLE_CONTROL_API_BASE`: gateway base URL, default `http://127.0.0.1:8080`
- `ABLE_SERVICE_TOKEN`: shared control-plane token if the gateway is protected

Studio now reads the live tool catalog from the gateway and stores only per-org overrides in `feature_flags`. The main operator surfaces are:

- `/settings`: shared tool catalog and approval toggles
- `/resources`: service/model/storage inventory
- `/collections`: curated install bundles
- `/setup`: first-run validation for gateway, control API, Ollama, and memory

## Tool System

Tool metadata is registry-backed from `able/core/gateway/tool_registry.py`. The gateway and studio use the same source of truth for:

- enable/disable state
- approval requirement
- risk level
- category grouping
- read-only/concurrency metadata
- artifact type

Current grouped categories in studio are:

- `search-fetch`
- `execution`
- `agents-tasks`
- `planning`
- `system`

## Model Roster

Pinned quant artifacts:

- `able-student-27b`
  - `UD-Q4_K_XL` = `17.6 GB`
  - `Q5_K_M` = `19.6 GB`
  - `Q8_0` = `28.6 GB`
- `able-nano-9b`
  - `UD-IQ2_M` = `3.65 GB`
  - `UD-Q4_K_XL` = `5.97 GB`
  - `Q5_K_M` = `6.58 GB`

Reference files:

- `config/distillation/able_student_27b.yaml`
- `config/distillation/able_nano_9b.yaml`
- `config/ollama/Modelfile.27b`
- `config/ollama/Modelfile.9b-edge`
- `config/ollama/Modelfile.9b-balanced`

Training lanes:

- `27B`: H100-only
- `9B`: default T4 / 16 GB lane, `sequence_len=2048`, `micro_batch_size=1`, checkpointing enabled

Useful commands:

```bash
python -m able.core.distillation.training --check --model 9b --gpu-class t4_colab
python -m able.core.distillation.training --train 9b --gpu-class t4_colab --runtime colab --checkpoint-dir ~/able-checkpoints/9b --resume
python -m able.core.distillation.training --status
```

If `--gpu-class` is omitted, the orchestrator uses each model's default lane.

Recommended prep for VS Code + Unsloth + Colab:

```bash
python -m able.core.distillation status
python -m able.core.distillation corpus --status
python -m able.core.distillation.training --check --model 9b --gpu-class t4_colab --runtime colab --checkpoint-dir ~/able-checkpoints/9b
python -m able.core.distillation.training --train 9b --gpu-class t4_colab --runtime colab --checkpoint-dir ~/able-checkpoints/9b --resume
```

Use the 9B lane for regular T4 runs. Keep the 27B lane for H100 sessions only.

## Deployment

Production deploy remains `main`-driven via `.github/workflows/deploy.yml`.

- `push` to `main`: production deploy
- `workflow_dispatch` with `ref`: manual branch/tag/SHA deploy

Both the GitHub Action and `deploy-to-server.sh` now install the packaged runtime and start the `able` systemd unit instead of calling `python start.py` directly.
The deploy path also bootstraps the `able` system user/group if the VPS has not been migrated yet.
Server deploys install `.[observability]` so Phoenix/OTel remain available there without forcing them into every local CLI install.

## Notes

- Generated frontend artifacts such as `able-studio/.next/`, `able-studio/node_modules/`, and local `.env` files are not part of source control.
- The README documents the current runtime, not aspirational roadmap language.
