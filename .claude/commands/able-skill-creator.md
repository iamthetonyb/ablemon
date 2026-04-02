---
name: able-skill-creator
description: Create new ABLE skills or improve existing ones. Two modes — CREATE (new capability from scratch) and IMPROVE (enhance an existing skill based on outcome data or user feedback). Use when adding a new capability, when a task has recurred 3+ times without a skill, or when skill_outcomes.jsonl shows a skill failing repeatedly. After creating or improving, always run /able-skill-tester.
user-invocable: true
---

# /able-skill-creator $ARGUMENTS

Skill target: **$ARGUMENTS**

---

## Detect mode

First, check if `$ARGUMENTS` already exists:

```bash
ls able/skills/library/$ARGUMENTS/SKILL.md 2>/dev/null && echo EXISTS || echo NEW
```

- **EXISTS** → IMPROVE mode
- **NOT FOUND** → CREATE mode

---

## CREATE MODE — New skill from scratch

### Step 1: Understand intent

Ask (or extract from context):
1. What does this skill enable ABLE to do?
2. What phrases would trigger it? (specific — "send cold email" not "communicate")
3. What's the expected output format?
4. Trust level: L1 (read-only), L2 (suggest), L3 (act), L4 (autonomous)?
5. Does it need `implement.py` (tool/hybrid) or just `SKILL.md` (behavioral)?

Don't proceed until you have clear answers to 1–3.

### Step 2: Check for overlap

```bash
grep -A3 "triggers:" able/skills/SKILL_INDEX.yaml | grep -i "$ARGUMENTS"
npx skills search "$ARGUMENTS" 2>/dev/null | head -20
```

If a similar skill exists on skills.sh, install it instead of building from scratch.

### Step 3: Initialize scaffold

```bash
python able/skills/scripts/init_skill.py "$ARGUMENTS" \
  --path able/skills/library \
  --resources scripts,references,assets
```

### Step 4: Write SKILL.md

YAML frontmatter (required):
```yaml
---
name: skill-name
description: >
  What it does AND specific phrases/contexts that trigger it.
  All "when to use" info goes here — body loads AFTER triggering.
  Be concrete: "Write a cold email to a prospect" not "help with writing".
---
```

Body: Purpose → Protocol (exact steps) → Output format → Edge cases.
Keep under 300 lines. Move reference docs to `references/`.

### Step 5: Security scan (required)

```bash
python -c "
import asyncio
from able.security.malware_scanner import scan_skill
result = asyncio.run(scan_skill('able/skills/library/$ARGUMENTS/'))
print(f'Scan: {result}')
"
```

Must return CLEAN.

### Step 6: Register in SKILL_INDEX.yaml

```yaml
  $ARGUMENTS:
    description: "same as frontmatter description"
    triggers: ["phrase 1", "phrase 2"]
    type: "behavioral|tool|hybrid"
    trust_level: "L1_OBSERVE|L2_SUGGEST|L3_ACT|L4_AUTONOMOUS"
    requires_approval: false
    created: "YYYY-MM-DD"
    use_count: 0
```

### Step 7: Create Claude Code command

```bash
cp able/skills/library/$ARGUMENTS/SKILL.md .claude/commands/able-$ARGUMENTS.md
# Update frontmatter to add: user-invocable: true
```

### Step 8: Test before committing

Run `/able-skill-tester $ARGUMENTS` — A/B test with vs without skill.

---

## IMPROVE MODE — Enhance existing skill

Use when `skill_outcomes.jsonl` shows failures, user flags it broken, or weekly reflection flags it underperforming.

### Step 1: Diagnose

```bash
python3 -c "
import json
outcomes = []
try:
    with open('able/audit/logs/skill_outcomes.jsonl') as f:
        for line in f:
            e = json.loads(line)
            if e.get('skill') == '$ARGUMENTS':
                outcomes.append(e)
except FileNotFoundError:
    pass
negatives = [o for o in outcomes if o.get('outcome') == 'negative']
print(f'Total: {len(outcomes)}, Failures: {len(negatives)}')
for n in negatives[-10:]:
    print(f'  Trigger: {n[\"trigger\"]!r}  Signal: {n[\"signal\"]!r}')
"
```

Read current SKILL.md. Identify root cause: unclear protocol? Wrong format? Missing steps?

### Step 2: Snapshot current version

```bash
cp able/skills/library/$ARGUMENTS/SKILL.md /tmp/skill-backup-$ARGUMENTS-$(date +%s).md
```

### Step 3: Write improved SKILL.md

Focus: make protocol more specific where failures occurred. Add edge case handling. Tighten output format. Don't make it longer — make it clearer.

### Step 4: Create improvement branch + PR

```bash
git checkout -b feat/skill-improve-$ARGUMENTS
git add able/skills/library/$ARGUMENTS/ .claude/commands/able-$ARGUMENTS.md
git commit -m "improve($ARGUMENTS): [1-line summary of what changed]"
git push origin feat/skill-improve-$ARGUMENTS
gh pr create \
  --title "improve($ARGUMENTS): [what changed]" \
  --body "$(printf '## Why\n[failures that drove this]\n\n## What changed\n[specific changes]\n\n## Test\nRun /able-skill-tester $ARGUMENTS')"
```

### Step 5: A/B test before merging

Run `/able-skill-tester $ARGUMENTS` — compares new vs snapshotted old.
Only merge if delta is positive. Negative delta → do not merge.

---

## Naming rules

- lowercase, hyphens only: `my-skill` not `MySkill`
- verb-led preferred: `analyze-logs`, `draft-email`
- max 64 characters
