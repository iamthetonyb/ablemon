---
name: atlas-skill-creator
description: Create new ATLAS skills using the 6-step process. Initializes scaffolding, writes SKILL.md, validates, and registers in SKILL_INDEX.yaml. Use when adding new capabilities or when a task recurs 3+ times.
user-invocable: true
---

# /atlas-skill-creator $ARGUMENTS

Create a new ATLAS skill for: **$ARGUMENTS**

## 6-Step Process

### Step 1: Understand
- What does this skill do? What triggers it?
- What are the inputs/outputs?
- What trust level? (L1_OBSERVE, L2_SUGGEST, L3_ACT, L4_AUTONOMOUS)
- Ask if unclear — don't assume.

### Step 2: Plan
- Identify reusable components: scripts, references, assets
- Check if a similar skill exists on skills.sh: `npx skills search $ARGUMENTS`
- Check existing skills in `atlas/skills/SKILL_INDEX.yaml` for overlap

### Step 3: Initialize
```bash
python atlas/skills/scripts/init_skill.py "$ARGUMENTS" --path atlas/skills/library --resources scripts,references,assets
```

### Step 4: Edit
Write `SKILL.md` with YAML frontmatter:
```yaml
---
name: skill-name
description: What it does AND when to use it. Be specific about trigger scenarios.
---
```
Keep SKILL.md under 500 lines. Move detailed docs to `references/`.

### Step 5: Package & Validate
```bash
python atlas/skills/scripts/package_skill.py atlas/skills/library/$ARGUMENTS
```
Run malware scan:
```python
from atlas.security.malware_scanner import scan_skill
await scan_skill("atlas/skills/library/$ARGUMENTS/")
```

### Step 6: Register
Add entry to `atlas/skills/SKILL_INDEX.yaml` with triggers, type, trust level.
Also create matching Claude Code skill in `.claude/skills/atlas-$ARGUMENTS/SKILL.md`.

### Step 7: Test
Use `/atlas-skill-tester` to validate the new skill works correctly.

## Naming Rules
- lowercase, hyphens only: `my-skill`
- verb-led preferred: `analyze-logs`, `draft-email`
- max 64 characters
