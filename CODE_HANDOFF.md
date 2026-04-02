# ABLE — AI Handoff Document

Last updated: 2026-04-01 by - claude 
Branch: `main`

This document is the cross-agent handoff for any AI assistant (Claude Code, Codex, Gemini, etc.) picking up work on the ABLE repo. Read it before touching anything.

## What ABLE Is

ABLE (Autonomous Business & Learning Engine) is a self-hosted AGI runtime. It routes requests through a 5-tier model stack, executes tool calls with operator approval, logs everything, and self-tunes its routing weights via an evolution daemon.

Channels: Telegram (production), CLI (`able chat`), Studio (web dashboard).

## Repo Structure

```
ABLE/
├── able/                          # Python package — the runtime
│   ├── __main__.py                # Console entry: `able serve` / `able chat`
│   ├── start.py                   # Gateway startup (systemd service path)
│   ├── cli/chat.py                # Local operator REPL
│   ├── core/
│   │   ├── gateway/gateway.py     # Central coordinator — routing, tools, Telegram, HTTP
│   │   ├── gateway/tool_registry.py  # Declarative tool registration + dispatch
│   │   ├── gateway/tool_defs/     # Tool modules: github, web, infra, tenant, resource
│   │   ├── control_plane/resources.py  # Nomad-style service/model/storage inventory
│   │   ├── approval/workflow.py   # Human-in-the-loop for write operations
│   │   ├── routing/               # Complexity scorer, prompt enricher, provider registry
│   │   ├── evolution/             # Self-tuning daemon (M2.7 background analysis)
│   │   ├── distillation/          # Training pipeline, GPU budget, model configs
│   │   ├── providers/             # OpenAI OAuth, Anthropic, OpenRouter, NIM, Ollama
│   │   ├── agents/                # Scanner, Auditor, Executor pipeline agents
│   │   ├── session/               # Session state manager
│   │   └── auth/                  # OpenAI OAuth PKCE flow
│   ├── tools/                     # GitHub, DigitalOcean, Vercel, search, voice
│   ├── skills/                    # Skill library + loader + executor
│   ├── memory/                    # SQLite + vector hybrid memory
│   ├── billing/                   # Usage tracking, invoicing
│   ├── security/                  # Malware scanner, secret isolation
│   ├── tests/                     # Test suite
│   └── evals/                     # promptfoo eval configs
├── able-studio/                   # Next.js 16.2 web dashboard
│   ├── app/                       # Pages: settings, resources, collections, setup, audit
│   ├── lib/control-plane.ts       # Gateway API client
│   └── drizzle/schema.ts          # Neon/Postgres schema
├── config/
│   ├── routing_config.yaml        # 5-tier provider registry
│   ├── scorer_weights.yaml        # Complexity scorer (M2.7-tuned)
│   ├── distillation/              # 27B and 9B training configs
│   └── ollama/                    # Modelfiles for local deployment
├── scripts/
│   ├── able-auth.py               # OpenAI OAuth setup
│   └── able-setup.sh              # First-run workspace init
├── deploy-to-server.sh            # Manual DigitalOcean deploy
├── .github/workflows/deploy.yml   # CI/CD: push to main → production
├── pyproject.toml                 # Package config — entry points: `able`, `able-chat`
├── CLAUDE.md                      # Claude Code context (loaded every session)
├── SOUL.md                        # Personality directives
├── ABLE.md                        # Full system documentation (~700 lines)
└── README.md                      # Operator-facing runtime docs
```

## Architecture

```
User Input → TrustGate → Scanner → Auditor → PromptEnricher → ComplexityScorer → ProviderChain → Tool Dispatch
                                                                       │
                                                          InteractionLogger → EvolutionDaemon (6h cycles)
```

### Model Routing (5 tiers)

| Score   | Tier | Model                    | Cost            |
|---------|------|--------------------------|-----------------|
| < 0.4   | 1    | GPT 5.4 Mini (OAuth)     | $0 (subscription) |
| 0.4-0.7 | 2    | GPT 5.4 (OAuth)          | $0 (subscription) |
| > 0.7   | 4    | Claude Opus 4.6          | $15/$75 per M   |
| bg only | 3    | MiniMax M2.7 (OpenRouter) | $0.30/$1.20 per M |
| offline | 5    | Ollama Qwen 3.5 27B/9B  | FREE            |

### Approval Flow

- **Telegram**: Inline keyboard buttons with HMAC-signed callbacks, timeout/escalation support
- **CLI** (`able chat`): Terminal prompt (y/n/a), with "always" mode for session-level auto-approve
- **Control plane API**: Service-token-gated, `approved_by` metadata required for lifecycle actions

### Tool System

Registry-backed (`able/core/gateway/tool_registry.py`). The gateway and studio share the same catalog. Tools declare: `requires_approval`, `risk_level`, `category`, `read_only`, `concurrent_safe`, `surface`, `artifact_kind`.

Dispatch checks approval before execution. Studio stores only overrides in Postgres `feature_flags`.

### Control Plane

