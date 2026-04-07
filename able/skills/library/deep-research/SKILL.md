# Deep Research Skill

> Multi-agent research with source grounding, structured extraction, and knowledge graph output.

## Triggers
- "deep research", "investigate thoroughly", "research report on"
- "deep dive into", "comprehensive analysis of"

## Trust Level
L3 — ACT (executes web searches, writes to Trilium)

## Workflow

### 1. Query Expansion
- Break the topic into 5-8 specific search queries
- Include recency markers (this week/month)
- Add domain-specific keywords

### 2. Parallel Web Search
- Run all queries via WebSearch (DuckDuckGo)
- For top results, use XCrawl to get full structured content (not just snippets)
- Cap at 20 unique URLs

### 3. Source Verification (Feynman Pattern)
- HEAD request to verify all URLs are reachable
- Cross-verify specific claims (version numbers, benchmarks) via secondary search
- Tag each finding: `#verified`, `#unverified`, `#broken-link`, `#contested`

### 4. LLM Analysis
- Route findings through M2.7 (background model) for synthesis
- Extract: key themes, contradictions, action items, open questions
- Identify high-value threads for follow-up research

### 5. Knowledge Graph
- Build NetworkX graph from findings (topics → tags → sources)
- Apply Louvain community detection for topic clustering
- Export interactive HTML to `data/research_graph.html`

### 6. Trilium Filing
- Create summary note with mermaid topic map
- Create per-finding child notes with cross-references
- Link web clipper articles that match research topics
- File verification results and confidence scores

## Output Format

```
## Deep Research Report: [TOPIC]

### Summary
[2-3 paragraph synthesis]

### Key Findings (verified)
1. [Finding with source]
2. [Finding with source]

### Contested Claims
- [Claim] — sources disagree: [source A] vs [source B]

### Action Items
- Quick wins: [...]
- Strategic: [...]

### Knowledge Graph
[Link to interactive visualization]

### Open Questions
- [Question for follow-up research]
```

## Dependencies
- `able.tools.search.web_search.WebSearch`
- `able.tools.xcrawl.client.XCrawlClient` (optional — enhances extraction)
- `able.core.evolution.source_grounder.SourceGrounder`
- `able.tools.graphify.builder.build_research_graph`
- `able.tools.trilium.wiki_skill.wiki_ingest_research`
