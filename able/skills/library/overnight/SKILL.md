# SKILL.md — Overnight Orchestrator

> Autonomous iteration-commit-rollback loop for long-running tasks.

---

## Purpose

Run a multi-iteration task overnight with automatic git commits on success
and rollbacks on failure. Cross-iteration learning via notes.md.
3-consecutive-failure abort with exponential backoff.

---

## Triggers

- "run overnight"
- "work overnight on {task}"
- "autonomous loop"
- "work while I sleep"
- "overnight {task}"

---

## Trust Required

**L3** (Execute) — This skill writes files, runs commands, and commits to git.

---

## Inputs

| Name | Type | Required | Description |
|------|------|----------|-------------|
| task | string | yes | What to accomplish across iterations |
| max_iterations | int | no | Maximum iterations (default: 10) |
| work_dir | string | no | Working directory (default: current project) |
| commit_prefix | string | no | Git commit message prefix (default: "overnight") |

---

## Outputs

| Name | Type | Description |
|------|------|-------------|
| report | OvernightReport | Iterations run, succeeded, failed, abort reason |
| notes | string | Cross-iteration learnings accumulated in notes.md |

---

## Execution Plan

1. Parse task description and constraints
2. Initialize OvernightLoop with configured parameters
3. Define iteration function that:
   a. Reads current notes.md for prior learnings
   b. Executes one iteration of the task
   c. Returns IterationResult with summary and learnings
4. Run the loop (commits on success, rolls back on failure)
5. Return OvernightReport with full execution summary

---

## Constraints

- Maximum 10 iterations by default (configurable)
- 3 consecutive failures trigger abort
- Exponential backoff: 60s * 2^(failures - 1)
- All work committed to git for reversibility
- notes.md persists learnings across iterations
