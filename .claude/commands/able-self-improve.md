Log a learning, error, or feature request using the ABLE Self-Improvement skill.

Follow the self-improvement SKILL.md protocol:
1. Check if similar entry exists: `grep -r "keyword" .learnings/`
2. Generate ID: {TYPE}-{YYYYMMDD}-{XXX}
3. Append to appropriate file (.learnings/LEARNINGS.md, ERRORS.md, or FEATURE_REQUESTS.md)
4. If recurring pattern detected, bump priority and link entries
5. If pattern appears 3+ times, consider extracting a new skill

Types:
- LRN: Something learned (include: what happened, what's correct, suggested action)
- ERR: Something that went wrong (include: error message, context, fix applied)
- FEAT: User wanted something we can't do yet (include: capability, complexity estimate)

Reference: able/skills/library/self-improvement/SKILL.md
