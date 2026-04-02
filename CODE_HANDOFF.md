# ABLE — Code Handoff

Date: 2026-04-02
Branch: `main` is the current production baseline. `codex/buddy-ui-and-orchestration` is the active working branch for the latest CLI and buddy UX pass.
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
├── CLAUDE.md                      # Optional Claude Code session context
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

Config source of truth:
- `config/distillation/able_student_27b.yaml`
- `config/distillation/able_nano_9b.yaml`
- `able/core/distillation/training/model_configs.py`

Training lanes:
- **27B**: H100-only, seq_len=8192, micro_batch=1, bf16
- **9B**: T4-first default, seq_len=2048, micro_batch=1, fp16, checkpoint every 100 steps

## What Was Just Completed

1. **All four learning feedback loops are now closed:**
   - **Eval → Self-Improvement**: `auto_improve.py` maps skill/content failures into approval-gated SKILL.md updates via `SelfImprovementEngine`.
   - **Proactive → Evolution**: `proactive.py` submits recurring failure insights to the collector via `submit_insight()`.
   - **Memory → Evolution**: `collector.py` now queries `HybridMemory` for durable learnings (LEARNING + SKILL types) and enriches the metrics package with `memory_context` before the analyzer sees it. Gateway and cron both pass memory to the daemon.
   - **Interaction → Distillation**: `collect_results.py` captures T4 gold outputs and emits CORPUS READY at 100+ pairs.
2. **Resource lifecycle tool**: `resource_action` in `able/core/gateway/tool_defs/resource_tools.py` with approval gating.
3. **Control-plane hardened**: all endpoints token-gated, `perform_action()` requires `service_token_verified`, HTTP status codes corrected.
4. **Operator slash commands expanded**: `/resources`, `/eval`, `/evolve` added to `able chat`. README updated.
5. **Test coverage**:
   - Buddy suite: 60 tests passing
   - CLI chat: 17 tests passing (added input validation, rate limiting, oversized message tests)
   - Routing tests: 50 tests (added circuit breaker + stream fallback tests)
   - Focused new-surface suite: 23 tests across control plane, resource tools, learning loops, collect_results, and evolution cycle passing
   - Full suite: 662 tests, 0 deprecation warnings
