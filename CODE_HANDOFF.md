# ABLE — Code Handoff

Date: 2026-04-24
Branch: `main` is the current production baseline; verify active work branch before editing.
Git state: always verify with `git log --oneline -10` before starting work.

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
│   │   ├── gateway/tool_result_storage.py  # 3-layer tool output persistence (Hermes PR #5210)
│   │   ├── gateway/tool_defs/     # Tool modules: github, web, infra, tenant, resource
│   │   ├── control_plane/resources.py  # Nomad-style service/model/storage inventory
│   │   ├── approval/workflow.py   # Human-in-the-loop for write operations
│   │   ├── routing/               # Complexity scorer, prompt enricher, provider registry
│   │   ├── routing/prompt_enricher.py  # 953-line domain-aware enricher (rule-based, 0ms)
│   │   ├── routing/interaction_log.py  # 25-field interaction logging (SQLite WAL)
│   │   ├── evolution/             # Self-tuning daemon (6h cycles, M2.7 analysis)
│   │   ├── evolution/auto_improve.py   # Eval failure → improvement action classifier
│   │   ├── distillation/          # Training pipeline, GPU budget, model configs
│   │   ├── federation/            # Federated distillation network (cross-instance corpus)
│   │   ├── providers/             # OpenAI OAuth, Anthropic, OpenRouter, NIM, Ollama
│   │   ├── agents/                # Scanner, Auditor, Executor pipeline agents
│   │   ├── agi/                   # Self-improvement, goal planner, proactive engine
│   │   ├── buddy/                 # Gamified agent companion (Pokemon-style + Tamagotchi needs)
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
│   ├── distillation/              # E4B, 9B training configs
│   └── ollama/                    # Modelfiles for local deployment
├── scripts/
│   ├── able-auth.py               # OpenAI OAuth setup
│   └── able-setup.sh              # First-run workspace init
├── deploy-to-server.sh            # Manual DigitalOcean deploy
├── .github/workflows/deploy.yml   # CI/CD: push to main → production
├── pyproject.toml                 # Package config — entry points: `able`, `able-chat`
├── CODE_HANDOFF.md                # This file — canonical cross-agent handoff
├── NEXT_RUN_PROMPT.md                # Reusable next-run prompt for any coding agent
├── docs/RUNTIME_REFACTOR_AUDIT.md # Runtime boundary map: core vs optional vs seed vs dead
├── CLAUDE.md                      # Optional Claude Code session context
├── SOUL.md                        # Personality directives
├── ABLE.md                        # Full system documentation (~700 lines)
└── README.md                      # Operator-facing runtime docs
```

## Architecture

```
User → TrustGate → Scanner → Auditor → PromptEnricher → ComplexityScorer → ProviderChain → Tool Dispatch
                                                                │                              │
                                                  InteractionLogger → EvolutionDaemon    ExecutionMonitor
                                                        │                │                     │
                                                  DistillationHarvester  AutoImprove    ContextCompactor
                                                                                              │
                                                                                    ToolResultStorage
```

### Gateway Robustness Stack (Phase 0/1 — 2026-04-09)

| Layer | Component | Purpose |
|-------|-----------|---------|
| Context | `ContextCompactor` | Strip-thinking + extractive summary at 80% capacity, death spiral prevention (max 3 attempts) |
| Context | `ToolResultStorage` | 3-layer large output defense: self-truncate → persist-to-disk → enforce-turn-budget |
| Progress | `ExecutionMonitor` | Spinning/thrashing/error-loop detection (<1ms heuristics) |
| Progress | Repeated call guard | Pre-dispatch fingerprint check blocks identical consecutive tool calls |
| Timeout | Activity-based | 20-iteration budget, idle pressure at 60s, extends for active agents |
| Recovery | Thinking prefill | Re-run when model produces thinking but no output (max 2 retries) |
| Recovery | 413 auto-compress | Catch provider context-length errors, auto-compact, retry |
| Notification | Completion queue | Cron jobs with `notify_on_complete` push results to gateway |

### Cron Reliability Contract

`CronScheduler` uses SQLite as the durable coordination store. The default DB path is `able/data/cron_executions.db`, which maps to `/home/able/app/able/data` in Docker and is mounted as the `able_db` volume on the server.

Only one runtime should be the cron + Telegram channel leader. Production deploys set `ABLE_CRON_ENABLED=1` and `ABLE_TELEGRAM_ENABLED=1`; local/dev gateways default to follower mode so they can use CLI/chat without scheduled reports or Telegram conflicts. Telegram supports `ABLE_TELEGRAM_MODE=off|polling|webhook`; webhook mode is preferred once a public HTTPS `ABLE_TELEGRAM_WEBHOOK_URL` routes to `/webhook/telegram`, because Telegram then pushes to one endpoint instead of competing through `getUpdates`. Scheduled and recovery runs claim `job_run_claims(job_name, run_slot)` before executing. `run_slot` is the actual scheduled epoch-minute, not the current recovery minute. This prevents duplicate fires across deploy restarts and same-DB process races. Empty-DB startup recovery is disabled by default to avoid stale Telegram floods; set `ABLE_CRON_EMPTY_DB_RECOVERY_HOURS` only when a first-boot catchup is explicitly wanted.

### Model Routing (5 tiers)

| Score   | Tier | Model                    | Cost              |
|---------|------|--------------------------|--------------------|
| < 0.4   | 1    | GPT 5.4 Mini (OAuth)     | $0 (subscription)  |
| 0.4-0.7 | 2    | GPT 5.4 (OAuth)          | $0 (subscription)  |
| > 0.7   | 4    | Claude Opus 4.6          | $15/$75 per M      |
| bg only | 3    | MiniMax M2.7 (OpenRouter) | $0.30/$1.20 per M |
| offline | 5    | Gemma 4 E4B (primary) → Qwen 3.5 9B | FREE |

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
| GET | `/ws` | service token | WebSocket streaming (JSON frames: chunk/done/error) |

Token verification uses `hmac.compare_digest` (timing-safe). Health endpoint exempt.

### Approval Flow

- **Telegram**: Inline keyboard buttons with HMAC-signed callbacks, timeout/escalation
- **CLI** (`able chat`): Terminal prompt (y/n/a), "always" mode for session auto-approve
- **Control plane API**: Service-token-gated, `approved_by` metadata + `service_token_verified` guard
- **Auto-improve skill updates**: When the gateway starts the evolution daemon, eval-driven SKILL.md updates now reuse the same approval workflow before applying `## Auto-Improve Guidance` changes.

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
Classifies eval failures into 7 categories: thinking_bleed, skill_gap, format_violation, under_routing, content_quality, over_routing, model_regression. Routing actions stay in the evolution daemon; skill/content-quality actions now submit approval-gated SKILL.md section updates through `SelfImprovementEngine`.

