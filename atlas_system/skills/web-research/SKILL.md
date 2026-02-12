# SKILL.md — Web Research

> Search the web and synthesize findings into a structured report.

---

## Purpose

Research a topic using web search, fetch relevant sources, and produce a synthesized summary with citations.

---

## Triggers

- "research {topic}"
- "find out about {topic}"
- "look up {topic}"
- "search for {topic}"
- When user asks questions requiring current information

---

## Trust Required

**L1** (Observe) — This skill is read-only.

---

## Inputs

| Name | Type | Required | Description |
|------|------|----------|-------------|
| topic | string | yes | What to research |
| depth | string | no | "quick" (3 sources) or "deep" (10 sources). Default: quick |
| focus | string | no | Specific angle or question to answer |

---

## Outputs

| Name | Type | Description |
|------|------|-------------|
| summary | markdown | Synthesized findings (200-500 words) |
| key_points | list | 3-5 bullet points with main takeaways |
| sources | list | URLs with titles, used in research |
| confidence | string | "high", "medium", "low" based on source quality |

---

## Implementation

### Step 1: Generate Search Queries

Based on the topic, generate 3-5 search queries:
- Main topic query
- Specific aspect queries
- Recent/news query (add "2026" or "latest")

```
Example for "AI agent security":
1. "AI agent security best practices"
2. "prompt injection defense 2026"
3. "LLM agent vulnerabilities OWASP"
4. "AI agent sandboxing techniques"
```

### Step 2: Execute Searches

Use `web_search` for each query:

```python
results = []
for query in queries:
    search_results = web_search(query, max_results=3)
    results.extend(search_results)
```

### Step 3: Deduplicate and Rank

Remove duplicate URLs, prioritize:
1. Official documentation
2. Peer-reviewed or authoritative sources
3. Recent publications (prefer 2025-2026)
4. Avoid: forums, social media, SEO spam

### Step 4: Fetch Top Sources

Use `web_fetch` on top 3-10 sources (based on depth):

```python
contents = []
for source in top_sources:
    content = web_fetch(source.url)
    contents.append({
        "url": source.url,
        "title": content.title,
        "text": content.content[:5000]  # Truncate if huge
    })
```

### Step 5: Synthesize

Combine information from all sources:
- Identify common themes
- Note contradictions
- Extract key facts
- Form coherent narrative

### Step 6: Format Output

```markdown
## Research: {topic}

### Summary
[200-500 word synthesis]

### Key Points
- Point 1
- Point 2
- Point 3

### Sources
1. [Title](url) — Brief note on what this contributed
2. [Title](url) — Brief note
3. [Title](url) — Brief note

### Confidence: {high/medium/low}
[Brief note on source quality and coverage]
```

---

## Example

**Input**:
```
research "OWASP LLM Top 10 2025" depth:quick
```

**Output**:
```markdown
## Research: OWASP LLM Top 10 2025

### Summary
The OWASP LLM Top 10 2025 identifies the most critical security 
risks for applications using large language models. Prompt injection 
remains the #1 threat, now categorized as both direct (user-supplied) 
and indirect (embedded in external content). New entries include 
"Agentic Risks" reflecting the rise of autonomous AI agents with 
tool access...

### Key Points
- Prompt injection is #1, with indirect injection most dangerous for agents
- "Agentic Risks" is new, covering tool misuse and privilege escalation
- Data poisoning moved up to #3 due to RAG vulnerabilities
- Recommendations emphasize defense-in-depth and human oversight

### Sources
1. [OWASP LLM Top 10 2025](https://owasp.org/...) — Primary source
2. [LLM Security Landscape 2025](https://...) — Industry analysis
3. [Agentic AI Security Guide](https://...) — Deep dive on agent risks

### Confidence: High
Multiple authoritative sources with consistent findings.
```

---

## Error Handling

| Error | Response |
|-------|----------|
| No search results | Try broader query, report if still empty |
| All fetches fail | Return search snippets only, note limitation |
| Contradictory sources | Present both views, note disagreement |
| Topic too broad | Ask user to narrow focus |

---

## Notes

- Always cite sources
- Prefer recent information (2025-2026)
- If topic is time-sensitive, note when sources were published
- For controversial topics, present multiple perspectives
- Never present speculation as fact
