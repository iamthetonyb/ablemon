# ABLE — Reusable Next-Run Prompt

> Copy everything below the line and use it as the next-run prompt for any coding agent.
> Always attach or reference `CODE_HANDOFF.md` with it.

---

You are continuing work on the ABLE repo — a self-hosted AGI runtime with 5-tier routing, approval-gated tools, a local CLI (`able chat`), a web control plane, a quant-pinned distillation pipeline, and a background evolution daemon that self-tunes routing weights using interaction data, eval results, proactive insights, and durable memory.

## Orientation

Before touching code:

1. Read `CODE_HANDOFF.md` fully. It is the canonical cross-agent handoff.
2. Verify the runtime and branch state:
   ```bash
   cd /Users/abenton333/Desktop/ABLE
   python3 -m able chat --help
   python3 -m pytest able/tests/test_cli_chat.py -x
   git log --oneline -10
   git status --short
   ```
3. Run the full new-surface test suite:
   ```bash
   python3 -m pytest able/tests/test_control_plane.py able/tests/test_resource_tools.py able/tests/test_learning_loops.py able/tests/test_collect_results.py able/tests/test_evolution_cycle.py -x
   ```
4. If `CODE_HANDOFF.md`, the code, and PR text disagree, trust order is:
   1. `CODE_HANDOFF.md`
   2. current branch state
   3. current code
   4. `README.md`
   5. GitHub PR text/comments

## What's already done

All four learning feedback loops are closed and tested:
- **Eval → Self-Improvement**: eval failures auto-classify and patch SKILL.md via approval workflow
- **Proactive → Evolution**: LearningInsights submit to evolution collector
- **Memory → Evolution**: collector queries HybridMemory for durable learnings before each cycle
- **Interaction → Distillation**: gold T4 outputs harvested, CORPUS READY threshold at 100 pairs

**Buddy gamification system** (system-wide, all channels):
- 5 starter species, 3 evolution stages, XP from real interactions
- A hidden late-game unlock exists outside the normal starter pool; do not spoil it in user-facing docs or pre-unlock UI
- Deterministic shiny starter hatch + earned legendary form tied to real runtime milestones
- Interactive first-run setup now requires a real starter pick plus onboarding for `focus`, `work_style`, and `distillation_track`
- `work_style` includes `all-terrain` for operators who move across solo build, delivery, ops, and collaboration
- Backpack/collection system is live: `/buddy bag`, `/buddy switch <name>`, badges, and full-dex progression
- **Needs/Tamagotchi layer**: hunger (evals), thirst (evolution), energy (domain exploration) — decay over time, restored by real actions
- Mood system with context-aware messages and cross-channel nudges
- Battles tied to real promptfoo evals, domain bonuses per species
- XP awards in the gateway (fires on Telegram, CLI, API — not channel-specific)
- Aether stays outside the starter pool and is now orchestration-first internally: Psychic type, swarm planning, buddy routing, signal fusion
- **Autonomous buddy progression** via cron: buddy-walk every 2h (passive XP + decay), evolution daemon awards deploy XP, distillation harvest awards pair XP, research/autopilot count as domain exploration, morning report includes buddy status
- Proactive engine `BuddyNeedsCheck` runs every 2h for auto-nudges
- Telegram nudges on evolution, legendary unlock, level-up, or low mood during autonomous runs
- CLI renders rarity badges, streaks, legendary unlock state, and operator profile/backpack status correctly
- Starter selection locked to 5 species (Aether hidden); exit handling works at every setup prompt
- `_handle_slash` uses `SlashCtx` context object instead of 18 positional args
- 60 buddy tests covering model, persistence, onboarding/profile state, rendering, battles, rarity, XP, needs, mood, autonomous tick, orchestration bonuses, and repo-root-independent battle discovery

**Streaming + approval UX**:
- `stream_message()` async generator in the gateway — runs full pipeline then streams AI response
- CLI streams tokens as they arrive, `--no-stream` flag to disable
- Gateway fallback now only re-fetches a completion if the stream fails before the first chunk; partial stream output is preserved without duplication
- CLI prompts now use local line editing/history, so arrow keys and cursor movement work during chat and onboarding
- `/clear` redraws the terminal with scrollback preserved; `/compact` redraws and prints a compact session recap without losing transcript or distillation value
- `/resources` now uses the real resource-plane inventory method, and `/battle` resolves eval configs relative to the repo root so both work from `~/.local/bin/able` outside the repo
- The CLI can show a short dim `thinking` preview when streamed chunks include reasoning markers such as `<think>...</think>`, but this is provider-dependent rather than universal across all backends
- Rich approval rendering: risk icons, visual bars, affected resource extraction

