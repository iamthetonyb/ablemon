---
name: self-improvement
description: "Structured learning system for continuous self-improvement. Use this skill to log learnings, track errors, record feature requests, detect recurring patterns, and extract new skills from repeated workflows. Triggers on: learning, error tracking, pattern detection, skill extraction, retrospective, what went wrong, lessons learned, improve system."
---

# Self-Improvement

> Continuous learning through structured logging, pattern detection, and automatic skill extraction.

## Workspace Structure

```
.learnings/
├── LEARNINGS.md        # What we learned (LRN entries)
├── ERRORS.md           # What went wrong (ERR entries)
└── FEATURE_REQUESTS.md # What users wanted (FEAT entries)
```

## Logging Format

### Learning Entry

Append to `.learnings/LEARNINGS.md`:

```markdown
## [LRN-YYYYMMDD-XXX] category
**Logged**: ISO-8601 timestamp
**Priority**: low | medium | high | critical
**Status**: pending | resolved | promoted
**Area**: frontend | backend | infra | gateway | skills | security

### Summary
One-line description of what was learned

### Details
Full context: what happened, what was wrong, what's correct

### Suggested Action
Specific fix or improvement to make

### Metadata
- Source: conversation | error | user_feedback
- Related Files: path/to/file.ext
- Tags: tag1, tag2
---
```

### Error Entry

Append to `.learnings/ERRORS.md`:

```markdown
## [ERR-YYYYMMDD-XXX] component_name
**Logged**: ISO-8601 timestamp
**Priority**: high
**Status**: pending | resolved
**Area**: frontend | backend | infra | gateway | skills | security

### Summary
Brief description of what failed

### Error
```
Actual error message or traceback
```

### Context
- Command/operation attempted
- Input or parameters used
- Environment details if relevant

### Fix Applied
What resolved the issue (fill in after resolution)

### Metadata
- Reproducible: yes | no | unknown
- Related Files: path/to/file.ext
- See Also: ERR-YYYYMMDD-XXX (if recurring)
---
```

### Feature Request Entry

Append to `.learnings/FEATURE_REQUESTS.md`:

```markdown
## [FEAT-YYYYMMDD-XXX] capability_name
**Logged**: ISO-8601 timestamp
**Priority**: medium
**Status**: pending | planned | implemented
**Area**: frontend | backend | infra | gateway | skills | security

### Requested Capability
What the user wanted to do

### User Context
Why they needed it, what problem they're solving

### Complexity Estimate
simple | medium | complex

### Suggested Implementation
How this could be built, what it might extend

### Metadata
- Frequency: first_time | recurring
- Related Features: existing_feature_name
---
```

## ID Generation

Format: `{TYPE}-{YYYYMMDD}-{XXX}`

- TYPE: LRN, ERR, or FEAT
- YYYYMMDD: Date logged
- XXX: Sequential counter for that day (001, 002, etc.)

## Recurring Pattern Detection

When logging something similar to an existing entry:

1. **Search first**: `grep -r "keyword" .learnings/`
2. **Link entries**: Add `See Also: ERR-YYYYMMDD-XXX` in Metadata
3. **Bump priority** if issue keeps recurring
4. Consider systemic fix:
   - Missing documentation → promote to CLAUDE.md or system prompt
   - Missing automation → create new skill or cron job
   - Architectural problem → create refactoring ticket

## Periodic Review

Review `.learnings/` at natural breakpoints (end of session, end of day, end of week):

### Quick Status Check
```bash
# Count by status
grep -c "Status: pending" .learnings/*.md
grep -c "Status: resolved" .learnings/*.md

# Find high-priority unresolved
grep -B2 "Priority: high" .learnings/*.md | grep "Status: pending"
```

### Review Actions
- Resolve entries that have been fixed
- Bump priority on entries that keep recurring
- Promote important learnings to system-level docs
- Extract patterns into new skills (see below)

## Automatic Skill Extraction

When a pattern appears 3+ times in learnings:

### Extraction Criteria
- Same workflow executed manually 3+ times
- Same error encountered and fixed 3+ times
- Same user request pattern with same response

### Extraction Workflow
1. Identify the recurring pattern from `.learnings/`
2. Draft a SKILL.md capturing the workflow
3. Run through skill-tester validation
4. Register in SKILL_INDEX.yaml
5. Mark related learning entries as `promoted`

## Best Practices

- Log immediately when something is learned (don't wait)
- Be specific — "gateway.py line 450 needs try/except" not "error handling needed"
- Include the actual error message, not a summary
- Link related entries to build a knowledge graph
- Review weekly minimum, daily preferred
