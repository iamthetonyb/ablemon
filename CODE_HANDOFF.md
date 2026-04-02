# ABLE — Code Handoff

Date: 2026-04-01
Branch: `codex/able-rewrite-integration`
Head: `88395b8`

## Source Of Truth

Use this file as the canonical cross-agent handoff.

Trust order when sources disagree:

1. This file
2. Current branch state
3. Current code in the repo
4. `README.md`
5. GitHub PR text/comments

## What ABLE Is

ABLE (Autonomous Business & Learning Engine) is a self-hosted AGI runtime. It routes requests through a 5-tier model stack, executes tool calls with operator approval, logs everything to a structured interaction database, and continuously self-tunes its routing weights and prompt enrichment via an evolution daemon.

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
│   │   ├── routing/prompt_enricher.py  # 953-line domain-aware enricher (rule-based, 0ms)
│   │   ├── routing/interaction_log.py  # 25-field interaction logging (SQLite WAL)
│   │   ├── evolution/             # Self-tuning daemon (6h cycles, M2.7 analysis)
│   │   ├── evolution/auto_improve.py   # Eval failure → improvement action classifier
│   │   ├── distillation/          # Training pipeline, GPU budget, model configs
│   │   ├── providers/             # OpenAI OAuth, Anthropic, OpenRouter, NIM, Ollama
│   │   ├── agents/                # Scanner, Auditor, Executor pipeline agents
│   │   ├── agi/                   # Self-improvement, goal planner, proactive engine
│   │   ├── session/               # Session state manager
│   │   └── auth/                  # OpenAI OAuth PKCE flow
│   ├── tools/                     # GitHub, DigitalOcean, Vercel, search, voice
│   ├── skills/                    # Skill library + loader + executor
│   ├── memory/                    # SQLite + vector hybrid memory
│   ├── evals/                     # promptfoo eval configs + collect_results.py
│   ├── billing/                   # Usage tracking, invoicing
│   ├── security/                  # Malware scanner, secret isolation
│   └── tests/                     # Test suite
├── able-studio/                   # Next.js 16.2 web dashboard
├── config/
│   ├── routing_config.yaml        # 5-tier provider registry + budget caps
│   ├── scorer_weights.yaml        # Complexity scorer (evolution-tuned, versioned)
│   ├── distillation/              # 27B and 9B training configs
│   └── ollama/                    # Modelfiles for local deployment
├── scripts/
│   ├── able-auth.py               # OpenAI OAuth setup
│   └── able-setup.sh              # First-run workspace init
├── deploy-to-server.sh            # Manual DigitalOcean deploy
├── .github/workflows/deploy.yml   # CI/CD: push to main → production
├── pyproject.toml                 # Package config — entry points: `able`, `able-chat`
├── CODE_HANDOFF.md                # This file — canonical cross-agent handoff
├── CLAUDE.md                      # Claude Code session context
├── SOUL.md                        # Personality directives
├── ABLE.md                        # Full system documentation (~700 lines)
└── README.md                      # Operator-facing runtime docs
```

## Architecture

```
User → TrustGate → Scanner → Auditor → PromptEnricher → ComplexityScorer → ProviderChain → Tool Dispatch
                                                                │
                                                  InteractionLogger → EvolutionDaemon (6h) → WeightDeployer
                                                        │                      │
                                                  DistillationHarvester    AutoImprove ← EvalResults