**Distillation quality**:
- Harvester: prefers corpus_eligible rows, uses raw_input over preview
- Prompt bank: domain aliases, dedup on load/add
- New eval configs: reasoning (7 tests), tools (7 tests), wired into `/battle`

**Deploy hardening**:
- Git operations on the DigitalOcean host now run as the `able` user
- Existing `/opt/able/ABLE` working trees are re-owned by `able` before fetch/checkout
- This fixes Git's `dubious ownership` failure during deploy without weakening `safe.directory`

**Reports + Security Sidecars**:
- Research scout writes readable operator-facing output to `~/.able/reports/research/latest.md` plus `latest.json`
- Dated JSON still mirrors to `data/research_reports/`
- Strix is now represented as a real optional sidecar in the control plane and weekly pentest path, not just a placeholder note

**Terminal UX overhaul**:
- One-command install: `bash install.sh` — handles Python, venv, deps, PATH, workspace init. `able` and `able-chat` work from any terminal.
- Bare `able` defaults to `chat` in interactive terminals, `serve` in non-interactive.
- `able chat` defaults to `--control-port 0` so transient local chats do not boot the control server unless requested.
- Phoenix/OTel skipped entirely in CLI mode (`skip_phoenix=True`) — eliminates all startup print spam.
- Warnings + stderr redirected during gateway init to catch SAWarning, DeprecationWarning residue.
- Local installs now skip Phoenix/OTel entirely unless `.[observability]` is installed; server deploys still install the observability extra.
- ANSI color: green prompt, cyan agent prefix, dim metadata. Respects `NO_COLOR`.
- Thinking spinner (braille animation) while waiting for first token.
- Response timing `[1.2s]` after each response.
- Slash command shortcuts: `/q`, `/h`, `/?`. Formatted help table.
- Claude Code-style header with buddy ASCII art mascot and stats.
- Buddy setup is required in interactive first-run sessions, `/buddy setup` can refresh onboarding later, and non-interactive sessions auto-skip the first-run prompt.
- Clean gateway teardown closes provider/web/studio sessions so `/exit` no longer leaks unclosed `aiohttp` sessions.
- Header labels now spell out `Wins`, `Draws`, `Losses`, and `/status` shows the active provider roster instead of only a count.
- `/image <path>` sends images to vision chain; `/audio <path>` transcribes audio files and optionally forwards to ABLE.
- 77 focused CLI/buddy tests (17 CLI chat + 60 buddy).

**Gateway resilience**:
- `ProviderChain.stream()` now checks circuit breaker before each provider and records success/failure — matches `complete()` resilience model.
- Input length validation: messages over 100K chars rejected before pipeline runs.
- Per-client rate limiting (token bucket + sliding window) wired into both `process_message()` and `stream_message()`. Default: 20/min, 200/hr.
- Tool outputs wrapped with `[TOOL OUTPUT — name]...[END TOOL OUTPUT]` delimiters to prevent prompt injection via tool responses.

**Lazy imports**:
- `aiohttp` (~203ms), `telegram` (~98ms) deferred to first use. CLI startup no longer pays the Telegram/aiohttp import tax.
- `from __future__ import annotations` + `TYPE_CHECKING` block keeps type hints valid without eager imports.
- Gateway import dropped from ~600ms to ~300ms.

**Multimodal**:
- Telegram: photos, videos (thumbnail extraction), video notes, audio docs, image docs all handled.
- CLI: `/image <path>` and `/audio <path>` commands.
- Pluggable ASR: `VoiceTranscriber` supports ExternalASR (HTTP endpoint for Voxtral/Qwen3), OpenAI Whisper (legacy), LocalWhisper. Selected via `ABLE_ASR_PROVIDER` / `ABLE_ASR_ENDPOINT`.
- Default ASR endpoint not yet configured — operator provides their preferred model endpoint.