HTTP endpoints on the gateway (`:8080`):

- `GET /health` — service health
- `GET /control/tools/catalog` — full tool catalog + effective settings
- `GET /control/resources` — Nomad-style resource inventory
- `GET /control/resources/{id}` — resource detail + logs
- `POST /control/resources/{id}/action` — lifecycle action (requires `approved_by` + service token)
- `GET /control/collections` — curated install bundles
- `GET /control/setup-wizard` — first-run validation steps

All control endpoints require `ABLE_SERVICE_TOKEN` when set.

## Quant-Pinned Model Roster

- `able-student-27b`: `UD-Q4_K_XL` = 17.6 GB | `Q5_K_M` = 19.6 GB | `Q8_0` = 28.6 GB
- `able-nano-9b`: `UD-IQ2_M` = 3.65 GB | `UD-Q4_K_XL` = 5.97 GB | `Q5_K_M` = 6.58 GB

Config source of truth:
- `config/distillation/able_student_27b.yaml`
- `config/distillation/able_nano_9b.yaml`
- `able/core/distillation/training/model_configs.py`

Training lanes:
- **27B**: H100-only (`h100_session`), seq_len=8192, micro_batch=1, bf16
- **9B**: T4-first default (`t4_colab`), seq_len=2048, micro_batch=1, fp16, checkpoint every 100 steps

## Import Convention

All Python imports inside `able/` use fully-qualified paths:

```python
from able.core.gateway.tool_registry import ToolRegistry  # correct
from able.tools.github.client import GitHubClient          # correct
# NOT: from core.gateway.tool_registry import ToolRegistry  # legacy style
```

Root-level shim packages (`core/__init__.py`, `tools/__init__.py`, etc.) exist as backwards compatibility redirects. New code must use `from able.X` style.

## Local Development

```bash
git clone https://github.com/iamthetonyb/ABLE.git && cd ABLE
python3 -m venv .venv && source .venv/bin/activate
pip install -r able/requirements.txt && pip install -e .
python scripts/able-auth.py       # OpenAI OAuth (T1/T2)
able chat                          # local operator REPL
able chat --auto-approve           # skip approval prompts
able serve                         # full gateway (Telegram + HTTP)
```

## Deployment

Production: push to `main` triggers `.github/workflows/deploy.yml`.
Manual: `bash deploy-to-server.sh [git-ref]`.

Both install via `pip install -e .` into `/home/able/.able/venv/` and restart the `able` systemd unit.

Health check: `curl http://127.0.0.1:8080/health`

## Validation Commands

```bash
# Unit tests
python -m pytest able/tests/test_cli_chat.py
python -m pytest able/tests/test_training_pipeline.py
python -m pytest able/tests/test_distillation_store.py

# CLI smoke test
python -m able chat --help

# Distillation preflight
python -m able.core.distillation.training --check --model 9b --gpu-class t4_colab

# Studio build
cd able-studio && pnpm install && pnpm build
```

## Known Gaps and Next Steps

1. **TUI upgrade for `able chat`**: Currently a plain text REPL. Streaming output, syntax highlighting, slash-command palette, and inline approval cards would bring it past OpenCode quality.

2. **Approval workflow for resource actions**: Control plane resource actions now require service-token authentication and `approved_by` metadata, but don't yet go through the full `ApprovalWorkflow.request_approval()` path with operator confirmation. Wiring this in would make resource lifecycle fully approval-native.

3. **Test coverage**: The control plane endpoints, resource tools, and new distillation runtime profiles need dedicated test cases.

4. **Root-level shim cleanup**: The `core/__init__.py`, `tools/__init__.py`, `memory/__init__.py`, `scheduler/__init__.py`, `clients/__init__.py` compatibility shims can be removed once all imports are confirmed migrated to `from able.X` style. Then simplify `pyproject.toml` packages.find to just `["able*"]`.

5. **Resource action tool**: The LLM can list/inspect resources via `resource_list` and `resource_status` tools but cannot trigger lifecycle actions through tool calls. Adding a `resource_action` tool with approval gating would close this loop.

## Cross-Agent Collaboration Protocol

This repo is designed for multi-agent development. When handing off or receiving work:

**Before starting work:**
1. Read this file first
2. Check `git log --oneline -10` for recent changes
3. Read `CLAUDE.md` for session-level context
4. Run `able chat --help` to verify the runtime is intact

**When making changes:**
- Commit to a feature branch, not main directly
- Use `from able.X` import style, not bare `from core.X`
- Run `python -m pytest able/tests/test_cli_chat.py` as a smoke test
- Update this handoff doc if you change architecture, add entry points, or modify the model roster

**When handing off:**
- Note the branch name and HEAD commit
- List what changed and what was NOT finished
- Include exact validation commands for verifying the work
- Flag any files that were modified but not tested

**Conventions:**
- No marketing copy in docs — factual and operator-facing only
- Quant sizes are pinned and verified — do not change without re-measuring
- The README documents current state, not roadmap
- SOUL.md defines personality, CLAUDE.md defines context, ABLE.md is the full reference
