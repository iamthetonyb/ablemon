# Codex Prompt — ABLE Next Run

> Copy everything below the line and paste as the Codex task prompt.
> Attach or reference `CODE_HANDOFF.md` alongside this prompt.

---

You are working on the ABLE repo — a self-hosted AGI runtime that routes through a 5-tier model stack, logs every interaction to a structured database, and continuously self-tunes its routing and prompt enrichment through an evolution daemon. The system learns from its own outputs.

## Orientation

Read `CODE_HANDOFF.md` fully before touching anything. It has the repo structure, architecture, learning pipeline, model roster, and collaboration protocol.

Then verify the runtime:
```bash
python3 -m able chat --help
python3 -m pytest able/tests/test_cli_chat.py -x
git log --oneline -10
```

## Context from last session

We just completed a major cleanup pass:
- Migrated all 87 bare imports to fully-qualified `from able.*` paths
- Removed the 5 root-level shim packages (core/, tools/, memory/, scheduler/, clients/)
- Simplified pyproject.toml to `include = ["able*"]`
- Fixed deploy.yml pip install flag, datetime deprecation, budget cap doc drift
- Merged main into `codex/able-rewrite-integration`, all tests passing

The codebase is clean. Import migration is done. Shims are gone. What remains is closing the self-learning loops and adding test coverage.

## What to do — ordered by impact

### 1. Close the evolution → self-improvement loop (highest impact)

The evolution daemon (`able/core/evolution/`) runs 6-hour cycles analyzing interaction data and tuning routing weights. The auto-improver (`able/core/evolution/auto_improve.py`) classifies eval failures into 7 categories (thinking_bleed, skill_gap, format_violation, under_routing, content_quality, over_routing, model_regression).

**Problem**: Auto-improve classifies failures but doesn't trigger `able/core/agi/self_improvement.py` to actually update anything. The self-improvement engine can patch documents (APPEND/REPLACE/PATCH modes) with approval gating, but nothing calls it.

**Task**: Wire `auto_improve.py`'s improvement actions into `self_improvement.py`. When auto-improve identifies a `skill_gap` or `content_quality` failure, it should generate a document patch for the relevant skill prompt (in `able/skills/library/*/SKILL.md`) and submit it through the approval workflow. For `under_routing` and `over_routing`, the evolution daemon's weight deployer already handles this — just verify the handoff is clean.

**Key files**:
- `able/core/evolution/auto_improve.py` (failure classifier)
- `able/core/agi/self_improvement.py` (document update engine)
- `able/core/approval/workflow.py` (approval gating)

### 2. Close the proactive → evolution loop

`able/core/agi/proactive.py` runs background checks including `LearningInsights` that detect recurring patterns. These insights are logged but don't feed back into the evolution daemon's collector.

**Task**: Add a `submit_insight()` method to the evolution collector (`able/core/evolution/collector.py`) that accepts proactive findings. Have `proactive.py`'s LearningInsights check call it when patterns are detected. The daemon's next analysis cycle should consider these alongside interaction metrics.

### 3. Add control plane endpoint tests

All 7 control plane endpoints have zero test coverage. The endpoints are registered in `able/core/gateway/gateway.py` (lines ~1700-1710).

**Task**: Create `able/tests/test_control_plane.py` with tests for:
- `GET /health` returns 200 with status
- `GET /control/tools/catalog` returns tool list (with and without service token)
- `GET /control/resources` returns resource inventory
- `GET /control/resources/{id}` returns detail for known resource, 404 for unknown
- `POST /control/resources/{id}/action` requires both `approved_by` and service token
- `GET /control/collections` returns bundle list
- `GET /control/setup-wizard` returns validation steps

Mock the gateway internals; test the HTTP layer and auth gating.

### 4. Add resource action tool

The LLM can list/inspect resources (`resource_list`, `resource_status` tools) but cannot trigger lifecycle actions.

**Task**: Add `resource_action` in `able/core/gateway/tool_defs/resource_tools.py` (or a new file). It should:
- Accept `resource_id`, `action`, and optional parameters
- Route through `ApprovalWorkflow.request_approval()` before executing
- Call `ResourcePlane.perform_action()` with `service_token_verified=True` and `approved_by` from the approval result
- Return the action result to the LLM
- Register in `ToolRegistry` with `requires_approval=True`, `risk_level="high"`

### 5. Distillation corpus acceleration

Currently ~20 training pairs, need 100+ for the first H100 fine-tuning run. The eval pipeline already captures T4 gold outputs.

**Task**: 
- Review `able/evals/collect_results.py` — verify it's correctly exporting distillation pairs from eval runs
- Add a corpus threshold check: when `data/distillation_*.jsonl` collectively reach 100+ pairs, log a clear "CORPUS READY" message
- If time permits, add 2-3 new eval configs targeting domains with the most routing traffic (check interaction_log.db for domain distribution)

### 6. Evolution daemon integration test

The 5-step cycle (collect → analyze → improve → validate → deploy) has no end-to-end test.

**Task**: Create `able/tests/test_evolution_cycle.py` that:
- Seeds a mock interaction log with 25+ records
- Runs a single daemon cycle (`--once --dry-run`)
- Verifies the analyzer produces bounded weight suggestions
- Verifies the validator rejects out-of-bounds changes
- Verifies the deployer creates a versioned backup

## Rules

- All imports: `from able.X.Y import Z` — shims are gone, bare imports will crash
- Commit to a feature branch (you're on `codex/able-rewrite-integration`)
- Run `python -m pytest able/tests/test_cli_chat.py` before every commit
- Update `CODE_HANDOFF.md` if you change architecture, entry points, or model roster
- No marketing copy — factual, operator-facing language only
- Quant sizes are pinned — don't touch without re-measuring
- Trust `config/routing_config.yaml` for budget/tier numbers

## When done

- Note the HEAD commit
- List what you changed and what you did NOT finish
- Include exact validation commands for verifying the work
- Flag any files modified but not tested
- Update the "What Was Just Completed" section of CODE_HANDOFF.md