6. **Legacy cleanup**: all 87 bare imports migrated, 5 root-level shims removed, pyproject.toml simplified.
7. **Buddy system (system-wide)**: Pokemon + Tamagotchi gamified agent companion in `able/core/buddy/`:
   - 5 starter species (Blaze/Wave/Root/Spark/Phantom) with domain bonuses
   - A late-game hidden unlock exists outside the starter pool; keep it out of user-facing docs and pre-unlock UI so operators discover it organically
   - Interactive starter selection is now mandatory for first-time setup; legacy auto-created starters are forced back through selection before the buddy system is considered initialized
   - Post-selection onboarding stores operator profile (`focus`, `work_style`, `distillation_track`) so the buddy setup reflects actual domain needs instead of just mascot choice
   - `work_style` now includes `all-terrain` for operators who move across solo build, delivery, ops, and collaboration instead of fitting one narrow mode
   - 3 evolution stages tied to real milestones (interactions, eval passes, distillation pairs, evolution deploys)
   - XP from real interactions (complexity-weighted), tool use, approvals, battles
   - **Rarity layer**:
     - Shiny starter hatch is a deterministic rare cosmetic variant at creation time
     - Legendary form is earned only from real runtime milestones: stage 3, level 40, eval passes, battle wins/streak, distillation pairs, and evolution deploys
   - **XP awards in the gateway** — fires on ALL channels (Telegram, CLI, API), not just CLI
   - Battle system runs real promptfoo evals — wins feed distillation, losses identify skill gaps
   - Collection/backpack layer: catch the full starter roster through real domain work, rotate active buddies with `/buddy switch <name>`, inspect the roster with `/buddy bag`, and unlock hidden late-game content without pre-unlock spoilers
   - Badge ladder now includes `Trainer` (first real evolution) plus hidden endgame badges that should remain undisclosed in user-facing copy until earned
   - Starter selection plus onboarding on first `able chat` run, `/buddy`, `/buddy bag`, `/buddy setup`, `/buddy switch`, and `/battle` slash commands
   - CLI renderer now shows shiny/legendary badges, streak progress, legendary unlock state, richer species context, and a clearer operator profile/backpack summary
   - Evolution announcement bug fixed: old/new stage names now render correctly
   - **Needs/Tamagotchi layer**: hunger/thirst/energy decay over time, restored by real actions:
     - Hunger → feed by running `/battle` (evals = food)
     - Thirst → water by running `/evolve` or chatting (interactions = sips)
     - Energy → walk by exploring new domains and using varied tools
   - Mood system (thriving/content/hungry/neglected) with context-aware messages
   - **Telegram nudges**: buddy status appended to responses when needs are low
   - **Proactive engine**: `BuddyNeedsCheck` runs every 2h, dispatches nudge notifications
   - Nudge module (`able/core/buddy/nudge.py`) for cross-channel care reminders
   - **Autonomous buddy progression** (cron-driven, not just interactive):
     - `buddy-walk` cron job runs every 2h: applies needs decay, awards 5 passive XP, restores 8 energy via `self_explore` walk, checks evolution/legendary
     - Evolution daemon awards 30 XP + thirst on successful weight deploy
     - Nightly distillation awards 3 XP per new pair harvested
     - Nightly/weekly research counts as domain exploration (research domain XP + energy)
     - Autopilot awards XP for objectives + distillation pairs generated
     - Morning report appends buddy name, level, and mood to Telegram summary
     - Telegram nudges fire on evolution, legendary unlock, level-up, or low mood during autonomous runs
   - **Starter selection locked to 5 species only** — Aether cannot be picked during setup (was a bug where `list(Species)` gave 6 options including the hidden unlock)
   - Aether's internal specialty is now orchestration-first (`Psychic` type, swarm planning, buddy routing, signal fusion) so its XP bonuses align with real multi-step and delegated work instead of generic interactions
   - **Exit handling in setup flow**: `/exit`, `/quit`, `/q` work at every prompt during buddy setup (starter pick, name, catch phrase, onboarding options)
   - **SlashCtx refactor**: `_handle_slash` now takes a `SlashCtx` context object instead of 18 positional args
   - 60 buddy tests covering model, persistence, backpack collection, badges/late-game progression, rendering, battles, rarity, XP engine, needs, mood, autonomous tick, orchestration bonuses, and repo-root-independent battle discovery
8. **Streaming output for `able chat`**:
   - Gateway: `stream_message()` async generator runs full pipeline then streams AI response
   - CLI: tokens printed as they arrive via `async for chunk in gateway.stream_message(...)`
   - Fallback to `process_message()` if streaming fails (e.g., tool-heavy requests)
   - `--no-stream` flag to disable streaming when needed
   - Streaming is text-only (no tool dispatch) — tools still use `process_message()`
9. **Rich CLI approval rendering**:
   - Risk level icons and visual bars (low/medium/high/critical)
   - Affected resources extracted from details and highlighted
   - Truncated detail display with clear separators
10. **Distillation quality improvements**:
    - Harvester: prefers corpus_eligible rows, uses raw_input over preview
    - Prompt bank: domain alias normalization ("code"→"coding"), dedup on load/add
    - New eval configs: `eval-reasoning.yaml` (7 tests), `eval-tools.yaml` (7 tests)
    - Battle system: reasoning + tools domains wired for `/battle`
11. **Test fixes**:
    - Morning reporter test: correct table name (`interaction_log` not `interactions`)
    - Split test daemon: fully-qualified import patches after shim removal
    - Total: 602 tests passing across the full test suite
12. **Deploy hardening**:
    - Git operations on the DigitalOcean host now run as the `able` user in both `.github/workflows/deploy.yml` and `deploy-to-server.sh`
    - Existing `/opt/able/ABLE` working trees are re-owned by `able` before clone/fetch/checkout
    - This fixes Git's `detected dubious ownership in repository at '/opt/able/ABLE'` failure without weakening `safe.directory`
