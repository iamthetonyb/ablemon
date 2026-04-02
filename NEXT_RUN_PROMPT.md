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
- Deterministic shiny starter hatch + earned legendary form tied to real runtime milestones
- **Needs/Tamagotchi layer**: hunger (evals), thirst (evolution), energy (domain exploration) — decay over time, restored by real actions
- Mood system with context-aware messages and cross-channel nudges
- Battles tied to real promptfoo evals, domain bonuses per species
- XP awards in the gateway (fires on Telegram, CLI, API — not channel-specific)
- Proactive engine `BuddyNeedsCheck` runs every 2h for auto-nudges
- Telegram nudges appended to responses when needs are low
- CLI renders rarity badges, streaks, and legendary unlock state correctly
- 45 tests covering model, persistence, rendering, battles, rarity, XP, needs, and mood

Plus: resource action tool, control-plane endpoint tests, legacy shim removal, CLI slash commands (/resources, /eval, /evolve, /buddy, /battle).

## Core Mission

Advance ABLE's scaffolding and operator usefulness. Prefer work that makes ABLE more self-owned, more testable, more deployable, and more capable of learning from its own behavior.

Good work usually looks like:
- increase distillation data quality and throughput
- improve the live operator experience (streaming, richer approval, better CLI)
- harden runtime seams (deploy, gateway, approval, control plane)
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
```

Then run the most relevant focused tests for the surfaces you touched:
```bash
python3 -m pytest able/tests/test_buddy.py -x
python3 -m pytest able/tests/test_control_plane.py able/tests/test_resource_tools.py able/tests/test_learning_loops.py able/tests/test_collect_results.py able/tests/test_evolution_cycle.py -x
python3 -m pytest able/tests/test_training_pipeline.py -x
python3 -m pytest able/tests/test_distillation_store.py -x
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
