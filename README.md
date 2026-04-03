# ABLE

**Autonomous Business & Learning Engine** — an AI agent runtime that routes across 5 model tiers, learns from every interaction, trains its own local models, & grows Ablémon that evolves alongside the system.

One install. One command. True autonomy. 

```bash
git clone https://github.com/iamthetonyb/ABLE.git && cd ABLE && bash install.sh
```

Then open a new terminal:

```bash
able
```

That's it. You'll pick a starter buddy, answer three onboarding questions, and you're running.

---

## What You Get

### Intelligent Model Routing

A 5-tier routing system that scores every request and sends it to the right model automatically. The cheapest tier that can handle the job wins. An evolution daemon runs every 6 hours to tune the routing weights from real interaction data — the system gets smarter at routing over time, without manual config.

| Tier | Model | When |
|------|-------|------|
| 1 | GPT 5.4 Mini (xhigh) | Default — handles ~70-80% of requests |
| 2 | GPT 5.4 (xhigh) | Complex reasoning, multi-step tasks |
| 3 | MiniMax M2.7 | Background evolution analysis only |
| 4 | Claude Opus 4.6 | Premium tasks, budget-gated |
| 5 | Qwen 3.5 27B/9B (Ollama) | Offline, local, free — distillation base |

T1 and T2 route through your ChatGPT subscription at $0/token. T5 runs locally. The evolution daemon on T3 tunes everything in the background.

### Buddy System

Every ABLE instance has a companion tied to it — kinda like the Pokemon games. Your buddy levels up from real work + interactions, not fake metrics.
This essentially is a gamified way to show a visual representation on how your system distills are going + overall evolution of its self improving nature based off how you use it.

**5 starters**, each with a domain bonus:

| Starter | Domain | Best For |
|---------|--------|----------|
| Blaze | Coder | Diffs, debugging, tool execution |
| Wave | Researcher | Web research, analysis, synthesis |
| Root | Builder | Deploy, infra, automation |
| Spark | Creative | Copywriting, design, content |
| Phantom | Security | Audits, threat analysis, hardening |

Buddies evolve through three stages tied to real system milestones:

- **Starter** — Fresh agent, learning the ropes
- **Trained** — 50+ interactions, 5+ eval passes, level 10+
- **Evolved** — 100+ distillation pairs, 3+ evolution deploys, level 25+

