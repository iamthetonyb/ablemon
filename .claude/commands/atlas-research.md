---
name: atlas-research
description: Research a topic using web search, fetch sources, and synthesize findings into a structured report with citations. Triggers on research, look up, investigate, find out about.
user-invocable: true
---

# /atlas-research $ARGUMENTS

Research: **$ARGUMENTS**

## Protocol

1. **Generate 3-5 search queries** based on the topic:
   - Main topic query
   - Specific aspect queries
   - Recent/news query (add current year)

2. **Execute searches** using WebSearch for each query

3. **Deduplicate and rank** results:
   - Prioritize: official docs > authoritative sources > recent publications
   - Avoid: forums, social media, SEO spam

4. **Fetch top sources** using WebFetch (3 for quick, 10 for deep)

5. **Synthesize** findings:
   - Identify common themes and contradictions
   - Extract key facts, form coherent narrative

6. **Output** in this format:

```markdown
## Research: $ARGUMENTS

### Summary
[200-500 word synthesis]

### Key Points
- Point 1
- Point 2
- Point 3

### Sources
1. [Title](url) — what this contributed
2. [Title](url) — what this contributed

### Confidence: {high/medium/low}
[Brief note on source quality]
```

## Depth Options
- Default: quick (3 sources)
- If user says "deep research" or "thorough": deep (10 sources)

Reference: `atlas/skills/library/web-research/SKILL.md` for full protocol.