13. **Clean terminal experience for `able chat`**:
    - **Log suppression**: all logging set to ERROR by default on startup — eliminates ~40 lines of provider/ollama/enricher noise. `--verbose` flag restores full logging.
    - **Claude Code-style header**: `render_header()` places buddy ASCII art mascot on the left with name, level, XP bar, stage, mood, needs, and battle record on the right — mirrors Claude Code's robot mascot layout.
    - **Required interactive setup**: first-run interactive sessions must complete starter selection plus onboarding before the buddy system is considered initialized. Non-interactive CLI sessions still skip the flow so scripted smokes do not block.
    - **Graceful no-buddy**: all buddy code paths handle `buddy is None` without blocking or crashing.
    - 74 focused CLI/buddy tests passing (14 CLI chat + 60 buddy).
14. **One-command installer and global `able` command**:
    - `install.sh`: checks Python 3.11+ (auto-installs via brew/apt/dnf if missing), creates `.venv`, installs deps + package, places `able` and `able-chat` wrappers in `~/.local/bin/`, adds to PATH if needed, runs `able-setup.sh` for workspace init.
    - `able` wrapper at `~/.local/bin/able` is now repo-backed (`PYTHONPATH=<repo>` + venv Python), so it works from outside the repo root instead of relying on a fragile editable-console-script path.
    - Bare `able` in an interactive terminal now defaults to `chat` (not `serve`). Non-interactive (systemd/cron) still defaults to `serve`.
    - README rewritten with "Quick Start" section: `git clone`, `cd ABLE`, `bash install.sh` — then `able` works.
    - Added GitHub Actions installer smoke workflow to catch wrapper regressions.
15. **Terminal UX overhaul**:
    - **Phoenix skipped in CLI**: gateway accepts `skip_phoenix=True`, CLI passes it — eliminates Phoenix/gRPC/OTel startup spam entirely (was ~20 lines of print output).
    - **Warnings suppressed**: `warnings.filterwarnings("ignore")` + stderr redirect during gateway init catches SAWarning, DeprecationWarning, and any remaining print() noise.
    - **Double response root fix**: `gateway.stream_message()` now falls back to `complete()` only if the stream fails before the first chunk. Partial streamed output is preserved without re-fetching and duplicating the answer.
    - **Thinking spinner**: animated braille dots while waiting for first token — clears when streaming starts.
    - **Line editing + history**: interactive prompts now enable `readline` history and cursor editing, so arrow keys work during chat and buddy setup instead of printing escape sequences.
    - **ANSI color**: green `>` prompt, cyan `able` prefix on responses, dim timestamps and help text. Respects `NO_COLOR` env var.
    - **Response timing**: `[1.2s]` shown after each response.
    - **Cleaner prompt**: `> ` instead of `you> `, slash commands get formatted help table.
    - **Slash command shortcuts**: `/q` for quit, `/h` for help, `/?` for help, `/clear` to redraw the terminal with full scrollback preserved, and `/compact` to clear the view and print a compact session recap without losing transcript/distillation value.
    - **Quieter local default**: `able chat` now defaults to `--control-port 0`, so transient local chat sessions do not boot or print the health/control server unless explicitly requested.
    - **Clean exit**: `ABLEGateway.aclose()` now closes provider sessions, web-search sessions, the shared Studio `aiohttp` session, and the health server runner; `/exit` no longer leaks unclosed client sessions on shutdown.
    - **Starter selection revamp**: first-run buddy setup now explains each starter in plain language (`element`, `role`, `best for`, `abilities`), requires a real pick in interactive sessions, and follows it with operator onboarding for focus/work style/distillation preferences.
    - `/resources` now calls the real resource-plane inventory method (`list_resources()`), fixing the broken `get_inventory` path in chat mode.
    - `/battle` now resolves eval config paths relative to the repo root, so battle domains are discoverable even when `able` is launched from `/tmp` through `~/.local/bin/able`.
    - CLI status/header labels now spell out battle stats as `Wins`, `Draws`, and `Losses`, and `/status` shows the active provider roster rather than only a raw count.
    - The CLI can display a short dim `thinking` preview when streamed chunks include reasoning markers such as `<think>...</think>`, but this is provider-dependent; it is not universal live reasoning across every backend yet.