**Distillation pipeline fully closed + hardened**:
- CLI sessions now write per-turn JSONL to `~/.able/sessions/` — CLISessionHarvester picks them up during nightly harvest
- ExternalToolHarvester reads `~/.able/external_sessions/*.jsonl` for third-party AI tool learning (Cursor, Windsurf, Copilot, etc.)
- All learning loops are autonomous: interaction → evolution (3am), all sources → distillation (2am), buddy XP awarded on harvest/deploy
- **Universal scaffolding stripping**: all 8 harvesters (Claude Code, CLI, OpenCLI/Codex, external tool, inbox, antigravity, 0wav) now call `_clean_messages()` — strips 13+ XML tag types, base64 data URIs, analytics names, and `<claude-code-hint>` zero-token side channel
- `scrub_corpus()` retroactively applies the same stripping to all existing pairs on every nightly run — old data gets cleaned too
- 25+ metadata entry types filtered from Claude Code JSONL (file-history-snapshot, queue-operation, marble-origami-*, stream_event, bash-progress, etc.)

**CommandGuard security hardening** (ported from Claude Code BashTool 12K LOC):
- Binary hijack env var detection (LD_, DYLD_, PATH=)
- Dangerous removal path checking (rm -rf /, /usr, /etc, etc.)
- cd+git compound detection (bare repo fsmonitor RCE vector)
- Safe env var stripping (NODE_ENV, RUST_LOG → skip to real command for matching)
- Subcommand cap (>50 → force approval, DoS protection)
- Pipe to zsh blocked alongside sh/bash
- SecureShell strips all DYLD_*/LD_* from execution environment

**Federated distillation network** (`able/core/federation/`):
- Zero-config: buddy creation auto-enrolls instance in network via `~/.able/instance.yaml` (UUID4)
- Contributor exports high-quality pairs (≥0.85), strips PII (emails, phones, IPs, paths, API keys), removes tenant/model metadata
- Ingester validates incoming pairs: TrustGate (52+ injection patterns), scaffolding strip, quality re-validation, content hash dedup
- Distributor uses pluggable `DistributionBackend` protocol — `GitHubReleasesBackend` first, with offline outbox/inbox queuing
- Sync cron at 3:30am daily (after harvest + evolution), incremental via `last_sync_cursor`
- `DistillationStore.get_pairs()` accepts `since` param for incremental export
- GitHub client: 4 new release methods for corpus distribution
- `/metrics/federation` endpoint for network health monitoring
- Domain snowball: more users in a domain = better distillation for everyone in that domain
- Network pairs stored as `tenant_id='network'`, flow through existing corpus builder
- `install.sh` seeds federation identity during workspace init
- Research integration: llm-d prefix-cache routing → domain affinity, vLLM Ascend → pluggable backend pattern, Ollama 0.19 MLX → 2x T5 decode speed validates distillation flywheel

**Unsloth training pipeline** (`able/core/distillation/training/`):
- `UnslothExporter` generates Colab-ready notebooks and VS Code training scripts
- Federation corpus → Unsloth fine-tuning (2x speed, 70% less VRAM) → GGUF export (Dynamic 2.0) → Ollama T5
- 9B: free T4 Colab daily (12-24h/day available), L4/A100/H100 also supported
- 27B: H100 preferred → A100 (40GB+) → L4 (24GB, seq=2048, batch=1). Does NOT fit T4 (16GB)
- GPU fallback chain: `GPU_FALLBACK_CHAINS` in model_configs — auto-resolves next GPU when budget exhausted
- CPU-first: all harvesting, scrubbing, federation sync, corpus build run on CPU. GPU only for training step
- MLX local training: `export_mlx_training_script()` generates shell scripts for Apple Silicon LoRA fine-tuning (mlx_lm.lora → fuse → llama.cpp GGUF → Ollama). 9B fits 32GB+ Macs, 27B needs 64GB+.
- Notebooks auto-install Unsloth, load ChatML corpus, train with SFTTrainer, export GGUF, generate Modelfile
- Training stats JSON for federation metrics tracking

**Test suite**:
- Full-suite pass: 742 tests, 0 deprecation warnings
- 40 federation tests (identity, models, PII scrubbing, ingestion, sync, store since, Unsloth exporter)
- 70 harvester tests (scaffolding, entry types, harvesters, session writers, corpus scrubber)
- 11 security tests (injection, command guard, binary hijack, cd+git, dangerous paths, subcommand cap)
- datetime.utcnow() replaced with datetime.now(timezone.utc) across all tenant modules

Plus: resource action tool, control-plane endpoint tests, legacy shim removal, CLI slash commands (/resources, /eval, /evolve, /buddy, /battle).

## Research References (Cross-Verify)

These external resources informed the federation and distillation architecture decisions. Use them to cross-verify design choices and identify improvements:

