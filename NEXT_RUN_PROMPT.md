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
- Backpack/collection system is live: `/buddy bag`, `/buddy switch <name>`, badges, and full-dex progression
- **Needs/Tamagotchi layer**: hunger (evals), thirst (evolution), energy (domain exploration) — decay over time, restored by real actions
- Mood system with context-aware messages and cross-channel nudges
- Battles tied to real promptfoo evals, domain bonuses per species
- XP awards in the gateway (fires on Telegram, CLI, API — not channel-specific)
- **Autonomous buddy progression** via cron: buddy-walk every 2h (passive XP + decay), evolution daemon awards deploy XP, distillation harvest awards pair XP, research/autopilot count as domain exploration, morning report includes buddy status
- Proactive engine `BuddyNeedsCheck` runs every 2h for auto-nudges
- Telegram nudges on evolution, legendary unlock, level-up, or low mood during autonomous runs
- CLI renders rarity badges, streaks, legendary unlock state, and operator profile/backpack status correctly
- Starter selection locked to 5 species (Aether hidden); exit handling works at every setup prompt
- `_handle_slash` uses `SlashCtx` context object instead of 18 positional args
- 58 buddy tests covering model, persistence, onboarding/profile state, rendering, battles, rarity, XP, needs, mood, and autonomous tick

**Streaming + approval UX**:
- `stream_message()` async generator in the gateway — runs full pipeline then streams AI response
- CLI streams tokens as they arrive, `--no-stream` flag to disable
- Gateway fallback now only re-fetches a completion if the stream fails before the first chunk; partial stream output is preserved without duplication
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
- 62 focused CLI/buddy tests (9 CLI chat + 53 buddy).

**Test suite**:
- Full-suite pass: 602 tests, 0 deprecation warnings
- datetime.utcnow() replaced with datetime.now(timezone.utc) across all tenant modules

Plus: resource action tool, control-plane endpoint tests, legacy shim removal, CLI slash commands (/resources, /eval, /evolve, /buddy, /battle).

## Core Mission

Advance ABLE's scaffolding and operator usefulness. Prefer work that makes ABLE more self-owned, more testable, more deployable, and more capable of learning from its own behavior.

Good work usually looks like:
- grow the distillation corpus toward 100+ pairs for H100 fine-tuning
- cut live startup and first-response latency further
- harden runtime seams (deploy, gateway, approval, control plane)
- add Studio dashboard integration for buddy, roster, operator profile, and routing metrics
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
python3 -m pytest able/tests/test_weekly_research.py -x
```

Then run the full suite:
```bash
python3 -m pytest able/tests/ -x --ignore=able/tests/test_routing.py --ignore=able/tests/test_gateway.py -q
```

Or targeted runs:
```bash
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