16. **CLI/runtime hardening validated from outside the repo root**:
    - `~/.local/bin/able chat --help` now succeeds from `/tmp`.
    - `printf '/q\n' | ~/.local/bin/able chat --control-port 0` exits cleanly with no leaked `aiohttp` session warnings.
    - `printf '/resources\n/q\n' | ~/.local/bin/able chat --control-port 0` now returns resource inventory outside the repo root.
    - `printf '/battle\n/q\n' | ~/.local/bin/able chat --control-port 0` now lists available battle domains outside the repo root.
    - `printf 'what is the square root of 69?\n/q\n' | ~/.local/bin/able chat --control-port 0 --no-stream` returned a clean T1 answer in ~5.2s during this pass.
17. **Observability split for local installs**:
    - `arize-phoenix` + `opentelemetry-*` moved to `.[observability]` extras in `pyproject.toml`.
    - Base local installs stay lighter; server deploys still install `.[observability]`.
18. **Operator report path + Strix sidecar visibility**:
    - Research scout now writes operator-facing copies to `~/.able/reports/research/latest.md` and `~/.able/reports/research/latest.json`, while still mirroring dated JSON into `data/research_reports/`.
    - The Telegram research summary now points at `~/.able/reports/research/latest.md` instead of a vague repo-relative directory.
    - Control plane now exposes real Strix status (`inactive` / `available` / `configured` / `misconfigured`) rather than a placeholder note.
    - Weekly self-pentest can optionally run a Strix sidecar scan when `ABLE_ENABLE_STRIX_PENTEST=1`, `STRIX_LLM`, `LLM_API_KEY`, and the `strix` CLI are present.
19. **Buddy wired into all system events**:
    - Evolution daemon: auto_improve eval passes feed `buddy.eval_passes` + hunger
    - Morning briefing (9am): LLM prompt includes buddy level, mood, needs, battle record
    - Evening check-in (9pm): LLM prompt includes buddy mood + action suggestions
    - Nightly research (1am): counts as research domain exploration XP
    - Weekly research (Sun 10am): counts as higher-complexity research interaction
    - Autopilot (5am): awards XP for objectives + distillation pairs
    - Nightly distillation (2am): awards 3 XP per new pair
    - Evolution daemon (3am): awards 30 XP + thirst on deploy
    - `buddy-walk` (every 2h): 5 passive XP + 8 energy + decay + evolution checks
20. **`datetime.utcnow()` eliminated**: all 14 occurrences across tenant modules replaced with `datetime.now(timezone.utc)` — 0 deprecation warnings in test suite.
21. **PhasedCoordinatorProtocol merged**: 4-phase agent execution for swarm (from `wu-g` branch).
22. **AuthManager singleton** — PBKDF2 was re-computed 14 times during gateway init (~880ms). Cached as singleton, cutting gateway init from ~1.6s to ~0.8s.
23. **BuddyNeedsCheck proactive bug fixed** — used nonexistent `NOTIFY` enum and `message=` field. Corrected to `ALERT` + `title`/`description`. Class is unused in production (buddy-walk cron covers the same role) but is now correct if ProactiveEngine is ever started.
24. **Gateway resilience hardening**:
    - **ProviderChain.stream() circuit breaker**: Stream path now checks `circuit_breaker.is_available()` before each provider, records success/failure — matches the complete() resilience model.
    - **Input length validation**: `process_message()` and `stream_message()` reject messages over 100K chars before any pipeline step runs.
    - **Per-client rate limiting**: `RateLimiter` (token bucket + sliding window) wired into both message paths. Default: 20/min, 200/hr. Configurable per client.
    - **Tool output wrapping**: Tool results fed back to LLM are wrapped with `[TOOL OUTPUT — name]...[END TOOL OUTPUT]` delimiters to prevent prompt injection via tool responses.
25. **Lazy imports for gateway.py**:
    - `aiohttp` (~203ms), `telegram` (~98ms), and provider SDK classes deferred to first use via `_ensure_aiohttp()` / `_ensure_telegram()` helpers.
    - CLI startup no longer pays the Telegram/aiohttp import tax. Gateway import dropped from ~600ms to ~300ms.
    - Provider class imports moved into legacy `_init_providers_legacy()` (primary path uses registry which does its own lazy imports).
    - `from __future__ import annotations` + `TYPE_CHECKING` block keeps type hints valid without eager imports.