```

### Model Routing (5 tiers)

| Score   | Tier | Model                    | Cost              |
|---------|------|--------------------------|--------------------|
| < 0.4   | 1    | GPT 5.4 Mini (OAuth)     | $0 (subscription)  |
| 0.4-0.7 | 2    | GPT 5.4 (OAuth)          | $0 (subscription)  |
| > 0.7   | 4    | Claude Opus 4.6          | $15/$75 per M      |
| bg only | 3    | MiniMax M2.7 (OpenRouter) | $0.30/$1.20 per M |
| offline | 5    | Ollama Qwen 3.5 27B/9B  | FREE               |

Budget caps (source of truth: `config/routing_config.yaml`):
- Opus API fallback: $25/day, $150/month
- Evolution (M2.7): $5/day, $50/month
- OpenRouter total: $75/month
- Hard cap: $250/month

### Control Plane Endpoints (gateway :8080)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | none | Service health probe |
| GET | `/control/tools/catalog` | service token | Full tool catalog + effective settings |
| GET | `/control/resources` | service token | Nomad-style resource inventory |
| GET | `/control/resources/{id}` | service token | Resource detail + logs |
| POST | `/control/resources/{id}/action` | service token + `approved_by` | Lifecycle action |
| GET | `/control/collections` | service token | Curated install bundles |
| GET | `/control/setup-wizard` | service token | First-run validation steps |

Token verification uses `hmac.compare_digest` (timing-safe). Health endpoint exempt.

### Approval Flow

- **Telegram**: Inline keyboard buttons with HMAC-signed callbacks, timeout/escalation
- **CLI** (`able chat`): Terminal prompt (y/n/a), "always" mode for session auto-approve
- **Control plane API**: Service-token-gated, `approved_by` metadata + `service_token_verified` guard

### Tool System

Registry-backed from `able/core/gateway/tool_registry.py`. Tools declare: `requires_approval`, `risk_level`, `category`, `read_only`, `concurrent_safe`, `surface`, `artifact_kind`. Dispatch checks approval before execution.

## Self-Learning Pipeline

This is the core of ABLE's dynamic learning system. Five subsystems form a feedback loop:

### 1. Interaction Logger (`able/core/routing/interaction_log.py`)
Every request logs a 25-field record to `data/interaction_log.db` (SQLite WAL): routing decision (score, tier, domain, features), execution result (provider, latency, tokens, cost, fallback), quality signals (success, escalation, user_correction, satisfaction), and distillation metadata (corpus_eligible, enrichment_level).

### 2. Evolution Daemon (`able/core/evolution/`)
6-hour cycle: Collect (24h interaction window) → Analyze (M2.7 pattern detection) → Improve (bounded weight changes, max 20%/cycle) → Validate (bounds, tier gaps ≥ 0.15) → Deploy (versioned backup, hot-reload). Safety: min 20 interactions to trigger, all changes auditable, `deployer.rollback(to_version=N)`.

### 3. Eval System (`able/evals/`)
100+ test cases across 6 configs (security, copywriting, code-refactoring, enricher-3way, model-shootout). `collect_results.py` parses promptfoo SQLite → captures T4 outputs as distillation targets → identifies routing mismatches → feeds auto_improve.

### 4. Auto-Improver (`able/core/evolution/auto_improve.py`)
Classifies eval failures into 7 categories: thinking_bleed, skill_gap, format_violation, under_routing, content_quality, over_routing, model_regression. Maps each to corrective actions that feed back into the evolution daemon.

### 5. Distillation Pipeline (`able/core/distillation/`)
Harvests successful T4 (gold) completions from interaction log → exports JSONL training pairs → fine-tunes Qwen 3.5 via Axolotl + Unsloth on H100 (27B) or T4 Colab (9B) → re-quantizes to UD targets → deploys to Ollama T5 lane. Currently ~20 pairs collected, needs 100+ for first H100 run.

### 6. Prompt Enricher (`able/core/routing/prompt_enricher.py`)
953-line rule-based enricher (0ms, $0). Detects 8 domains, expands 11 flavor words with domain-specific criteria. Four enrichment levels (none/light/standard/deep). Integrates memory context when available. A/B validated: baseline 0% vs enriched 60% pass on T1.

## Import Convention

All Python imports use fully-qualified paths:

```python
from able.core.gateway.tool_registry import ToolRegistry  # correct
from able.tools.github.client import GitHubClient          # correct
# NOT: from core.gateway.tool_registry import ...          # WRONG — shims removed
```

Root-level shim packages have been removed. All 87 bare imports migrated.

## Quant-Pinned Model Roster

Pinned sizes — do not change without re-measuring.

- `able-student-27b`: `UD-Q4_K_XL` = 17.6 GB | `Q5_K_M` = 19.6 GB | `Q8_0` = 28.6 GB
- `able-nano-9b`: `UD-IQ2_M` = 3.65 GB | `UD-Q4_K_XL` = 5.97 GB | `Q5_K_M` = 6.58 GB

Config source of truth:
- `config/distillation/able_student_27b.yaml`
- `config/distillation/able_nano_9b.yaml`
- `able/core/distillation/training/model_configs.py`

Training lanes:
- **27B**: H100-only, seq_len=8192, micro_batch=1, bf16
- **9B**: T4-first default, seq_len=2048, micro_batch=1, fp16, checkpoint every 100 steps

## What Was Just Completed (This Session)

1. Merged main into branch — picked up 88-file atlas→able rename
2. Migrated remaining 87 bare imports to `from able.*` across 25 files
3. Removed all 5 root-level shim packages (core/, tools/, memory/, scheduler/, clients/)
4. Simplified `pyproject.toml` packages.find to `["able*"]`
5. Fixed `deploy.yml` pip install to use `-e` flag (matches deploy-to-server.sh)
6. Fixed `datetime.utcnow()` deprecation in approval/workflow.py
7. Synced ABLE.md budget caps to match routing_config.yaml ($25/$150 Opus API)
8. Documented control plane endpoints in this handoff

## Next-Run Objectives

### Priority 1: Close the learning feedback loops

The five learning subsystems (interaction logger, evolution daemon, eval system, auto-improver, distillation) are individually mature but operate semi-independently. The next high-value work closes these loops:

**A. Evolution → Self-Improvement bridge**
`auto_improve.py` classifies failures but doesn't yet trigger `able/core/agi/self_improvement.py` to update skill prompts or routing behavior. Wire the auto-improver's improvement actions into the self-improvement engine so classified failures automatically generate document patches (with operator approval for sensitive files).

**B. Proactive → Evolution bridge**
`able/core/agi/proactive.py` runs LearningInsights checks but doesn't feed findings back to the evolution daemon. Connect proactive pattern detection to the collector so insights influence the next weight-tuning cycle.

**C. Memory → Evolution bridge**
`able/memory/hybrid_memory.py` stores learnings and patterns but the evolution daemon doesn't query memory to inform weight adjustments. Add a memory recall step to the evolution collector so accumulated operator preferences and domain patterns shape scoring.

**D. Distillation corpus acceleration**
Currently ~20 training pairs, need 100+. The eval pipeline (`collect_results.py`) already captures T4 outputs as gold targets. Increase eval coverage and add a threshold trigger that alerts when corpus hits 100 pairs (ready for H100 run).

### Priority 2: Test coverage for new surfaces

- Control plane endpoints (all 7 routes) — unit tests with mocked gateway
- Resource tools (`resource_list`, `resource_status`) — functional tests
- Distillation runtime profiles (T4 vs H100 lane selection) — validation tests
- Evolution daemon cycle — integration test for collect→analyze→improve→validate→deploy

### Priority 3: Resource action tool

The LLM can list/inspect resources via `resource_list` and `resource_status` tools but cannot trigger lifecycle actions through tool calls. Add a `resource_action` tool in `able/core/gateway/tool_defs/` with approval gating through `ApprovalWorkflow.request_approval()`. This closes the resource management loop.

### Priority 4: Operator experience

- `able chat` streaming output (currently blocks until full response)
- Slash-command palette (`/status`, `/tools`, `/help` exist; add `/eval`, `/evolve`, `/distill`)
- Inline approval cards (rich terminal rendering for approval prompts)

## Validation Commands

```bash
python3 -m able chat --help
python3 -m pytest able/tests/test_cli_chat.py -x
python3 -m pytest able/tests/test_training_pipeline.py -x
python3 -m pytest able/tests/test_distillation_store.py -x
bash -n deploy-to-server.sh
python3 -m py_compile scripts/able-auth.py
```

## Cross-Agent Collaboration Protocol

**Before starting work:**
1. Read this file first
2. Check `git log --oneline -10` for recent changes
3. Read `CLAUDE.md` for session-level context
4. Run `able chat --help` to verify the runtime is intact

**When making changes:**
- Commit to a feature branch, not main directly
- All imports: `from able.X.Y import Z` — bare imports are dead, shims are gone
- Run `python -m pytest able/tests/test_cli_chat.py` as a smoke test
- Update this handoff if you change architecture, entry points, or model roster

**When handing off:**
- Note the branch name and HEAD commit
- List what changed and what was NOT finished
- Include exact validation commands
- Flag any files modified but not tested

**Conventions:**
- No marketing copy — factual and operator-facing only
- Quant sizes are pinned — do not change without re-measuring
- Trust the routing_config.yaml for budget/tier numbers, not ABLE.md
- The README documents current state, not roadmap
