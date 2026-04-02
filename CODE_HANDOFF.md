# ABLE — Code Handoff

Date: 2026-04-02
Branch: `main` is the current production baseline. Active improvement branch: `codex/cli-speed-and-ux-hardening`.
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
   - Buddy suite: 45 tests passing
   - Focused new-surface suite: 23 tests across control plane, resource tools, learning loops, collect_results, and evolution cycle passing
6. **Legacy cleanup**: all 87 bare imports migrated, 5 root-level shims removed, pyproject.toml simplified.
7. **Buddy system (system-wide)**: Pokemon + Tamagotchi gamified agent companion in `able/core/buddy/`:
   - 5 starter species (Blaze/Wave/Root/Spark/Phantom) with domain bonuses
   - 3 evolution stages tied to real milestones (interactions, eval passes, distillation pairs, evolution deploys)
   - XP from real interactions (complexity-weighted), tool use, approvals, battles
   - **Rarity layer**:
     - Shiny starter hatch is a deterministic rare cosmetic variant at creation time
     - Legendary form is earned only from real runtime milestones: stage 3, level 40, eval passes, battle wins/streak, distillation pairs, and evolution deploys
   - **XP awards in the gateway** — fires on ALL channels (Telegram, CLI, API), not just CLI
   - Battle system runs real promptfoo evals — wins feed distillation, losses identify skill gaps
   - Starter selection on first `able chat` run, `/buddy` and `/battle` slash commands
   - CLI renderer now shows shiny/legendary badges, streak progress, and legendary unlock state
   - Evolution announcement bug fixed: old/new stage names now render correctly
   - **Needs/Tamagotchi layer**: hunger/thirst/energy decay over time, restored by real actions:
     - Hunger → feed by running `/battle` (evals = food)
     - Thirst → water by running `/evolve` or chatting (interactions = sips)
     - Energy → walk by exploring new domains and using varied tools
   - Mood system (thriving/content/hungry/neglected) with context-aware messages
   - **Telegram nudges**: buddy status appended to responses when needs are low
   - **Proactive engine**: `BuddyNeedsCheck` runs every 2h, dispatches nudge notifications
   - Nudge module (`able/core/buddy/nudge.py`) for cross-channel care reminders
   - 45 tests covering model, persistence, rendering, battles, rarity, XP engine, needs, and mood
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
    - Total: 583 tests passing across the full test suite
12. **Deploy hardening**:
    - Git operations on the DigitalOcean host now run as the `able` user in both `.github/workflows/deploy.yml` and `deploy-to-server.sh`
    - Existing `/opt/able/ABLE` working trees are re-owned by `able` before clone/fetch/checkout
    - This fixes Git's `detected dubious ownership in repository at '/opt/able/ABLE'` failure without weakening `safe.directory`
13. **Clean terminal experience for `able chat`**:
    - **Log suppression**: all logging set to ERROR by default on startup — eliminates ~40 lines of provider/ollama/enricher noise. `--verbose` flag restores full logging.
    - **Claude Code-style header**: `render_header()` places buddy ASCII art mascot on the left with name, level, XP bar, stage, mood, needs, and battle record on the right — mirrors Claude Code's robot mascot layout.
    - **Optional buddy**: first-run buddy selection is skippable, and non-interactive CLI sessions now skip onboarding automatically so scripted smokes do not block before chat starts.
    - **Graceful no-buddy**: all buddy code paths handle `buddy is None` without blocking or crashing.
    - 55 focused CLI/buddy tests passing (8 CLI chat + 47 buddy).
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
    - **ANSI color**: green `>` prompt, cyan `able` prefix on responses, dim timestamps and help text. Respects `NO_COLOR` env var.
    - **Response timing**: `[1.2s]` shown after each response.
    - **Cleaner prompt**: `> ` instead of `you> `, slash commands get formatted help table.
    - **Slash command shortcuts**: `/q` for quit, `/h` for help, `/?` for help.
    - **Quieter local default**: `able chat` now defaults to `--control-port 0`, so transient local chat sessions do not boot or print the health/control server unless explicitly requested.
    - **Clean exit**: `ABLEGateway.aclose()` now closes provider sessions, web-search sessions, the shared Studio `aiohttp` session, and the health server runner; `/exit` no longer leaks unclosed client sessions on shutdown.
    - **Starter selection revamp**: first-run buddy setup now explains each starter in plain language (`element`, `role`, `best for`, `abilities`) and explicitly states that the choice affects buddy flavor/bonus XP, not routing.
