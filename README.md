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

## Local Runtime

```bash
git clone https://github.com/iamthetonyb/ABLE.git
cd ABLE

python3 -m venv .venv
source .venv/bin/activate
pip install -r able/requirements.txt
pip install -e .

python scripts/able-auth.py
able chat
```

`able chat` is the local operator interface. It runs the same gateway pipeline used by the service, keeps a persistent session id, prompts in-terminal for write approvals, and streams AI responses token-by-token. Exposes the local control API on `http://127.0.0.1:8080` unless you pass `--control-port 0`.

Useful local commands:

```bash
able chat                                       # Default — streaming enabled
able chat --session showcase --client master
able chat --no-stream                           # Disable streaming (wait for full response)
able chat --control-port 0
able serve
```

Inside `able chat`, these local commands are handled without going through the model:

- `/help`
- `/status`
- `/tools`
- `/resources` — control plane resource inventory
- `/eval` — distillation corpus progress and pair counts
- `/evolve` — trigger a single evolution cycle (waters your buddy)
- `/buddy` — show your buddy's stats, needs, and mood
- `/battle <domain>` — run an eval-based battle (feeds your buddy)
- `/exit`

`able` with no subcommand still starts the packaged service path. `able serve` is the explicit version of that same behavior.

Environment is read from your shell or `/home/able/.able/.env` in the systemd deployment. `ABLE_SERVICE_TOKEN` is used to protect the control-plane API when set.

If you want a pure local/offline lane, bring up Ollama first and create the pinned models from `config/ollama/`.

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

## Notes

- Generated frontend artifacts such as `able-studio/.next/`, `able-studio/node_modules/`, and local `.env` files are not part of source control.
- The README documents the current runtime, not aspirational roadmap language.