- **llm-d architecture** — https://llm-d.ai/docs/architecture — Kubernetes-native distributed inference with prefix-cache-aware routing. ABLE adopted the domain-affinity routing pattern for federation ingestion prioritization.
- **vLLM Ascend** — https://github.com/vllm-project/vllm-ascend — Hardware-pluggable interface pattern. ABLE adopted the `DistributionBackend` protocol for pluggable federation backends (GitHub Releases first, future: HTTP, IPFS, S3).
- **Ollama 0.19 MLX** — https://www.macrumors.com/2026/03/31/ollama-now-runs-faster-apple-silicon-macs/ — 1.6x prefill, ~2x decode on Apple Silicon via MLX backend. Validates ABLE's Qwen 3.5 quant choices (27B at 17.6GB, 9B at 3.65GB both fit 32GB unified memory). Faster T5 inference accelerates the distillation flywheel.
- **Unsloth** — https://github.com/unslothai/unsloth — 2x faster training + 70% less VRAM via custom Triton kernels. Dynamic 2.0 GGUFs with layer-selective quantization. Used for `UnslothExporter` notebook/script generation.

## Core Mission

Advance ABLE's scaffolding and operator usefulness. Prefer work that makes ABLE more self-owned, more testable, more deployable, and more capable of learning from its own behavior.

Good work usually looks like:
- grow the distillation corpus toward 100+ pairs for H100 fine-tuning
- cut live startup and first-response latency further
- harden runtime seams (deploy, gateway, approval, control plane)
- add Studio dashboard integration for buddy, roster, operator profile, and routing metrics
- add provider-level reasoning streaming where backends support it
- add missing tests around new or risky surfaces
- fix doc/runtime drift

## How To Choose Work

Use this order unless the user gives a more specific task:

1. Take the highest-leverage open item from `CODE_HANDOFF.md` "Next-Run Objectives".
2. Prefer correctness and data throughput over broad new features.
3. Prefer real operator/runtime value over speculative architecture.
4. If you add or change behavior, add tests for that seam.
5. Keep docs factual. No roadmap hype, no marketing copy.

## Working Rules

- All imports must use `from able.X.Y import Z`. Root-level shims are gone — bare imports will crash.
- Quant sizes are pinned. Do not change them without re-measuring and updating the handoff.
- Keep production assumptions realistic: GitHub remote, server deploy path, CLI/runtime behavior, and offline workflow all need to stay aligned.
- Prefer small, defensible improvements over aesthetic refactors.
- If you find stale docs or prompts, update them before you stop.

## Validation Expectations

At minimum, rerun the CLI smoke test:
```bash
python3 -m able chat --help
python3 -m pytest able/tests/test_cli_chat.py -x
cd /tmp && ~/.local/bin/able chat --help
cd /tmp && printf '/q\n' | ~/.local/bin/able chat --control-port 0
cd /tmp && printf '/resources\n/q\n' | ~/.local/bin/able chat --control-port 0
cd /tmp && printf '/battle\n/q\n' | ~/.local/bin/able chat --control-port 0
cd /tmp && printf '/compact\n/q\n' | ~/.local/bin/able chat --control-port 0
python3 -m pytest able/tests/test_weekly_research.py -x
```

Then run the full suite:
```bash
python3 -m pytest able/tests/ -x --ignore=able/tests/test_routing.py --ignore=able/tests/test_gateway.py -q
```

Or targeted runs:
```bash
python3 -m pytest able/tests/test_federation.py -x -v
python3 -m pytest able/tests/test_buddy.py -q
python3 -m pytest able/tests/test_cli_chat.py -x
python3 -m pytest able/tests/test_control_plane.py able/tests/test_resource_tools.py able/tests/test_learning_loops.py able/tests/test_collect_results.py able/tests/test_evolution_cycle.py -x
python3 -m pytest able/tests/test_weekly_research.py -x
python3 -m pytest able/tests/test_harvesters.py -x
python3 -m pytest able/tests/test_evolution_split_tests.py -x
```

If you change deploy/runtime wiring:
```bash
bash -n deploy-to-server.sh
```

## Before You Finish

1. Update `CODE_HANDOFF.md`:
   - refresh "What Was Just Completed"
   - refresh "Next-Run Objectives"
   - refresh validation commands if needed
2. Update this `NEXT_RUN_PROMPT.md` if the best next-run prompt has changed.
3. Report:
   - branch name
   - HEAD commit
   - what changed
   - what did not get finished
   - exact validation commands run
   - files modified but not tested

## Output Style

- Be direct.
- Be operator-facing.
- No fluff.
- Leave room for stronger follow-on work by clearly identifying the next highest-value step.
