---
name: atlas-skill-loop
description: Run the full autonomous skill improvement cycle. Reads skill_outcomes.jsonl, ranks skills by failure rate, generates improved versions of the worst performers, A/B tests them, and creates GitHub PRs for the winners. Use weekly or whenever skill performance is declining. This is the AGI self-improvement loop — it finds what's broken and fixes it without being told which skill to improve.
user-invocable: true
---

# /atlas-skill-loop $ARGUMENTS

Run the autonomous skill improvement cycle.

`$ARGUMENTS` can specify:
- empty → run full cycle on all skills
- `--skill <name>` → run cycle on one specific skill
- `--dry-run` → analyze and report only, don't commit improvements
- `--top <N>` → improve top N worst performers (default: 3)

---

## Step 1: Load outcome data

```bash
python3 << 'EOF'
import json
from collections import defaultdict

outcomes_by_skill = defaultdict(list)
try:
    with open('atlas/audit/logs/skill_outcomes.jsonl') as f:
        for line in f:
            try:
                e = json.loads(line.strip())
                skill = e.get('skill')
                if skill:
                    outcomes_by_skill[skill].append(e)
            except json.JSONDecodeError:
                pass
except FileNotFoundError:
    print("No outcome data yet — system is still accumulating.")
    exit(0)

# Compute stats per skill
print("\nSKILL PERFORMANCE REPORT")
print("=" * 50)
stats = []
for skill, outcomes in outcomes_by_skill.items():
    total = len(outcomes)
    positive = sum(1 for o in outcomes if o.get('outcome') == 'positive')
    negative = sum(1 for o in outcomes if o.get('outcome') == 'negative')
    unknown = total - positive - negative
    rate = (positive / total * 100) if total > 0 else None
    stats.append((skill, total, positive, negative, unknown, rate))

# Sort by opportunity: high use + low success rate
stats.sort(key=lambda x: (x[1] * (1 - (x[5] or 50) / 100)), reverse=True)

for skill, total, pos, neg, unk, rate in stats:
    rate_str = f"{rate:.0f}%" if rate is not None else "N/A"
    flag = "🔴" if (rate and rate < 60) else "🟡" if (rate and rate < 80) else "🟢"
    print(f"{flag} {skill:<30} {total:>4} uses  {rate_str:>6} success  ({neg} failures)")

print()
print("Candidates for improvement: skills with <70% success and 3+ uses")
EOF
```

---

## Step 2: Identify improvement candidates

From the report above, select skills that are:
- Success rate < 70%
- At least 3 uses (enough data to know it's actually broken, not just new)
- Has a `SKILL.md` file (behavioral/hybrid skills can be improved by editing)

Default: improve top 3 by opportunity score (use_count × failure_rate).

Report to user:
```
Top improvement candidates:
1. [skill-name] — X% success, Y failures out of Z uses
2. [skill-name] — ...
3. [skill-name] — ...

Proceeding to generate improvements. This will create GitHub PRs for review.
```

---

## Step 3: For each candidate — diagnose and improve

For each skill in the candidate list, run the improve flow:

### 3a. Read failure patterns

```bash
python3 -c "
import json
outcomes = []
with open('atlas/audit/logs/skill_outcomes.jsonl') as f:
    for line in f:
        e = json.loads(line)
        if e.get('skill') == 'SKILL_NAME' and e.get('outcome') == 'negative':
            outcomes.append(e)
for o in outcomes[-15:]:
    print(f'Trigger: {o[\"trigger\"]!r}')
    print(f'Signal:  {o[\"signal\"]!r}')
    print()
"
```

### 3b. Generate improved SKILL.md

Read current SKILL.md. Read failure patterns. Use LLM (via provider chain) to generate an improved version that:
- Addresses the specific failure patterns observed
- Keeps the same core purpose
- Is no longer than the original (clarity > length)
- Preserves working parts of the protocol

### 3c. A/B test the improvement

Run `/atlas-skill-tester [skill-name]` — compares new vs old.

If delta positive → proceed to PR.
If delta negative or zero → log "no improvement found" and skip.

### 3d. Create GitHub PR

```bash
SKILL_NAME="[skill-name]"
git checkout main
git pull origin main
git checkout -b feat/skill-improve-$SKILL_NAME-$(date +%Y%m%d)

# Apply improvement
# (improved SKILL.md is already written)
cp atlas/skills/library/$SKILL_NAME/SKILL.md .claude/commands/atlas-$SKILL_NAME.md

git add atlas/skills/library/$SKILL_NAME/ .claude/commands/atlas-$SKILL_NAME.md
git commit -m "improve($SKILL_NAME): auto-improve based on outcome data

Failure rate: X% → projected Y% (based on A/B test)
Root cause: [what was causing failures]
Fix: [what changed in the protocol]"

git push origin feat/skill-improve-$SKILL_NAME-$(date +%Y%m%d)

gh pr create \
  --title "auto-improve: $SKILL_NAME skill" \
  --body "**Triggered by**: Autonomous skill improvement loop
**Failure rate**: X% (N failures out of M uses)
**Root cause**: [what was broken]
**Fix**: [what changed]
**A/B test**: +X% improvement verified before this PR

Review the diff. If it looks right, merge — CI/CD will deploy to production."
```

---

## Step 4: Send Telegram summary

After processing all candidates, send a summary to owner:

```python
await gateway.approval_workflow.request_approval(
    operation="skill_auto_improve_batch",
    details={
        "candidates": [list of skills],
        "prs_created": [list of PR URLs],
        "skipped": [skills where no improvement found],
    },
    risk_level="low",
    context="Weekly skill improvement cycle complete. PRs created for review."
)
```

---

## Step 5: Update SKILL_INDEX.yaml use_count

After the cycle, reset use_count for improved skills so the next cycle starts fresh:

```bash
# This is done by the backend outcome logger automatically
# Manual reset if needed:
python3 -c "
import yaml
with open('atlas/skills/SKILL_INDEX.yaml') as f:
    index = yaml.safe_load(f)
for skill_name in ['skill1', 'skill2']:
    if skill_name in index['skills']:
        index['skills'][skill_name]['last_improvement'] = '$(date +%Y-%m-%d)'
with open('atlas/skills/SKILL_INDEX.yaml', 'w') as f:
    yaml.dump(index, f, default_flow_style=False)
"
```

---

## Running this autonomously

This skill is designed to be called by the `skill-improvement` cron job (registered in `InitiativeEngine`):

```python
# In initiative.py — runs every Sunday midnight after _self_reflection
scheduler.add_job(
    "skill-improvement-cycle",
    "30 0 * * 0",   # Sunday 12:30am (after self-reflection at midnight)
    self._skill_improvement_cycle,
    description="Weekly autonomous skill improvement cycle",
    timeout=1800    # 30 min max
)
```

It runs on the server, generates PRs, and notifies via Telegram. Human reviews and merges. CI/CD deploys. Loop complete.