26. **Multimodal support across channels**:
    - **CLI**: `/image <path>` sends images to vision chain with optional caption. `/audio <path>` transcribes audio files and optionally forwards to ABLE.
    - **Telegram**: Photos, videos (via thumbnail extraction), video notes, audio documents, and image documents all handled. Filter updated to accept `VIDEO | VIDEO_NOTE | Document.ALL`.
    - **Pluggable ASR**: `VoiceTranscriber` refactored with 3 backends: `ExternalASR` (HTTP endpoint for Voxtral/Qwen3/any frontier model), `OpenAIWhisper` (legacy), `LocalWhisper` (faster-whisper). Backend selected via `ABLE_ASR_PROVIDER` env var or auto-detected from `ABLE_ASR_ENDPOINT`.
    - Default ASR endpoint not yet configured — operator provides their preferred model endpoint via `ABLE_ASR_ENDPOINT` + `ABLE_ASR_API_KEY`.
27. **Distillation pipeline gap closed**:
    - CLI sessions (`able chat`) now write per-turn JSONL to `~/.able/sessions/{session_id}.jsonl` via `_SessionWriter` — feeds the nightly distillation harvest.
    - `CLISessionHarvester` registered in `harvest_runner.py` as priority 2 source alongside `able_interaction`.
    - **ExternalToolHarvester** added: reads `~/.able/external_sessions/*.jsonl` — any third-party AI tool (Cursor, Windsurf, Copilot, Grok, custom agents) can drop session files there and ABLE learns from them autonomously.
    - Optional `_source.txt` tag file and per-record `"source"` field let users attribute sessions to specific tools.
    - 15 new harvester tests: CLI session writing, end-to-end _SessionWriter → CLISessionHarvester pickup, ExternalToolHarvester with source tagging/override/domain detection/thinking extraction.
28. **Distillation scaffolding stripping** (commit `be96bbf`):
    - `BaseHarvester._strip_scaffolding()` removes 13+ XML tag types, base64 data URIs, and internal analytics names
    - `ClaudeCodeHarvester` filters 25+ metadata entry types and system subtypes
    - `scrub_corpus()` retroactively applies filters to all existing distillation pairs on each nightly run
    - 66 harvester tests
29. **Universal scaffolding + CommandGuard hardening** (commit `6336939`):
    - **All 8 harvesters** now call `_clean_messages()`: Claude Code, CLI, OpenCLI/Codex, external tool, inbox, antigravity, 0wav — every source gets scaffolding stripped
    - New patterns stripped: `<claude-code-hint>` (zero-token side channel), `<example>` tags, base64 data URIs → `[image]`, `tengu_*` analytics event names
    - 5 new Claude Code JSONL entry types filtered: bash-progress, code-indexing, plugin-hint, comment-label, claude-code-hint
    - **CommandGuard security hardening** ported from Claude Code BashTool (12K LOC analysis):
      - Binary hijack env var detection (`LD_`, `DYLD_`, `PATH=`)
      - Dangerous removal path checking (`rm -rf /`, `/usr`, `/etc`, etc.)
      - cd+git compound detection (bare repo fsmonitor RCE vector)
      - Safe env var stripping (`NODE_ENV`, `RUST_LOG` → skip to real command)
      - Subcommand cap (>50 → force approval, DoS protection)
      - Pipe to zsh blocked alongside sh/bash
    - SecureShell strips all `DYLD_*` and `LD_*` from execution environment
    - 702 tests passing (70 harvester, 11 security)
30. **Federated distillation network**:
    - **New package `able/core/federation/`**: 7 modules (identity, models, contributor, distributor, ingester, sync, __init__)
    - **Instance identity**: UUID4 generated on install or buddy creation, persisted in `~/.able/instance.yaml`
    - **Zero-config enrollment**: buddy creation in `able chat` auto-enrolls in federation network (non-fatal)
    - **Contributor**: exports high-quality pairs (≥0.85), strips PII (emails, phones, IPs, home paths, API keys, SSH keys), removes tenant_id/gold_model
    - **Ingester**: 4-layer defense (TrustGate 52+ injection patterns, scaffolding strip, quality re-validation, content hash dedup via SQLite unique index)
    - **Distributor**: pluggable `DistributionBackend` protocol (inspired by vLLM Ascend plugin pattern), `GitHubReleasesBackend` as first implementation, outbox/inbox queuing for offline resilience
    - **Sync orchestrator**: cron at 3:30am daily (after harvest 2am + evolution 3am), incremental via `last_sync_cursor`
    - **Store enhancement**: `DistillationStore.get_pairs()` now accepts `since: Optional[datetime]` for incremental export
    - **GitHub client**: 4 new release methods (create_release, upload_release_asset, list_releases, download_release_asset)
    - **Metrics**: `/metrics/federation` endpoint with instance_id, network_enabled, last_sync, domain distribution, network pair counts
    - **Domain snowball**: instances naturally specialize — more users in security = better security distillation for everyone
    - Network pairs stored with `tenant_id='network'`, flow through existing `CorpusBuilder.build_tenant_with_able_base()` path
    - `install.sh` seeds federation identity during workspace init
    - 34 federation tests (identity, models, PII scrubbing, ingestion validation, sync orchestrator, store since parameter)
