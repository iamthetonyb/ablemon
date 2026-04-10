# ABLE — Code Handoff

Date: 2026-04-07
Branch: `main` is the current production baseline.
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
│   ├── distillation/              # 27B and 9B training configs
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
- `able-gemma4-31b`: `UD-Q4_K_XL` = 18.8 GB | `Q5_K_M` = 21.0 GB | `Q8_0` = 31.0 GB
- `able-gemma4-e4b`: `UD-Q4_K_XL` = 3.2 GB | `UD-IQ2_M` = 1.8 GB

Config source of truth:
- `config/distillation/able_student_27b.yaml`
- `config/distillation/able_nano_9b.yaml`
- `able/core/distillation/training/model_configs.py`

Training lanes:
- **27B**: H100-only, seq_len=8192, micro_batch=1, bf16
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
- **Morning briefing double-execution diagnosis** — No code duplication found; may be runtime issue (gateway restarting?)

### Priority 10.5: Master Plan — Next immediate items

See full plan at `.claude/plans/luminous-wibbling-pie.md` (79 items across 7 tracks).

Completed: A1, A2, A3, A+1, A+2, A+3, A+4, B1, B2, B3, B4.

**Next up (high value)**:
- **A+5**: T5 local model cloud advisor escalation — Opus guidance for stuck Ollama models
- **A+6**: Subscription-aware advisor fallback — activate advisor only when API costs apply
- **B5-B6**: AutoImprover E2E + ExecutionMonitor integration tests
- **C1**: MemPalace 4-layer memory (~170 token wake-up from unbounded)
- **C2**: Temporal knowledge graph — fact lifecycle management
- **D1**: Claude Agent SDK integration — replace manual T4 tool loop
- **E1**: Concurrent tool execution — asyncio.gather for independent tool calls
- **A4-A8**: Remaining security (subprocess runner, enhanced SSRF, env sanitization, plugin hardening, smart approvals)

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
python3 -m pytest able/tests/test_provider_registry_primary.py able/tests/test_telegram_buddy_dispatch.py -x
python3 -m pytest able/tests/test_package_layout.py able/tests/test_runtime_boundaries.py -x
python3 -m pytest able/tests/test_buddy.py -q
python3 -m pytest able/tests/test_weekly_research.py -x
python3 -m pytest able/tests/test_control_plane.py able/tests/test_resource_tools.py able/tests/test_learning_loops.py able/tests/test_collect_results.py able/tests/test_evolution_cycle.py -x
python3 -m pytest able/tests/test_gateway_metrics.py -x
# Phase 2.5 security + advisor + production module tests:
python3 -m pytest able/tests/test_arg_sanitizer.py able/tests/test_pii_redactor.py able/tests/test_durable_task.py able/tests/test_tool_result_storage.py -x -v
# Full suite (884 expected with ignores, 934 total including routing+gateway):
python3 -m pytest able/tests/ -x --ignore=able/tests/test_routing.py --ignore=able/tests/test_gateway.py -q
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
