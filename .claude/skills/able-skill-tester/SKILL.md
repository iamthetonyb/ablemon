---
name: able-skill-tester
description: Test and validate ABLE skills before registration. Checks SKILL.md format, runs malware scan, validates triggers, and does a dry-run. Use after creating or modifying skills.
user-invocable: true
---

# /able-skill-tester $ARGUMENTS

Test and validate skill: **$ARGUMENTS**

## Validation Steps

### 1. Format Check
- Verify `able/skills/library/$ARGUMENTS/SKILL.md` exists
- Check SKILL.md has required sections: Purpose, Triggers, Inputs, Outputs
- Verify YAML frontmatter if present (name, description)
- Check SKILL.md is under 500 lines (context window efficiency)

### 2. Security Scan
```bash
python -c "
import asyncio
from able.security.malware_scanner import scan_skill
result = asyncio.run(scan_skill('able/skills/library/$ARGUMENTS/'))
print(f'Scan result: {result}')
"
```
Must return CLEAN. Block SUSPICIOUS/DANGEROUS/MALICIOUS.

### 3. Package Validation
```bash
python able/skills/scripts/package_skill.py able/skills/library/$ARGUMENTS
```

### 4. Registry Check
- Verify skill is in `able/skills/SKILL_INDEX.yaml`
- Check triggers don't conflict with existing skills
- Verify trust level is appropriate

### 5. Dry Run
- Load skill via `able/skills/loader.py`
- Verify it loads without errors
- Check all dependencies are available

## Output

```
SKILL TEST REPORT: $ARGUMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Format:    [PASS/FAIL] — details
Security:  [PASS/FAIL] — scan result
Package:   [PASS/FAIL] — validation result
Registry:  [PASS/FAIL] — entry found/missing
Dry Run:   [PASS/FAIL] — load result
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Overall:   [PASS/FAIL]
```