16. **CLI/runtime hardening validated from outside the repo root**:
    - `~/.local/bin/able chat --help` now succeeds from `/tmp`.
    - `printf '/q\n' | ~/.local/bin/able chat --control-port 0` exits cleanly with no leaked `aiohttp` session warnings.
    - `printf 'what is the square root of 69?\n/q\n' | ~/.local/bin/able chat --control-port 0 --no-stream` returned a clean T1 answer in ~5.2s during this pass.
17. **Observability split for local installs**:
    - `arize-phoenix` + `opentelemetry-*` moved to `.[observability]` extras in `pyproject.toml`.
    - Base local installs stay lighter; server deploys still install `.[observability]`.

## Next-Run Objectives

### Priority 1: Cut live startup + first-response latency further

The local CLI path is now clean and operator-usable, but latency is still dominated by gateway initialization and provider round-trips. The next best pass is:
- Profile `ABLEGateway.__init__()` again after the wrapper/import cleanup
- Lazy-load any remaining heavy gateway imports that are not needed before the first prompt
- Add provider/tier latency breakdown in CLI verbose mode so local overhead and upstream model latency are separated

### Priority 2: Studio dashboard buddy integration

The buddy system works across all channels (CLI, Telegram, API) but the Studio web dashboard doesn't display buddy state yet. Wire buddy status, needs, and battle history into the Studio API so operators can see their buddy's progress from the web.

### Priority 3: Streaming for tool-dispatch iterations

Current streaming (`stream_message()`) handles the common case (text-only responses). For messages that trigger tool dispatch, the full `process_message()` still blocks. Investigate whether tool iterations can stream partial results and tool execution notifications.

### Priority 4: Distillation corpus growth

The harvester and prompt bank are improved but the corpus still needs more pairs. Focus on:
- Running the new eval configs (reasoning, tools) to generate T4 gold outputs
- Monitoring the corpus pair count (`/eval` in the CLI) and pushing toward 100+
- Verifying the interaction logger correctly marks corpus-eligible interactions

### Priority 5: Keep docs and runtime in lockstep

- Refresh `README.md` only when code changes make its current commands stale.
- Keep `CODE_HANDOFF.md` and `NEXT_RUN_PROMPT.md` updated at the end of each pass.

## Validation Commands

```bash
able --help                                     # Verify global command works
able chat --help                                # Verify chat subcommand
cd /tmp && ~/.local/bin/able chat --help        # Verify wrapper works outside repo root
cd /tmp && printf '/q\n' | ~/.local/bin/able chat --control-port 0
python3 -m pytest able/tests/test_cli_chat.py -x
python3 -m pytest able/tests/test_buddy.py -q
python3 -m pytest able/tests/test_control_plane.py able/tests/test_resource_tools.py able/tests/test_learning_loops.py able/tests/test_collect_results.py able/tests/test_evolution_cycle.py -x
python3 -m pytest able/tests/ -x --ignore=able/tests/test_routing.py --ignore=able/tests/test_gateway.py -q
bash -n deploy-to-server.sh
bash -n install.sh
```

For targeted runs:
```bash
python3 -m pytest able/tests/test_buddy.py -x                # Buddy + needs + rarity
python3 -m pytest able/tests/test_cli_chat.py -x              # CLI + streaming
python3 -m pytest able/tests/test_harvesters.py -x            # Distillation
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