Buddies have mood, needs, + battle records (all tied around your business needs/goals etc.) Plus ASCII art that changes with each evolution stage. (There's some hidden features for completionists)
Use `/buddy` in chat to check on yours, `/battle <domain>` to test your system in eval-driven battles.

### Self-Distillation Pipeline

ABLE builds its own fine-tuned models from the work you do. Every interaction is a potential training pair. The entire pipeline runs on CPU except the final fine-tuning step:

1. **Harvest** — 8 sources (CLI, Claude Code, Codex, tools, inbox, etc.) collected nightly
2. **Strip + Scrub** — Scaffolding removal, PII redaction, quality scoring
3. **Corpus Build** — Domain-balanced ChatML format, train/val split
4. **Fine-tune** — Unsloth on cloud GPU (free T4 for 9B, H100/A100/L4 for 27B) or MLX LoRA locally on Apple Silicon

Target models: **Qwen 3.5 27B** (server) and **9B** (edge/mobile), both using Unsloth Dynamic 2.0 quants. A proactive readiness check monitors corpus growth and auto-exports training scripts when thresholds are hit.

### Federated Distillation Network

When you create a buddy, your instance auto-enrolls in a cross-instance training network. Zero config. Instances share anonymized, quality-gated training pairs:

```
You (security focus) ──→ GitHub Releases ──→ Others (coding focus)
```

**Domain snowball**: More users in a domain = more training pairs = better models = better outputs = even more pairs. The network grows autonomously with 4-layer ingestion security (TrustGate, scaffolding strip, quality re-validation, content hash dedup).

### ABLE Studio

A Next.js web dashboard for managing the runtime without touching the terminal:

- `/settings` — Tool catalog, approval toggles
- `/resources` — Service, model, and storage inventory
- `/collections` — Curated install bundles
- `/setup` — First-run validation for gateway, Ollama, and memory

```bash
cd able-studio && pnpm install && pnpm dev
```

### Tool System

Registry-backed tool metadata shared between the gateway and Studio. Tools have enable/disable state, approval requirements, risk levels, categories, and artifact types — all from a single source of truth. Operators toggle tools in Studio; the gateway enforces it.

---

## Chat Commands

| Command | What it does |
|---------|-------------|
| `/help` | Available commands |
| `/status` | Session stats (messages, tokens, cost) |
| `/tools` | List available tools |
| `/resources` | Control plane resource inventory |
| `/buddy` | Buddy stats, mood, needs |
| `/buddy bag` | Backpack, roster, badges, profile |
| `/buddy switch <name>` | Switch active buddy |
| `/battle <domain>` | Eval-based battle (security, code, etc.) |
| `/eval` | Distillation corpus progress |
| `/evolve` | Run a single evolution cycle |
| `/compact` | Clear view, print session recap |
| `/clear` | Clear terminal |
| `/exit` | Quit |

---

## CLI Usage

```bash
able                    # Chat (default in interactive terminal)
able chat               # Explicit chat
able serve              # Background gateway service
able chat --auto-approve          # Skip approval prompts
able chat --control-port 8080     # Expose control-plane API
able chat --no-stream             # Wait for full response
```

### Auth Setup (Optional)

For zero-cost T1/T2 routing via ChatGPT subscription:

```bash
python scripts/able-auth.py
```

### Offline Mode

Pure local with Ollama — no API keys, no internet:

```bash
ollama serve
ollama create qwen3.5-27b-ud -f config/ollama/Modelfile.27b
able
```

---

## Training Your Own Models

### On Apple Silicon (free, local)

MLX LoRA training — zero cloud, zero cost. 9B fits on 16GB Macs, 27B on 32GB+:

```bash
python3 -c "
from able.core.distillation.training.unsloth_exporter import UnslothExporter
UnslothExporter().export_mlx_training_script('9b', 'data/distillation_corpus.jsonl')
"
bash notebooks/train_mlx_able-nano-9b.sh
```

### On Cloud GPU (Colab / H100)

Unsloth fine-tuning — 2x speed, 70% less VRAM. 9B trains on Colab's free T4:

```bash
python3 -c "
from able.core.distillation.training.unsloth_exporter import UnslothExporter
e = UnslothExporter()
e.export_notebook('9b', 'data/distillation_corpus.jsonl', 'your-hf-org/able-nano-9b')
"
```

GPU fallback chain: **H100 → A100 → L4** for 27B, **T4 → L4 → A100 → H100** for 9B. The orchestrator picks the best available GPU automatically.

---

## Repo Layout

```
able/                   Python runtime and CLI
able/core/buddy/        Buddy companion system
able/core/distillation/ Harvesters, corpus builder, training pipeline
able/core/federation/   Cross-instance corpus sharing
able/core/routing/      Complexity scorer, prompt enricher, provider registry
able/core/evolution/    Self-evolving weight daemon
able/evals/             Promptfoo eval configs (feed distillation corpus)
able-studio/            Next.js control center
config/                 Routing, distillation, Ollama, and tenant config
docs/                   System docs and boundary map
```

## Runtime Boundaries

The default startup path is kept lean. Optional systems (billing, channel adapters, ASR, federation sync) live in-repo but only load when explicitly configured. See [docs/RUNTIME_REFACTOR_AUDIT.md](docs/RUNTIME_REFACTOR_AUDIT.md) for the full boundary map.

## Deployment

`push` to `main` triggers production deploy via `.github/workflows/deploy.yml`. The deploy path installs the packaged runtime and starts the `able` systemd unit. Server deploys include Phoenix/OTel observability; local installs skip it to stay light.

## Notes

- Generated artifacts (`.next/`, `node_modules/`, `.env`) are not in source control.
- This README documents the current runtime, not roadmap.
