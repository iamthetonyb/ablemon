# ABLE

ABLE is the local/operator-controlled runtime for the Autonomous Business & Learning Engine. This repo contains the Python gateway, the `able-studio` control center, a federated distillation pipeline with cross-instance corpus sharing, and the deployment assets used to run the packaged `able` service on the server.

The runtime is maintained with a runtime-first boundary policy: keep the operator path lean, keep optional systems in-repo but off the default startup path, and keep sample/template assets available without forcing them into the hot path. See [docs/RUNTIME_REFACTOR_AUDIT.md](docs/RUNTIME_REFACTOR_AUDIT.md) for the current boundary map.

## What Is In Scope

- Registry-backed tool runtime shared by the gateway and studio
- Nomad-style resource plane for services, models, storage, and optional local bundles
- Federated distillation pipeline with pinned 27B/9B quant targets and cross-instance corpus sharing
- Telegram gateway, approvals, routing, memory, audit, and background jobs
- ABLE Studio for audit, clients, CRM, settings, resources, collections, and setup

## Runtime Boundaries

Default operator/runtime path:

- `able`, `able chat`, gateway routing, approvals, memory, distillation, buddy, control plane
- Studio settings/resources/collections/setup surfaces

Optional but kept in-repo:

- billing (`Stripe`, `x402`) — webhook/server-only, config-gated
- channel adapters (`Slack`, `Discord`) — adapter library, not a primary runtime surface
- ASR backends — only loaded when explicitly configured or invoked
- Strix sidecar, federation publish/sync, and cron extras

Seed assets kept for local/server use:

- copywriting skill/evals/prompts
- prompt-bank seed data
- sample corpus/template assets

## Repo Layout

- `able/`: Python package and runtime entrypoint
- `able/core/distillation/`: harvesters, corpus builder, training pipeline, validation gate
- `able/core/federation/`: cross-instance corpus sharing (auto-enrolls on buddy creation)
- `able/evals/`: promptfoo eval configs feeding gold outputs into the distillation corpus
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

Interactive prompts use local line editing and history when `readline` is available, so arrow keys, cursor movement, and prompt history behave like a normal shell instead of printing raw escape sequences.

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
| `/clear` | Clear the terminal view but keep scrollback |
| `/compact` | Clear the view and print a compact session recap |
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

On first interactive run, ABLE now requires starter selection plus a short onboarding profile. The starter affects buddy theme and bonus XP, while the onboarding stores your focus, work style, and preferred distillation lane. `work_style` includes an `all-terrain` option for operators who switch between solo build, delivery, ops, and collaboration instead of fitting one mode. Non-interactive sessions skip this flow so scripted smokes do not block.

The chat header shows your active buddy, level, mood, needs, battle record, and how many AI providers are currently ready. Buddy battle stats are rendered as full labels (`Wins`, `Draws`, `Losses`) instead of terse counters.

`/resources` and `/battle` work correctly even when you run `able` from outside the repo root through `~/.local/bin/able`.

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

Payment endpoints are not activated on the default runtime path. Stripe/x402 only bootstrap on the webhook server when the relevant payment env vars are enabled.

### Optional Observability Extras

Base installs skip Phoenix and OpenTelemetry so the CLI stays lighter. If you want the Phoenix dashboard on a local machine, install the extra explicitly:

```bash
./.venv/bin/pip install -e ".[observability]"
```

The CLI shows a short dim `thinking` preview only when the current provider actually streams reasoning markers. That preview is provider-dependent; it is not universal chain-of-thought streaming across every backend.

Audio transcription is also opt-in. The CLI and gateway only initialize ASR support when you explicitly configure an audio backend such as `ABLE_ASR_PROVIDER` or `ABLE_ASR_ENDPOINT`.

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

The Studio build is part of the runtime boundary checks. Empty duplicate route folders were removed during the runtime-first cleanup so the app tree matches what Next actually builds.

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

- `27B`: H100 preferred → A100 (40GB+) → L4 (24GB, tight). Does NOT fit T4 (16GB).
- `9B`: free T4 / 16 GB Colab lane (daily), `sequence_len=2048`, `micro_batch_size=1`, checkpointing enabled

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

Use the 9B lane for regular T4 runs. Keep the 27B lane for H100/A100/L4 sessions.

### Local MLX Fine-Tuning (Apple Silicon)

For zero-cost local training on Mac using MLX (no cloud GPU needed):

