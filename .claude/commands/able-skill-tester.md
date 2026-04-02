---
name: able-skill-tester
description: A/B test ABLE skills against real prompts. Runs with-skill vs without-skill (or old vs new version) using subagents, grades outputs, and generates a benchmark report. Use after creating or modifying any skill, when a skill shows poor performance data in skill_outcomes.jsonl, or before merging an improvement branch. This is a real performance test — not a format check.
user-invocable: true
---

# /able-skill-tester $ARGUMENTS

A/B test ABLE skill: **$ARGUMENTS**

---

## Phase 0: Static checks (must pass before running A/B)

1. Skill exists: `able/skills/library/$ARGUMENTS/SKILL.md`
2. YAML frontmatter has `name` and `description`
3. File is under 500 lines
4. No hardcoded secrets, absolute paths, or user-specific values
5. Triggers in `SKILL_INDEX.yaml` don't conflict with existing skills

If any fail, fix before proceeding.

---

## Phase 1: Read outcomes data

Check `able/audit/logs/skill_outcomes.jsonl` for this skill.

```bash
grep '"skill":"$ARGUMENTS"' able/audit/logs/skill_outcomes.jsonl 2>/dev/null | tail -50
```

Look for:
- What real prompts triggered it
- Success vs failure signals
- Patterns in what failed

Use this data to design realistic test prompts. If no outcomes exist yet, design prompts based on the skill's declared triggers.

---

## Phase 2: Design 3 test prompts

Write 3 prompts that a real user would actually type. Cover:
1. The most common trigger scenario (happy path)
2. An edge case or ambiguous phrasing
3. A harder, multi-step version of the task

Show them: "Here are the test prompts — want to change anything?" Wait for confirmation.

---

## Phase 3: Snapshot current version

Before running (especially if testing an improvement):

```bash
cp able/skills/library/$ARGUMENTS/SKILL.md /tmp/skill-snapshot-$ARGUMENTS-$(date +%s).md
```

---

## Phase 4: Spawn all 6 runs in ONE message

For each of the 3 prompts, spawn TWO subagents **simultaneously** — do not wait for results before spawning.

**With-skill subagent:**
```
Task: [test prompt]
Skill to use: able/skills/library/$ARGUMENTS/SKILL.md
Save output to: /tmp/skill-ab-$ARGUMENTS/with/test-[N]/output.md
```

**Baseline subagent (choose one):**
- New skill → same prompt, no skill
- Improved skill → same prompt, using `/tmp/skill-snapshot-$ARGUMENTS-*.md`
```
Task: [test prompt]
Save output to: /tmp/skill-ab-$ARGUMENTS/baseline/test-[N]/output.md
```

---

## Phase 5: Grade while waiting

Draft binary, objective grading criteria — don't wait for runs to finish.

Good criteria:
- Does the output follow the required format? (Y/N)
- Does it include the required sections? (Y/N)
- Does it avoid the forbidden phrases from SOUL.md? (Y/N)
- Does it answer the actual question asked? (Y/N)
- Is it the right length — not padded, not truncated? (Y/N)

Subjective criteria (tone, creativity) → flag for human review, don't auto-grade.

---

## Phase 6: Score and report

Grade each output. Score = `criteria_passed / total_criteria`.

```
═══════════════════════════════════════
SKILL A/B TEST: $ARGUMENTS
═══════════════════════════════════════

Test 1: [prompt summary]
  With skill:  [X/5] — [score]%
  Baseline:    [X/5] — [score]%
  Delta:       [+/-X%] — [key difference in 1 line]

Test 2: ...
Test 3: ...

─────────────────────────────────────
Avg with skill:  X%
Avg baseline:    Y%
Net improvement: [+/-Z%]

VERDICT: [SHIP IT / ITERATE / DO NOT SHIP]
Why: [2 sentences on what drove the result]
Next: [specific recommendation]
═══════════════════════════════════════
```

---

## Phase 7: Decision

| Delta | Action |
|-------|--------|
| +10% or more | Ship it. Commit and push. |
| +5% to +10% | Ship it. Monitor outcomes. |
| 0% to +5% | Marginal. Ask user to review outputs and decide. |
| Negative | Do not ship. The skill made things worse. |

If shipping: commit both `able/skills/library/$ARGUMENTS/` and `.claude/commands/able-$ARGUMENTS.md`, then push to `main`.