### 5. Distillation Pipeline (`able/core/distillation/`)
Harvests successful T4 (gold) completions from interaction log → exports JSONL training pairs → fine-tunes via Unsloth on free Colab T4 (E4B primary, 9B fallback) → exports GGUF → deploys to Ollama T5 lane. 684 total pairs, 165 domain-balanced corpus v048 ready for first E4B fine-tune.

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

- **`able-gemma4-e4b`**: `UD-Q4_K_XL` = 3.2 GB | `UD-IQ2_M` = 1.8 GB (primary distillation target)
- `able-nano-9b`: `UD-IQ2_M` = 3.65 GB | `UD-Q4_K_XL` = 5.97 GB | `Q5_K_M` = 6.58 GB
- `able-gemma4-31b`: `UD-Q4_K_XL` = 18.8 GB | `Q5_K_M` = 21.0 GB | `Q8_0` = 31.0 GB
- `able-gemma4-e4b`: `UD-Q4_K_XL` = 3.2 GB | `UD-IQ2_M` = 1.8 GB

Config source of truth:
- `config/distillation/able_nano_9b.yaml`
- `able/core/distillation/training/model_configs.py`

Training lanes:
- **E4B (primary)**: Free T4 Colab (10GB QLoRA), seq_len=4096, micro_batch=2, fp16, Unsloth mandatory (KV-sharing bug)
- **9B**: T4-first default, seq_len=2048, micro_batch=1, fp16, checkpoint every 100 steps
- **Gemma 4 31B**: A100/H100, seq_len=8192, micro_batch=1, bf16, Unsloth LoRA r=8/alpha=8
- **Gemma 4 E4B**: Free T4 Colab (10GB QLoRA), seq_len=4096, micro_batch=2, fp16, Unsloth mandatory (KV-sharing bug)

## Latest Completed Work (Session 2026-04-09, continued)

46. **Plan Item 1: TurboQuant KV Cache** (aeac30f):
    - Added `flash_attention on`, `cache_type_k q4_0`, `cache_type_v q4_0` to `config/ollama/Modelfile.gemma4-31b` — ~2x usable context at same VRAM
    - NEW `config/ollama/Modelfile.gemma4-31b-turbo` — aggressive KV variant with `num_predict 16384`
    - NEW `config/ollama/Modelfile.gemma4-e4b` — edge model, 32K context, Gemma 4 chat template
    - NEW `able/core/distillation/training/kv_cache_config.py` — `recommend_kv_strategy(model_name, vram_gb, target_context)` returns optimal K/V cache types. 4-tier VRAM-based recommendations (f16 → q8_0/q4_0 → q4_0/q4_0 → reduced context)

47. **Plan Item 2: Gemma 4 Distillation Targets** (aeac30f):
    - `ABLE_GEMMA4_31B` (server, 22GB QLoRA on A100/H100) and `ABLE_GEMMA4_E4B` (edge, 10GB QLoRA on free T4) added to `MODEL_REGISTRY`
    - Unsloth LoRA defaults: r=8, alpha=8, `finetune_vision_layers=False` — WARNING: `use_cache=False` + gradient checkpointing corrupts KV-sharing on Gemma 4, must use Unsloth's fix
    - Chat template: `<start_of_turn>user/model<end_of_turn>` (not ChatML). Updated in `unsloth_exporter.py`, `quantizer.py`, all Modelfiles
    - Runtime profiles: t4_colab (default for E4B), l4_session, a100_session, local (2048 seq, fp16)
    - Aliases: `gemma4`, `gemma4-31b`, `gemma4-e4b`, `e4b` all resolve in MODEL_REGISTRY

48. **Plan Item 3: DeepTeam Red Teaming Bridge** (aeac30f):
    - NEW `able/security/deepteam_bridge.py` (~260 lines) — `DeepTeamBridge` class wraps ABLE gateway as `model_callback`. 16 vulnerability categories mapped to ABLE security layers (trust_gate, secret_isolation, command_guard, egress_inspector, etc.)
    - `ScanResult` with `block_rate` + `passed` (≥80%) properties. `DeepTeamReport.to_pentest_checks()` for PentestReport integration
    - Wired into `self_pentest.py`: `_run_optional_deepteam_scan()` gated by `ABLE_ENABLE_DEEPTEAM=1` env var + `deepteam` package availability
    - Weekly cron: `weekly-deepteam` at `0 4 * * 0` (Sunday 4am), 10 attacks/category, 900s timeout, awards buddy XP

49. **Test results after Items 1-3**: 651 passing → identified 3 pre-existing test failures.

50. **Phase 2 Partial: Durable Task Framework** (39ef60a, Plan Item 6):
    - NEW `able/core/execution/__init__.py` + `durable_task.py` (~270 lines): `DurableTask` ABC with `TaskCheckpoint`, `TaskResult`, `TaskContext` (checkpoint/retry/waitpoint), `TaskStore` (SQLite in `data/durable_tasks.db`), `TaskRunner`. Buddy XP awarded on checkpoint (+5), completion (+15), resume (+10).
    - NEW `able/core/execution/overnight_loop.py` (~200 lines): `OvernightLoop` orchestrator — iteration-commit-rollback, 3-consecutive-failure abort, exponential backoff (60s × 2^(N-1)), cross-iteration `notes.md`, per-run metadata in `data/overnight_runs/`.

51. **Phase 2 Partial: Buddy Gamification Wiring** (39ef60a):
    - `model.py`: 8 new XP constants (XP_DURABLE_TASK_CHECKPOINT through XP_BENCHMARK_PASS), 7 new badges (death-spiral-survivor, night-owl, red-team-leader, context-master, multi-agent, architect, safe-hands), 8 new need restoration sources (red_team_scan, overnight_iteration, durable_task, context_compact, overnight_cycle, tool_persist, edge_inference, monitor_recovery).
    - `xp.py`: 7 new award functions — `award_pentest_xp()`, `award_durable_task_xp()`, `award_overnight_xp()`, `award_managed_agent_xp()`, `award_monitor_recovery_xp()`, `award_benchmark_xp()`.
    - `battle.py`: 3 new battle functions — `run_deepteam_battle()`, `run_benchmark_battle()`, `log_benchmark_as_battle()`.
    - `renderer.py`: `render_compact_status()` — one-line widget format `[emoji Name L12 H:▓▓▓░ T:▓▓▓▓ E:▓▓░░]`.