```bash
# Generate the MLX training script
python3 -c "
from able.core.distillation.training.unsloth_exporter import UnslothExporter
UnslothExporter().export_mlx_training_script('9b', 'data/distillation_corpus.jsonl')
"

# Run it
bash notebooks/train_mlx_able-nano-9b.sh
```

The script trains a LoRA adapter via `mlx_lm.lora`, fuses it, converts to GGUF via `llama.cpp`, and generates an Ollama Modelfile. The 9B model at 4-bit needs ~8-10GB during training — fits any 32GB+ Mac. The 27B model needs ~20-24GB (64GB+ Macs only).

MLX training is ~2-3x slower than Unsloth on NVIDIA but has zero setup friction and no GPU allocation wait. Quality is identical — LoRA math is the same.

### Unsloth Fine-Tuning (Colab + VS Code)

The `UnslothExporter` generates ready-to-run Colab notebooks and standalone Python scripts for fine-tuning via Unsloth (2x speed, 70% less VRAM):

```bash
# Generate a Colab notebook for 9B training on free T4
python3 -c "
from able.core.distillation.training.unsloth_exporter import UnslothExporter
e = UnslothExporter()
e.export_notebook('9b', 'data/distillation_corpus.jsonl', 'your-hf-org/able-nano-9b')
e.export_training_script('9b', 'data/distillation_corpus.jsonl')
"
```

Notebooks auto-install Unsloth, load the ChatML corpus, train with SFTTrainer, export GGUF (Dynamic 2.0 quants), and generate an Ollama Modelfile. The 9B model trains on Colab's free T4 runtime (12-24 hours available). The 27B model requires an H100 session.

## Distillation Pipeline (CPU-First Design)

The entire distillation pipeline is designed to run on CPU, reserving GPU only for the final fine-tuning step:

| Stage | Runs On | Frequency |
|-------|---------|-----------|
| **Harvest** — 8 source harvesters (CLI, Claude Code, Codex, external tools, inbox, etc.) | CPU | Nightly (2am cron) |
| **Scaffolding strip** — 13+ XML tag types, base64, analytics, side channels | CPU | During harvest |
| **PII scrub** — emails, phones, IPs, paths, API keys (10 regex patterns) | CPU | During federation export |
| **Quality gate** — score threshold, length validation, content hash dedup | CPU | During harvest + ingestion |
| **Federation sync** — contribute/fetch/ingest via GitHub Releases | CPU | Nightly (3:30am cron) |
| **Corpus build** — domain balancing, ChatML format, train/val split | CPU | On demand |
| **Promptfoo eval** — gold output generation, regression testing | CPU | On demand / battle |
| **Fine-tuning** — Unsloth (cloud) or MLX LoRA (local) | **GPU** (free T4/MLX for 9B, A100/L4/H100 for 27B) | When corpus reaches threshold |

The promptfoo eval configs (`able/evals/`) serve double duty: they validate model quality AND generate gold T4 outputs that feed back into the distillation corpus via `corpus_eligible` flagging in the interaction log. The evolution daemon (M2.7, 6h cycles) tunes routing weights based on these eval results, closing the self-improvement loop.

Phoenix/OTel observability (optional `.[observability]` install) provides tracing across the full pipeline when enabled on server deploys. The CLI skips Phoenix entirely to keep startup fast.

## Federated Distillation Network

When a new ABLE installation creates a buddy during `able chat` first-run, the instance auto-enrolls in the federated distillation network via `~/.able/instance.yaml` (UUID4 identity, zero config). The network shares anonymized training pairs across instances:

```
Instance A (security) ──→ GitHub Releases ──→ Instance B (coding)
   contributor                                    ingester
   (PII scrub + quality gate)                    (TrustGate + dedup)
```

**Domain snowball**: More users working in a domain = more training pairs = better fine-tuned models for that domain = better outputs = more training pairs. The network grows autonomously.

**Security** (4-layer ingestion):
1. TrustGate — 52+ injection pattern detection, reject if trust_score < 0.7
2. Scaffolding strip — defense-in-depth re-strip on all incoming text
3. Quality re-validation — prompt ≥ 20 chars, response ≥ 50 chars
4. Content hash dedup — SHA256 unique index, identical pairs from 100 instances = 1 stored pair

Network pairs are stored with `tenant_id='network'` and flow through the existing `CorpusBuilder` path. The sync runs at 3:30am daily, after the 2am harvest and 3am evolution cycle.

```bash
# Check federation status
python3 -c "from able.core.federation.identity import get_instance_config; print(get_instance_config())"

# Opt out of network sharing
python3 -c "from able.core.federation.identity import set_network_enabled; set_network_enabled(False)"
```

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
