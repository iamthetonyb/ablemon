# ABLE — Code Handoff

Date: 2026-04-01
Canonical branch: `codex/able-rewrite-integration`
Baseline head before this handoff refresh: `cd4a049`
Canonical integration PR: `#49`

## Source Of Truth

Use this file as the canonical cross-agent handoff.

If any of these disagree, trust them in this order:

1. This file
2. Current branch state on `codex/able-rewrite-integration`
3. Current code in the repo
4. `README.md`
5. GitHub PR text/comments

Important: `origin/main` does not currently contain a tracked `CODE_HANDOFF.md`. This branch introduces the canonical handoff doc. Do not assume GitHub PR body text is current.

## Merge Policy

- Merge PR `#49` only.
- Do not merge atlas-era PRs `#37` through `#48` individually.
- Treat those PRs as salvage/reference inputs only.

## North Star

The goal is to turn ABLE into a self-owned operator runtime with robust scaffolding so core workflows no longer depend on external chat products as the primary interface.

That means:

- `able chat` is the simple local operator entrypoint
- `able-studio` is the control center for resources, tools, approvals, setup, and operator visibility
- deploys are consistent from local -> GitHub -> server
- local/offline model lanes are first-class, not afterthoughts
- distillation and evaluation move the system toward a stronger self-hosted stack over time

## Current State

The integration branch already lands these major changes:

- Packaged `able` runtime entrypoint
- Local operator CLI:
  - `able chat`
  - `able serve`
  - `able-chat`
- Registry-backed gateway tool system shared with studio
- Nomad-style resource plane and control-plane endpoints
- T4-first 9B distillation lane and quant-pinned model roster
- Deploy path aligned to the packaged service
- Server deploy bootstrap for the `able` system user/group

## What Was Recently Added

- Local chat CLI in `able/cli/chat.py`
- `able/__main__.py` subcommands for `serve` and `chat`
- CLI-aware channel tagging in `able/core/gateway/gateway.py`
- `scripts/able-auth.py`
- `scripts/able-setup.sh`
- Deploy bootstrap fix for missing `able` user/group in:
  - `.github/workflows/deploy.yml`
  - `deploy-to-server.sh`

## Current Objectives

These are the highest-value next steps for Claude or any follow-up agent:

1. Verify correctness and remove drift
- audit import paths
- confirm entry points
- confirm routing tier table vs docs
- confirm quant roster vs config

2. Close test gaps
- add dedicated tests for `/health`
- add dedicated tests for control-plane endpoints
- add tests for resource tools
- expand distillation profile tests for T4 vs H100 behavior

3. Finish the operator/runtime path
- verify `able chat` is strong enough for immediate demos
- identify the smallest justified improvements toward a stronger OpenCode-class local interface
- do not overbuild a TUI without evidence

4. Tighten deploy parity
- verify branch deploy via `workflow_dispatch ref=...`
- verify the server bootstraps cleanly from an older VPS state
- verify service health after restart

5. Reduce legacy scaffolding debt
- verify whether root-level shim packages are still required
- remove them only if imports are actually migrated

## Quant-Pinned Model Roster

These sizes are pinned. Do not change them without re-measuring and updating config/docs together.

- `able-student-27b`
  - `UD-Q4_K_XL` = `17.6 GB`
  - `Q5_K_M` = `19.6 GB`
  - `Q8_0` = `28.6 GB`
- `able-nano-9b`
  - `UD-IQ2_M` = `3.65 GB`
  - `UD-Q4_K_XL` = `5.97 GB`
  - `Q5_K_M` = `6.58 GB`

Source of truth:

- `config/distillation/able_student_27b.yaml`
- `config/distillation/able_nano_9b.yaml`
- `config/ollama/Modelfile.27b`
- `config/ollama/Modelfile.9b-edge`
- `config/ollama/Modelfile.9b-balanced`
- `able/core/distillation/training/quantizer.py`

## Validated Commands

These were already verified on this branch:

```bash
python3 -m able --help
python3 -m able chat --help
printf '/exit\n' | python3 -m able chat --control-port 0
python3 -m pytest able/tests/test_cli_chat.py -x
python3 -m pytest able/tests/test_cli_chat.py able/tests/test_training_pipeline.py able/tests/test_distillation_store.py
bash -n deploy-to-server.sh
python3 -m py_compile scripts/able-auth.py
```

## Deploy Notes

The recent server failure was:

```text
chown: invalid user: 'able:able'
```

That is now addressed by explicitly bootstrapping the `able` system user/group before chowning runtime paths.

Files:

- `.github/workflows/deploy.yml`
- `deploy-to-server.sh`
- `able/able.service`

## Known Gaps

- `able chat` is functional but still a text REPL, not a richer TUI
- resource lifecycle actions are not yet fully approval-workflow-native across every surface
- control-plane endpoints still need dedicated test coverage
- import cleanup and shim removal need a real audit, not assumption-based deletion
- PR `#49` body is likely stale relative to current branch state

## Claude Tasking Guidance

When Claude audits this repo, optimize for:

- correctness
- operator usefulness
- deploy realism
- test coverage on the actual new surfaces
- reduction of legacy drift

Do not optimize for:

- marketing language
- speculative AGI claims
- broad refactors without a concrete payoff
- merging the whole old PR stack

## Expected Output From Claude

Claude should return:

- findings first, ordered by severity
- exact file references
- exact validation commands
- clear merge recommendation:
  - `merge now`
  - `merge after X`
  - `do not merge`
- a short post-merge improvement list

## Compatibility

`CLAUDE_HANDOFF.md` now exists as a compatibility pointer. Keep this file canonical going forward so the prompt surface stays stable.