52. **Phase 2 Partial: SSRF Hardening + Test Fixes** (39ef60a, Plan Item 8a):
    - `egress_inspector.py`: CGNAT range detection (100.64.0.0/10 — Python's `is_private` misses this), cloud metadata blocking (169.254.169.254, metadata.google.internal), `check_archive_traversal()` for tar/zip `../` paths, `validate_redirect_target()` for redirect re-validation.
    - `unsloth_exporter.py`: KV-sharing runtime guard — `warnings.warn()` when exporting Gemma 4 notebooks without Unsloth.
    - `__main__.py`: Public `build_parser()` wrapper for test access.
    - 3 test fixes: `test_registry_has_all_models` (count ≥4 + Gemma 4 assertions), `test_run_success_qwen` (individual modes vs `mode="all"`), `test_save_report` (async `_save_report` via `asyncio.run()`).
    - **Test results**: 828 passing, 0 failures.

53. **Phase 2 Complete: Managed Agents Provider** (Plan Item 7):
    - NEW `able/core/providers/managed_agent_provider.py` (~350 lines): `ManagedAgentProvider` with SSE streaming, stream-first pattern (open SSE before kickoff), lossless reconnect via `events.list()`, custom tools with host-side credential injection (ABLE keeps secrets).
    - `ManagedAgentSession` tracks session_id, events, token usage, cost ($0.08/session-hr). Correct idle-break: checks `stop_reason.type` not bare "idle" string.
    - Beta header: `managed-agents-2026-04-01`. SSE_MAX_RECONNECTS=5 with exponential backoff.
    - Wired into `provider_registry.py` as `managed_agent` provider type.
    - Added `managed-agent-opus` to `routing_config.yaml` as T4 priority 1 with fallback to `claude-opus-4-6` (Claude Code CLI).
    - Buddy XP: `award_managed_agent_xp()` called on session completion (already existed from Phase 2 partial).

54. **Phase 2 Complete: Structured Handoffs — Three Man Team** (Plan Item 9):
    - NEW `ThreeManTeamProtocol` class in `able/core/swarm/swarm.py` (~100 lines).
    - File-based artifact chain: PLANNER → PLAN-BRIEF.md → CODER → BUILD-LOG.md → REVIEWER → REVIEW-FEEDBACK.md.
    - Scope-lock discipline: step N+1 halts if step N fails. Sequential execution enforced.
    - Token optimization via `_STEP_READS`: PLANNER sees only goal+context, CODER sees only PLAN-BRIEF.md, REVIEWER sees PLAN-BRIEF.md + BUILD-LOG.md.
    - Role-specific prompts in `_STEP_PROMPTS` with concrete output format requirements.
    - Verdict extraction: parses REVIEW-FEEDBACK.md for PASS/REVISE/FAIL.

55. **Phase 2 Complete: Behavioral Benchmarks** (Plan Item 10):
    - NEW `provider_behavioral_audit()` in `able/core/evolution/auto_improve.py` (~180 lines).
    - 10 standardized `BEHAVIORAL_PROBES` (2 per failure mode) run through each provider tier.
    - 5 failure mode classifiers: thinking_bleed, empty_response, tool_refusal, format_violation, hallucinated_tool.
    - `_FAILURE_MODE_GUIDANCE` dict generates per-model-family execution guidance for system prompt injection.
    - `BehavioralAuditResult` dataclass per tier with pass_rate, failures, guidance.
    - Results persisted to `data/behavioral_audit/`. Buddy XP via `award_benchmark_xp()` per mode per tier.
    - Integration: called by evolution daemon weekly after interaction audit.
    - **Test results**: 828 passing, 0 failures. Codex audit: PASS.

56. **Streaming Tool Dispatch in Gateway** (d0b4f9c, Priority 3):
    - `stream_message()` now supports multi-turn tool dispatch. When authorized tools exist, uses `complete()` for tool iterations (yields `⚙️ [tool_name]` progress notifications), then `stream()` for the final text response.
    - When no tools are available, preserves original stream-first behavior (stream → fallback to complete on failure).
    - 10-iteration tool dispatch cap. Tool result persistence via `_maybe_persist()` integrated.
    - All existing streaming tests pass unchanged (stream-first fallback path preserved for toolless chains).

57. **Anthropic Provider Extended Thinking Streaming** (d0b4f9c, Priority 4):
    - `stream()` method now respects `extended_thinking` flag: sets beta header (`interleaved-thinking-2025-05-14`), thinking budget, temperature=1.
    - Captures `thinking_delta` events and yields as `<think>...</think>` markers for downstream filtering by `_StreamThinkFilter`.
    - Handles `content_block_start/stop` for `tool_use` blocks — accumulates tool calls during streaming (exposed via `_last_stream_tool_calls`).
    - `input_json_delta` reassembly for streamed tool arguments.
    - Accepts `tools` parameter (was missing from stream signature).

58. **battle.py Hardening** (d0b4f9c):
    - `run_deepteam_battle()`: Clamp `pct` BEFORE result classification (was after — raw value drove win/draw/loss).
    - `run_deepteam_battle()`: Guard `category_count < 1` → force to 1 (prevents `total=0` in BattleRecord).
    - Both `run_deepteam_battle()` and `run_benchmark_battle()`: NaN rejection via `math.isfinite()` — prevents silent corruption from upstream computation errors.
    - Codex audit: PASS (3 INFO items addressed: clamp-before-branch, category_count guard, NaN rejection).

    **Test results**: 872 passing, 0 failures. Codex audit: PASS.

59. **Tool Argument Sanitizer** (Plan A1 — CRITICAL security fix):
    - NEW FILE `able/core/security/arg_sanitizer.py` (~200 lines). Closes critical gap where TrustGate validates user messages but NOT tool arguments.
    - `sanitize_tool_args(tool_name, args) -> SanitizeResult` — per-tool-type rules: shell tools get metachar checks, file tools get traversal checks, URL tools get SSRF inspection.
    - Checks: null bytes, `../` path traversal, shell metacharacters (context-aware — blocked in file tools, warned in general, allowed in shell tools), SSRF metadata endpoints (`169.254.169.254`, `metadata.google.internal`), API key leakage (`sk-`, `ghp_`, `AKIA`), control character stripping.
    - Nested dict handling: only checks KEYS (paths), not values (file content naturally has shell syntax).
    - Raises `ToolArgRejected` for critical violations (null bytes, traversal on file tools, SSRF metadata).
    - Wired into `able/core/gateway/tool_registry.py` `dispatch()` — runs before every tool execution.
    - 23 tests in `able/tests/test_arg_sanitizer.py`.

60. **PII Redactor** (Plan A3):
    - NEW FILE `able/core/security/pii_redactor.py` (~100 lines).
    - `redact_pii(text) -> (redacted_text, list[RedactedField])` — pattern-based detection for email, phone (US + country code), SSN, credit card, API keys (OpenAI/GitHub/AWS/Slack/Google/GitLab prefixes).
    - Typed numbered placeholders: `[REDACTED_EMAIL_1]`, `[REDACTED_PHONE_1]`, etc.
    - `has_pii(text) -> bool` — quick check without full redaction.
    - Designed for T1/T2 external providers only — T4 (Claude) and T5 (local) exempt.
    - 14 tests in `able/tests/test_pii_redactor.py`.

61. **Advisor Strategy — Provider Support** (Plan A+1):
    - `AnthropicProvider.ADVISOR_TOOL_TYPE = "advisor_20260301"` class constant.
    - `advisor_tool(max_uses=3, advisor_model=None)` classmethod — returns server-side tool declaration for Sonnet→Opus advisory within a single API call.
    - `_convert_tools()` updated: `advisor_20260301` type passes through unchanged (server-side tool, not converted to function format).
    - `complete()` tracks advisor usage: `usage.advisor_usage` → `{"calls": N, "input_tokens": N, "output_tokens": N}` logged and attached to `CompletionResult`.
    - `CompletionResult.advisor_usage` field added to `able/core/providers/base.py`.

62. **Advisor Routing Config** (Plan A+2):
    - New provider entry `claude-sonnet-advisor` in `config/routing_config.yaml`: tier 2.5, Sonnet executor + Opus advisor, `advisor_enabled: true`, `advisor_max_uses: 3`, `advisor_fallback_only: true`.
    - Routing thresholds: `tier_advisor_min_score: 0.5`, `tier_advisor_max_score: 0.7`.
    - `advisor_fallback_only: true` — advisor routing only activates when API-priced providers are in use (subscription providers don't benefit from cost reduction).

63. **NextAuth Session Enforcement** (Plan A2):
    - NEW FILE `able-studio/middleware.ts` (~45 lines). Protects all `/api/*`, `/dashboard/*`, `/settings/*`, `/admin/*` routes.
    - Exempt: `/api/auth/*`, `/health`, `/login`, `/register`, `/_next`, static assets.
    - Unauthenticated requests redirect to `/login` with `callbackUrl` query param.
    - Uses NextAuth v5 `auth()` callback pattern (not `getToken`).

64. **Unit Tests for Production Modules** (Plan B1, B4):
    - NEW FILE `able/tests/test_durable_task.py` (14 tests): TaskCheckpoint serialization, TaskStore persistence (register/status/save/get/list_resumable), TaskContext checkpoint/retry/waitpoint, TaskRunner execute/resume lifecycle.
    - NEW FILE `able/tests/test_tool_result_storage.py` (11 tests): small/large output persistence, summary extraction, empty/none handling, read_file exemption, tool_use_id sanitization, turn budget enforcement.

    **Test results**: 934 passing, 0 failures (69 new tests: 23 arg sanitizer + 14 PII + 14 durable task + 11 tool result storage + 7 existing test improvements).

65. **Gateway Advisor Tool Injection** (Plan A+3):
    - When the selected provider has `advisor_enabled=True` in its config `extra`, the gateway injects `AnthropicProvider.advisor_tool()` into the `authorized_tools` array before the provider call.
    - Reads `advisor_max_uses` and `advisor_model` from provider config extra dict.
    - Injection logged at INFO level. Failure to inject is non-fatal (warning only).
    - Works for both `process_message()` vision and text-only paths (tools array is shared).

66. **Advisor Cost Tracking in Interaction Log** (Plan A+4):
    - 3 new columns on `InteractionRecord`: `advisor_input_tokens`, `advisor_output_tokens`, `advisor_calls`.
    - Migration columns added to `_MIGRATION_COLUMNS` (auto-added on first connection).
    - `update_result()` accepts `advisor_input_tokens`, `advisor_output_tokens`, `advisor_calls` params.
    - Gateway passes `result.advisor_usage` fields to interaction log after provider responds.
    - Enables evolution daemon to analyze: "which tasks used the advisor?" for routing optimization.

67. **ContextCompactor Unit Tests** (Plan B3):
    - NEW FILE `able/tests/test_context_compactor.py` (31 tests).
    - Covers: `estimate_tokens` (string, list, empty), `needs_compaction` (under/over threshold), `compact_if_needed` (basic reduction, tail preservation, summary structure), strip-thinking recovery (think blocks, internal reasoning, user preservation, mutation safety, skip-full-compaction), death spiral prevention (max attempts, no-op detection, counter reset), `is_context_length_error` (direct, 413, disconnect types, negatives), `_extractive_summary` (user requests, errors, empty), `snapshot_hash` (deterministic, content-sensitive), `_get_text` (string, list, empty).

68. **OvernightLoop Unit Tests** (Plan B2):
    - NEW FILE `able/tests/test_overnight_loop.py` (15 tests).
    - Covers: dataclass defaults, report success_rate (normal + zero), all-succeed run, git commit on success, git rollback on failure, 3-consecutive-failure abort, success resets failure counter, exception-as-failure, abort signal, notes accumulation + passing to task_fn, metadata persistence, exponential backoff timing (60s * 2^n formula), results collection.

    **Test results**: 980 passing, 0 failures (46 new tests: 31 context compactor + 15 overnight loop).

69. **Concurrent Tool Execution** (Plan E1):
    - Modified `gateway.py` tool dispatch: when multiple tool calls arrive in one LLM turn, runs them in parallel via `asyncio.gather()` instead of sequentially.
    - 3-phase design: (1) classify calls as blocked or executable (repeated call guard), (2) parallel execution via `asyncio.gather` for 2+ executable calls (single calls still sequential), (3) record/persist/assemble messages in original order. Exception isolation per tool — one failure doesn't block others.

70. **ExecutionMonitor Integration Tests** (Plan B6):
    - NEW FILE `able/tests/test_execution_monitor.py` (28 tests).
    - Covers: `_args_fingerprint` (empty, deterministic, order-independent, whitespace normalization, different args), `_text_similarity` (identical, empty, no overlap, partial), healthy analysis (few calls, diverse calls), spinning detection (hard spin, soft spin, not-spinning with different tools, terminate after many), thrashing detection (A-B-A-B pattern, no thrashing with 3 tools), output repetition (identical outputs = stall, diverse outputs = healthy), error loop (3 failures, success breaks loop, terminate after many), `get_summary` (empty, populated), dataclass defaults, priority ordering (spinning > thrashing, spinning > error_loop).

71. **T5 Cloud Advisor Escalation** (Plan A+5):
    - NEW FILE `able/core/gateway/t5_advisor.py` (~130 lines): `T5AdvisorState` tracks consecutive failures, empty outputs, and advisor budget. `maybe_escalate_to_advisor()` curates last 3 messages + task summary, sends to T4 (Opus) or T2 fallback chain with `max_tokens=700, temperature=0.3`.
    - Gateway wiring: state initialized when `scoring_result.selected_tier == 5`. Tool results tracked via `record_tool_result()`. Escalation triggers after 3+ consecutive tool failures or 2+ empty outputs. Advisor response injected as `[ADVISOR] {guidance}` system message. Max 2 advisor calls per session.
    - NEW FILE `able/tests/test_t5_advisor.py` (21 tests): state defaults, budget exhaustion, stuck detection (failures, empty outputs, resets), context curation (basic, truncation, limits), escalation (not stuck, no providers, stuck+provider, T4 preference, T2 fallback, provider failure, budget respect, correct params, empty guidance).

    **Test results**: 1029 passing, 0 failures (49 new tests: 28 execution monitor + 21 T5 advisor).

72. **Subscription-Aware Advisor Fallback** (Plan A+6):
    - Gateway routing override: when T4 subscription (CLI) is unavailable and complexity is 0.5-0.7, redirects to advisor-enhanced Sonnet provider (saves ~80% vs full Opus API). Uses `tier_advisor_min_score`/`tier_advisor_max_score` from routing config thresholds.
    - Injection guard: `advisor_fallback_only` flag checked before advisor tool injection — skips advisor when subscription provider is active (no cost benefit).
    - Completes the full A+1 through A+6 advisor strategy end-to-end.

73. **Subprocess I/O Standardization** (Plan A4):
    - NEW FILE `able/core/security/subprocess_runner.py` (~220 lines): `run()` sync and `async_run()` async execution with guardrails. `SubprocessResult` with success/timed_out/truncated properties. `_sanitize_env()` strips 18+ injection vectors (LD_PRELOAD, DYLD_*, JAVA_TOOL_OPTIONS, RUSTFLAGS, GIT_SSH_COMMAND, NODE_OPTIONS, KUBECONFIG, etc.) with per-call allowlist override. Output truncation with `[TRUNCATED — N bytes omitted]` marker. Default 30s timeout, 50KB output cap.
    - NEW FILE `able/tests/test_subprocess_runner.py` (34 tests): result properties, truncation logic, env sanitization (8 injection categories + prefix patterns + allowlist + safe preservation), sync execution (echo, exit codes, stderr, timeout, not-found, truncation, env stripping, cwd, stdin), async execution (echo, exit codes, timeout, env stripping, not-found, truncation, stdin).

74. **MemPalace 4-Layer Memory** (Plan C1):
    - NEW FILE `able/memory/layered_memory.py` (~220 lines): `LayeredMemory` class with 4 layers. L0 (~50 tokens): identity from `~/.able/memory/identity.yaml` + objectives. L1 (~500-800 tokens): auto-generated from learnings.md + HybridMemory with deduplication. L2 (on-demand): filtered retrieval via HybridMemory `search()`. L3 (deep): full semantic search with lower threshold + metadata. `wake_up()` returns L0+L1 (~170 tokens). `recall(query, depth)` for graduated retrieval. `get_stats()` for observability.
    - NEW FILE `able/tests/test_layered_memory.py` (30 tests): layer/config defaults, L0 loading (default, yaml, objectives, truncation, corrupt yaml), L1 loading (learnings file, truncation, empty sources, hybrid deduplication), L2/L3 query (empty without hybrid, queries hybrid, handles failure, metadata, lower threshold), wake_up (with files, with defaults), recall (depth 1/2/3), get_stats, get_layer.

    **Test results**: 1093 passing, 0 failures (64 new tests: 34 subprocess runner + 30 layered memory).

75. **Codex P1 Fixes** (Session 2026-04-11):
    - P1: Gateway role corruption — `Role.__members__.values()` contains enum objects, not strings. Fixed with try/except `Role(_role_str)`.
    - P1: MCP stdio framing — `run_stdio()` now supports both Content-Length framing (standard MCP) and bare JSON-per-line.
    - P1: `set_gateway()` wired to store gateway and propagate to handlers. `handle_message()` routes through gateway when available.
    - P2: Persistent shell stderr deadlock — concurrent stderr drain via asyncio task prevents pipe buffer deadlock.

76. **Wove Patterns — ReadTracker** (read-before-write enforcement):
    - NEW FILE `able/core/gateway/read_tracker.py` (~115 lines). Tracks file reads per session, blocks writes to unread existing files.
    - `check_write_large()` blocks full-file rewrites on files >200 lines, forces targeted edits.
    - LRU eviction at 500 tracked files. Symlink canonicalization prevents bypass.
    - 10 tests in `able/tests/test_read_tracker.py`.

77. **Claurst Patterns — Memory Freshness** (staleness warnings):
    - NEW FILE `able/memory/freshness.py` (~100 lines). Age-scaled caveats: <2d = fresh, 2-7d = verify warning, 7-30d = STALE, 30-90d = months stale, 90d+ = archival.
    - `annotate_memory()` prepends caveat to stale memory content.
    - Supports Unix timestamp, datetime, and ISO string inputs.
    - 14 tests in `able/tests/test_freshness.py`.

78. **Claurst Patterns — Effort Levels** (user routing control):
    - NEW FILE `able/core/routing/effort_levels.py` (~90 lines). 4 levels: low (force T1), medium (auto), high (bias up), max (force T4, session-scoped).
    - `apply_effort()` returns `EffortOverride` with adjusted score or forced tier.
    - `ABLE_EFFORT_LEVEL` env var support. MAX is session-scoped for cost protection.
    - 12 tests in `able/tests/test_effort_levels.py`.

79. **Claurst Patterns — Budget Tracker** (millicent-based):
    - NEW FILE `able/core/routing/budget_tracker.py` (~160 lines). Integer millicent tracking (1/100,000 USD) avoids floating-point accumulation errors.
    - Per-tier cost recording with breakdown. `suggest_downgrade()` auto-downgrades when budget <20%.
    - `ABLE_MAX_BUDGET_USD` env var. Budget exceeded → graceful downgrade, not hard stop.
    - 12 tests in `able/tests/test_budget_tracker.py`.

80. **D2 — RTK Token Compression** (previously blocked, Rust dep resolved):
    - NEW FILE `able/tools/rtk/wrapper.py` (~130 lines). Wraps shell commands through RTK for 60-90% token savings on tool outputs.
    - `should_compress()` identifies compressible commands (git, ls, find, docker, kubectl, etc.).
    - NEW FILE `able/tools/rtk/tracking.py` (~130 lines). SQLite analytics for compression savings per command prefix.
    - 12 tests in `able/tests/test_rtk_wrapper.py`.

81. **D8 — WebGPU Edge Inference** (previously blocked, TypeScript dep resolved):
    - NEW FILE `able-studio/lib/webgpu-inference.ts` (~230 lines).
    - WebGPU detection with VRAM estimation. Fallback chain: WebGPU → Ollama → Cloud.
    - Tool call parser: `<|tool_call>call:name{params}<tool_call|>` format (gemma-gem pattern).
    - Streaming support with callback. Feature flag: `NEXT_PUBLIC_ENABLE_WEBGPU=true`.

82. **F12 — Media Generation Fallback** (previously stretch goal):
    - NEW FILE `able/tools/media/generator.py` (~250 lines). Auto-fallback media generation.
    - Intent detection via regex patterns for image/audio/video requests.
    - Provider fallback chains: DALL-E 3 → placeholder (image), ElevenLabs → placeholder (audio).
    - `MediaGenerator` orchestrator with `available_providers()` introspection.
    - 13 tests in `able/tests/test_media_generator.py`.

    **Test results**: 3296 passing, 0 failures (73 new tests this batch).
    **Plan status**: 74/74 items implemented. 0 blocked.

---

## External Repo Adoptions (Session 2026-04-11)

**Claurst** (github.com/Kuberwastaken/claurst):
- Memory freshness/staleness warnings → `able/memory/freshness.py`
- User effort levels for routing control → `able/core/routing/effort_levels.py`
- Millicent-based budget tracking → `able/core/routing/budget_tracker.py`

**Wove** (github.com/mits-pl/wove):
- Read-before-write enforcement → `able/core/gateway/read_tracker.py`
- Large file protection (>200 lines blocks full rewrite)

**Wave 2 patterns shipped (2026-04-11, later session):**
- Claurst: layered config resolution → `able/core/config/layered_config.py`
- Claurst: named agent profiles → `able/core/agents/agent_profiles.py`
- Claurst: config schema validation at boot → `able/core/config/config_validator.py`
- Wove: output discipline guardrails → `able/core/gateway/output_discipline.py`
- Wove: sub-task isolation → `able/core/execution/subtask_isolator.py`

83. **Edit Precision Scorer Bug Fix**:
    - `interaction_auditor.py` `_edit_precision_score()` had inverted scoring: wasteful rewrites got 1.0 (high), surgical edits got ~0 (low). Fixed to `1.0 - change_ratio`.

84. **Docker/Cron Resilience**:
    - `sqlite_store.py`: zstandard import now optional (graceful fallback to raw bytes). Fixes memory init crash on slim Docker.
    - `requirements-core.txt`: Added `zstandard>=0.23.0` and `beautifulsoup4>=4.12.0` for slim profile.
    - `doctor.py`: Added zstandard, dspy-ai, beautifulsoup4 to dependency checks.
    - Gateway startup: Doctor health check runs at boot, prints errors/warnings to console.

85. **Unsloth Studio Findings Applied**:
    - Pinned `unsloth>=2026.4.3` in exporter (Gemma 4 gradient accumulation fix + Qwen 3.5 stability).
    - Added Gemma 4 loss range warning (10-15 is normal) to notebook.
    - Added merged GGUF export option (full fine-tuned models, not just LoRA adapters).
    - HuggingFace API throttle reduction (env vars).
    - Updated model config descriptions with Gemma 4 E4B 8GB VRAM note.

86. **WebGPU TS Build Fix**: `@webgpu/types` added to devDependencies, tsconfig types array.

87. **SKILL_INDEX.yaml**: media-generation + rtk-compress skills registered.

88. **Full Doc Audit**: ABLE.md, CLAUDE.md, README.md, ROUTING.md synced — Gemma 4 31B replaces Nemotron, Managed Agents + Sonnet Advisor + Qwen 3.6 Plus added.

    **Test results**: 3370 passing, 0 failures.

**Remaining high-value patterns (future work):**
- Claurst: plugin capability declarations, deterministic gacha bones/soul split, AutoDream memory consolidation
- Wove: three-tier context architecture (tech-tag filtering), repo map via tree-sitter, sibling file reference

---

## Current Patch (2026-04-24)

**Cron duplicate-fire hardening**
- Fixed startup recovery's empty-DB detector. The old `get_job_stats("__any__")` check always returned 0, so every restart behaved like a fresh DB and could recover daily notification jobs again.
- Replaced current-minute dedupe with durable scheduled-run claims in `job_run_claims(job_name, run_slot)`.
- Recovery now claims the actual missed scheduled slot, not the wall-clock minute when recovery runs. Example: `nightly-research` scheduled at 1am keeps the same idempotency key whether recovered at 1:01, 9:00, or after deploy.
- Empty-DB recovery is disabled by default to prevent stale Telegram floods after reinstall/path changes. Optional override: `ABLE_CRON_EMPTY_DB_RECOVERY_HOURS`.
- Scheduler heartbeat and morning-report cron history now use the scheduler DB path under `able/data/`, matching the Docker `able_db` volume.
- Added `able/tests/test_cron_claims.py`: duplicate scheduler instances, empty-DB recovery suppression, recovery slot identity, stale lease takeover.
- Added a cron/Telegram leader gate: `ABLE_CRON_ENABLED=1` is required before the gateway registers/runs cron jobs or the continuous evolution daemon; `ABLE_TELEGRAM_ENABLED=1` is required before Telegram polling unless cron leader mode implies it. Deploy scripts set both on the server; local/dev runs default to follower mode.
- Added Telegram webhook mode: `ABLE_TELEGRAM_MODE=webhook` registers `ABLE_TELEGRAM_WEBHOOK_URL` through Telegram `setWebhook`, adds `POST /webhook/telegram` on the gateway HTTP plane, and avoids `getUpdates` polling conflicts. `ABLE_TELEGRAM_WEBHOOK_SECRET` validates Telegram's secret-token header.
- Added `scripts/setup-telegram-webhook-https.sh` and `docs/TELEGRAM_WEBHOOK.md` so production can get a public HTTPS endpoint with Caddy. The no-domain path uses `<public-ip>.sslip.io`; custom domains are also supported.
- Deploy now preserves existing server webhook env values when the corresponding GitHub secrets are blank, preventing a later deploy from silently reverting the server back to polling mode.
- Added `docs/OPTIMIZATION_ROADMAP.md`, summarizing the deep-research reports as ABLE-specific follow-up work: stable-prefix prompt layout, bounded tool budgets, tiered capture/training artifacts, optional Arrow/Zstd evaluation, and Studio/media profiling.
- `github-digest` no longer sends a Telegram "Skipped — GITHUB_TOKEN not set" message. Missing optional config is logged only.
- Added `able/tests/test_cron_leader_gate.py`: env gate defaults, explicit leader mode, Telegram mode/webhook routing, and no Telegram delivery for missing GitHub token.

Validation run this patch:
- `python3 -m py_compile able/scheduler/cron.py able/core/evolution/morning_report.py`
- `python3 -m pytest able/tests/test_cron_claims.py -q`
- `python3 -m pytest able/tests/test_evolution_scheduler.py -q`
- `python3 -m pytest able/tests/test_cron_leader_gate.py able/tests/test_cron_claims.py able/tests/test_control_plane.py -q`
- `bash -n scripts/setup-telegram-webhook-https.sh deploy-to-server.sh`

---

## Previous Work (same session, earlier)

42. **Phase 0 Critical Fixes — Gateway Robustness** (Plan Items 0b, 0c):
    - **Context compactor wired into gateway** (`gateway.py`): Before each LLM call in the tool loop, messages are checked against the provider's `max_context` limit. When at 80% capacity, `ContextCompactor.compact_if_needed()` runs extractive summarization on the oldest 60%.
    - **Death spiral prevention** (`context_compactor.py`): Hard cap of 3 compression attempts per session. Each attempt verifies `len(result) < original_len` — if compression didn't reduce, breaks immediately. `reset_compression_counter()` at session start.
    - **Strip-thinking recovery**: Before full compaction, strips `<think>...</think>` and `[Internal reasoning]` blocks from assistant messages. If stripping alone reclaims enough space, skips full compaction (cheaper, preserves more context).
    - **Disconnect reclassification**: `is_context_length_error()` recognizes `RemoteProtocolError`, `ServerDisconnectedError`, `ConnectionResetError`, `ReadTimeout` as disguised context-length failures (providers disconnect on oversized payloads instead of returning 413).
    - **Min-tail protection**: Always preserves at least 3 recent messages during compaction — prevents losing active conversation context.
    - **413 auto-compress + retry**: Provider errors caught in gateway tool loop, auto-compacts and retries when `is_context_length_error()` returns True.

43. **Phase 1 Hermes Quick Wins** (Plan Items 4a–4e, 5):
    - **Activity-based timeout** (4a, Hermes v0.8 PR #5389): Replaced fixed 15-iteration budget with 20-iteration activity-aware timeout. Tracks `_last_activity_ts` and `_last_activity_desc`. Hard budget pressure at iteration 17, idle pressure at >60s inactivity + iteration ≥8. Active agents never get killed prematurely.
    - **Tool result persistence** (4b, Hermes PR #5210 + #6085): NEW FILE `able/core/gateway/tool_result_storage.py` (~150 lines). 3-layer defense: Layer 2 `maybe_persist_tool_result()` saves outputs >4000 tokens to `data/tool_results/`, replaces inline with pointer + summary. Layer 3 `enforce_turn_budget()` spills largest outputs to disk when turn total exceeds 200K chars. `read_file`/`Read` exempt (prevents infinite loops). `cleanup_old_results()` removes files >24h old.
    - **Thinking-only prefill** (4c, Hermes PR #5931): When model produces `<think>` content but no user-facing text, thinking is appended as assistant prefill and the loop re-runs (max 2 retries). Prevents wasted iterations.
    - **Strip-thinking recovery** (4d, gemma-gem): Implemented in `ContextCompactor._strip_thinking_blocks()`. Regex strips `<think>...</think>` and `[Internal reasoning]...[/Internal reasoning]` from assistant messages before full compaction.
    - **Repeated tool call guard** (4e, mini-coding-agent): Pre-dispatch check uses `_args_fingerprint()` to detect last 2 tool calls with same name + args. Blocks with `[BLOCKED] Repeated tool call` message forcing different approach. Lightweight complement to full ExecutionMonitor.
    - **Background notification queue** (5, Hermes PR #5779): `CronScheduler.completion_queue` (`queue.Queue`), `notify_on_complete: bool = False` on `CronJob` dataclass. Gateway `_drain_completion_queue()` method pulls completions after each turn, injects as system messages.

44. **Test fix**: `test_reasoning_preview_extracts_think_blocks` — pre-existing failure from `_ReasoningPreview` → `_ReasoningCapture` rename. Fixed `limit=40` param removal and assertion update for `captured_thinking` attribute.

45. **Test results**: 823 passing (2 pre-existing failures in unrelated modules), 0 regressions from these changes.

> Previous session history (Items 1-41) archived to `CHANGELOG.md`.

## Next-Run Objectives

### Priority 0: Phase 2 Architecture — ALL DONE

Phase 0 (gateway robustness), Phase 1 Items 1-5 (TurboQuant, Gemma 4, DeepTeam, Hermes quick wins) — DONE.
Phase 2 ALL DONE: Items 6 (durable tasks), 7 (managed agents), 8a (SSRF), 9 (structured handoffs), 10 (behavioral benchmarks), buddy gamification wiring.

### Priority 1: Live production verification

Run the now-hardened deploy path against production and verify the real operator path end-to-end:
- activate Telegram webhook mode on production with `scripts/setup-telegram-webhook-https.sh`, then confirm `/health` reports `telegram_mode=webhook`, `telegram_polling_enabled=false`, and no `getUpdates` conflicts in logs
- confirm the deployed container sees `/home/able/.able/auth.json`
- confirm tier 1 resolves to `gpt-5.4-mini` on the live server, not Nemotron
- send a real Telegram buddy query (`How's <buddy>?`) and verify it dispatches the buddy tool path
- confirm the new CI smoke stays green on PRs and main pushes

### Priority 1: Studio fully wired ✓ (completed 2026-04-07)

All 10 Studio API routes built and operational. Gateway has `/api/buddy`, `/metrics/*`, `/events` (SSE), and `/api/chat`. Studio chat now routes through TrustGate → enricher → interaction_log → distillation. Corpus metric card and live events feed on dashboard.

### Priority 2: ASR backend configuration

The pluggable ASR interface is ready. Next step: configure the operator's preferred audio-native model (Voxtral or Qwen3) as the `ABLE_ASR_ENDPOINT`, test with real audio from Telegram and CLI, and verify transcription quality.

### Priority 3: Streaming for tool-dispatch iterations ✓ (completed 2026-04-09)

`stream_message()` now supports multi-turn tool dispatch: `complete()` for tool iterations with progress notifications, `stream()` for final text response. Stream-first fallback preserved when no tools available.

### Priority 4: Provider-level reasoning streaming ✓ (completed 2026-04-09)

Anthropic `stream()` now supports extended thinking: beta header, thinking budget, `thinking_delta` → `<think>` markers, tool call accumulation. CLI `_StreamThinkFilter` already handles the markers downstream.

### Priority 5: Federation live setup

Configure the `able-network-corpus` GitHub repo and `GITHUB_TOKEN` for live federation sync:
- Create the GitHub repo for network corpus distribution
- Test a full contribution → publish → fetch → ingest cycle end-to-end
- Verify PII scrubbing and TrustGate rejection on real data
- Confirm `tenant_id='network'` pairs flow through `CorpusBuilder` correctly

### Priority 6: First Colab training run

Run the first real Unsloth fine-tuning using the current corpus:
- **Recommended target**: Gemma 4 E4B (10GB QLoRA fits free T4 perfectly)
- Export a notebook: `UnslothExporter().export_notebook("able-gemma4-e4b", corpus_path)`
- Upload to Colab, connect free T4 runtime, execute all cells
- Quantize to UD-IQ2_M (1.8GB) for deployment on M2 8GB
- Validate GGUF output loads in Ollama
- Compare fine-tuned E4B vs base on reasoning + tools eval configs
- Document real training time and memory usage for the handoff
- Alternative: `export_notebook("9b", ...)` for Qwen 3.5 9B (12GB, still fits T4)

### Priority 7: Distillation corpus growth

Push toward 100+ pairs for H100 fine-tuning:
- Run reasoning + tools eval configs to generate T4 gold outputs
- Monitor corpus pair count (`/eval` in CLI)
- Verify interaction logger correctly marks corpus-eligible interactions
- CLI sessions now feed the pipeline automatically — every `able chat` conversation is harvestable
- External tool sessions can be dropped in `~/.able/external_sessions/` for cross-tool learning
- Federation network pairs supplement local corpus automatically after sync

### Priority 8: Keep docs and runtime in lockstep

- Refresh `README.md` only when code changes make its current commands stale.
- Keep `CODE_HANDOFF.md` and `NEXT_RUN_PROMPT.md` updated at the end of each pass.

### Priority 9: Promote optional systems only when justified

- Keep billing, channels, ASR, Strix, and federation live sync off the default hot path unless they are explicitly configured or a real operator-facing entrypoint is being shipped.
- When one of those systems becomes active work again, modernize it on its own merits instead of silently letting it drift back into the startup path.

### Priority 10: Remaining roadmap from executor research

Still on the roadmap (saved for future sessions):
- **Structured subprocess JSON I/O protocol** — Replace raw stdout parsing with `{ "status": "ok", "data": {...} }` contract on CLI tool invocations (Plan A4: SubprocessRunner)
- **Elicitation/interactive approval flows** — Tools can pause, collect structured user input via forms, then resume (richer than approve/deny)
- **Python 3.14 JIT streaming for hot paths** — Leverage JIT compilation for hot paths alongside WebSocket improvements
- ~~**Studio NextAuth session checks**~~ ✓ Done (Item 63): `able-studio/middleware.ts` enforces auth on all protected routes
- ~~**Morning briefing / nightly research double-execution diagnosis**~~ ✓ Fixed: startup recovery no longer always treats DB as empty; scheduled/recovery jobs now use durable `job_run_claims(job_name, run_slot)` idempotency.

### Priority 10.5: Master Plan — Next immediate items

See full plan at `.claude/plans/luminous-wibbling-pie.md` (79 items across 7 tracks).

Completed: A1, A2, A3, A4, A+1, A+2, A+3, A+4, A+5, A+6, B1, B2, B3, B4, B6, C1, E1.

**Next up (high value)**:
- **B5**: AutoImprover E2E pipeline test
- **C2**: Temporal knowledge graph — fact lifecycle management
- **C3**: Smart search pipeline — BM25+vector+rerank fusion
- **D1**: Claude Agent SDK integration — replace manual T4 tool loop
- **A5-A8**: Remaining security (enhanced SSRF, env sanitization, plugin hardening, smart approvals)
- **C4**: Recency-weighted context compression
- **C5**: Memory dreaming / REM (offline consolidation)

### Priority 11: End-to-end system hardening

- Verify all cron jobs execute successfully on the production server
- ~~Confirm Phoenix receives spans from gateway calls (systematic tracing)~~ ✓ Fixed: tracer retries every 5min, flushes no-op cache on late connect
- ~~Validate Trilium receives research findings + knowledge graphs~~ ✓ Fixed: `ensure_parent()` auto-creates notes, silent failures now warn
- ~~Historic data uploaded to Trilium~~ ✓ Fixed: weekly `trilium-historic-upload` cron job (Sunday 3am)
- Test eval collection → self-improvement feedback loop end-to-end
- Ensure buddy auto-care keeps mood above "hungry" without user intervention for 48h+

## Validation Commands

```bash
able --help                                     # Verify global command works
able chat --help                                # Verify chat subcommand
cd /tmp && ~/.local/bin/able chat --help        # Verify wrapper works outside repo root
cd /tmp && printf '/q\n' | ~/.local/bin/able chat --control-port 0
cd /tmp && printf '/resources\n/q\n' | ~/.local/bin/able chat --control-port 0
cd /tmp && printf '/battle\n/q\n' | ~/.local/bin/able chat --control-port 0
cd /tmp && printf '/compact\n/q\n' | ~/.local/bin/able chat --control-port 0
python3 -m pytest able/tests/test_cli_chat.py -x
python3 -m pytest able/tests/test_cron_claims.py -q
python3 -m pytest able/tests/test_cron_leader_gate.py -q
python3 -m pytest able/tests/test_provider_registry_primary.py able/tests/test_telegram_buddy_dispatch.py -x
python3 -m pytest able/tests/test_package_layout.py able/tests/test_runtime_boundaries.py -x
python3 -m pytest able/tests/test_buddy.py -q
python3 -m pytest able/tests/test_weekly_research.py -x
python3 -m pytest able/tests/test_control_plane.py able/tests/test_resource_tools.py able/tests/test_learning_loops.py able/tests/test_collect_results.py able/tests/test_evolution_cycle.py -x
python3 -m pytest able/tests/test_gateway_metrics.py -x
# Phase 2.5 security + advisor + production module tests:
python3 -m pytest able/tests/test_arg_sanitizer.py able/tests/test_pii_redactor.py able/tests/test_durable_task.py able/tests/test_tool_result_storage.py -x -v
# Phase 3 tests (execution monitor, T5 advisor, context compactor, overnight loop):
python3 -m pytest able/tests/test_execution_monitor.py able/tests/test_t5_advisor.py able/tests/test_context_compactor.py able/tests/test_overnight_loop.py -x -v
# Full suite (1093 total):
python3 -m pytest able/tests/ -x -q
cd able-studio && pnpm build
bash -n deploy-to-server.sh
bash -n install.sh
docker compose build
docker compose up -d
curl http://localhost:8080/health
```

For targeted runs:
```bash
python3 -m pytest able/tests/test_buddy.py -x                # Buddy + onboarding + backpack + rarity + orchestration bonuses
python3 -m pytest able/tests/test_cli_chat.py -x             # CLI + streaming + slash-command UX
python3 -m pytest able/tests/test_weekly_research.py -x       # Research report persistence
python3 -m pytest able/tests/test_harvesters.py -x            # Distillation harvesters + scaffolding strip
python3 -m pytest able/tests/test_federation.py -x -v         # Federation network + PII scrub + ingestion + Unsloth exporter
python3 -m pytest able/tests/test_evolution_split_tests.py -x  # Evolution daemon
```

## Cross-Agent Collaboration Protocol

**Before starting work:**
1. Read this file first
2. Check `git log --oneline -10` for recent changes
3. Read `NEXT_RUN_PROMPT.md` for the current reusable next-run prompt
4. Read `CLAUDE.md` only if session-specific Claude context is needed
5. Run `able chat --help` to verify the runtime is intact

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
- Update `CODE_HANDOFF.md` and `NEXT_RUN_PROMPT.md` so the next run starts from the actual current state

**Conventions:**
- No marketing copy — factual and operator-facing only
- Quant sizes are pinned — do not change without re-measuring
- Trust the routing_config.yaml for budget/tier numbers, not ABLE.md
- The README documents current state, not roadmap