31. **Unsloth training exporter** (`able/core/distillation/training/unsloth_exporter.py`):
    - `UnslothExporter.export_notebook()` generates Colab-ready .ipynb files with Unsloth 2x speed + 70% VRAM savings
    - `UnslothExporter.export_training_script()` generates standalone Python scripts for VS Code + Colab runtime
    - Both use model configs from `model_configs.py` (27B H100, 9B T4 Colab, 9B local)
    - Notebooks: install Unsloth → load model → format ChatML corpus → train with SFTTrainer → export GGUF (Dynamic 2.0 quants) → generate Ollama Modelfile
    - GGUF targets: Q4_K_M, IQ2_M, Q8_0 for 9B; Q4_K_M, Q5_K_M, Q8_0 for 27B
    - Designed to maximize free Colab T4 runtime (12-24 hours)
    - Training stats exported as JSON for federation metrics
    - 6 exporter tests covering notebook generation, script generation, ChatML format, GGUF export presence
    - 742 tests passing total (40 federation + 702 existing)

## Next-Run Objectives

### Priority 1: Studio dashboard buddy integration

The buddy system works across all channels (CLI, Telegram, API, cron) but the Studio web dashboard doesn't display buddy state yet. Wire buddy status, operator profile, roster/backpack progress, badges, and battle history into the Studio API.

### Priority 2: ASR backend configuration

The pluggable ASR interface is ready. Next step: configure the operator's preferred audio-native model (Voxtral or Qwen3) as the `ABLE_ASR_ENDPOINT`, test with real audio from Telegram and CLI, and verify transcription quality.

### Priority 3: Streaming for tool-dispatch iterations

Current streaming (`stream_message()`) handles text-only responses. For tool dispatch, `process_message()` still blocks. Investigate partial result streaming during tool iterations.

### Priority 4: Provider-level reasoning streaming

The CLI parser can already surface streamed reasoning markers, but most providers currently only stream visible answer text. Add provider-level support where available (Anthropic/OpenAI reasoning deltas) so live `thinking` previews are grounded in actual provider events.

### Priority 5: Federation live setup

Configure the `able-network-corpus` GitHub repo and `GITHUB_TOKEN` for live federation sync:
- Create the GitHub repo for network corpus distribution
- Test a full contribution → publish → fetch → ingest cycle end-to-end
- Verify PII scrubbing and TrustGate rejection on real data
- Confirm `tenant_id='network'` pairs flow through `CorpusBuilder` correctly

### Priority 6: First Colab training run

Run the first real Unsloth fine-tuning using the current corpus:
- Export a notebook: `UnslothExporter().export_notebook("9b", corpus_path, hf_repo="able-nano-9b")`
- Upload to Colab, connect free T4 runtime, execute all cells
- Validate GGUF output loads in Ollama
- Compare fine-tuned model vs base on reasoning + tools eval configs
- Document real training time and memory usage for the handoff

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
python3 -m pytest able/tests/test_buddy.py -q
python3 -m pytest able/tests/test_weekly_research.py -x
python3 -m pytest able/tests/test_control_plane.py able/tests/test_resource_tools.py able/tests/test_learning_loops.py able/tests/test_collect_results.py able/tests/test_evolution_cycle.py -x
python3 -m pytest able/tests/ -x --ignore=able/tests/test_routing.py --ignore=able/tests/test_gateway.py -q
bash -n deploy-to-server.sh
bash -n install.sh
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
