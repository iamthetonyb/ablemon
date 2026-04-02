# Codex Prompt — ABLE Reusable Next Run

> Copy everything below the line and use it as the next-run prompt for any coding agent.
> Always attach or reference `CODE_HANDOFF.md` with it.

---

You are continuing work on the ABLE repo — a self-hosted AGI runtime with 5-tier routing, approval-gated tools, a local CLI (`able chat`), a web control plane, a quant-pinned distillation pipeline, and a background evolution daemon.

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
3. If `CODE_HANDOFF.md`, the code, and PR text disagree, trust order is:
   1. `CODE_HANDOFF.md`
   2. current branch state
   3. current code
   4. `README.md`
   5. GitHub PR text/comments

## Core Mission

Advance ABLE's own scaffolding and operator usefulness. Prefer work that makes ABLE more self-owned, more testable, more deployable, and more capable of learning from its own behavior.

Good work usually looks like one of these:
- close a feedback loop between interaction logging, evals, skills, memory, routing, and distillation
- harden a live runtime seam (CLI, gateway, control plane, approval path, deploy path)
- improve corpus quality or training readiness without changing pinned quant targets
- add missing tests around new or risky surfaces
- fix doc/runtime drift so operators and follow-on agents can trust the repo

## How To Choose Work

Use this order unless the user gives a more specific task:

1. Take the highest-leverage open item from `CODE_HANDOFF.md`.
2. Prefer correctness and loop-closure over broad new feature work.
3. Prefer real operator/runtime value over speculative architecture.
4. If you add or change behavior, add tests for that seam.
5. Keep docs factual. No roadmap hype, no marketing copy.

## Working Rules

- All imports must use `from able.X.Y import Z`.
- Do not recreate root-level shims or legacy `from core.X` style imports.
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

Then run the most relevant focused tests for the surfaces you touched. If you change learning/control-plane/distillation code, prefer these:
```bash
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
   - refresh “What Was Just Completed”
   - refresh “Next-Run Objectives”
   - refresh validation commands if needed
2. Update this `CODEX_PROMPT.md` if the best next-run prompt has changed.
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
