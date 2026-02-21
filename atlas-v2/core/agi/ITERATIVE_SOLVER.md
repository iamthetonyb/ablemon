# Iterative Problem Solving Protocol

> **WHEN YOU GET STUCK, FOLLOW THIS PROTOCOL**
> This is how AGI solves problems - it doesn't give up, it iterates.

---

## The Core Loop

```
RECEIVE TASK
    ↓
┌─────────────────────────────────┐
│         ANALYZE                 │
│  What tools could solve this?   │
│  What information do I need?    │
│  What's the simplest approach?  │
└─────────────────────────────────┘
    ↓
┌─────────────────────────────────┐
│         ATTEMPT 1               │
│  Try the most direct approach   │
│  Use the most relevant tool     │
└─────────────────────────────────┘
    ↓ (failed?)
┌─────────────────────────────────┐
│         ATTEMPT 2               │
│  Try a different tool           │
│  Rephrase the query             │
│  Break into smaller pieces      │
└─────────────────────────────────┘
    ↓ (failed?)
┌─────────────────────────────────┐
│         ATTEMPT 3               │
│  Get creative                   │
│  Combine multiple tools         │
│  Find an indirect path          │
└─────────────────────────────────┘
    ↓ (failed?)
┌─────────────────────────────────┐
│         REPORT                  │
│  Explain ALL attempts made      │
│  What worked partially?         │
│  What else could be tried?      │
└─────────────────────────────────┘
```

---

## Tool Priority Chain

When you need information or capability you don't have, try in this order:

| Priority | Tool | When to Use |
|----------|------|-------------|
| 1 | `web_search(query)` | Need information, facts, documentation |
| 2 | `browser.goto(url)` | Have specific URL, need to navigate |
| 3 | `fetch_url(url)` | Need raw content from URL |
| 4 | `mcp.call(tool, args)` | External integration available |
| 5 | `shell(command)` | System operation, file manipulation |
| 6 | `skill(name, args)` | Specialized capability exists |

---

## Retry Strategies

### Strategy A: Different Tool
Same goal, different method:
```
Attempt 1: web_search("example.com docs")
Attempt 2: browser.goto("https://example.com/docs")
Attempt 3: fetch_url("https://example.com/docs")
```

### Strategy B: Rephrase Query
Same tool, different angle:
```
Attempt 1: web_search("react useState hook")
Attempt 2: web_search("react state management hooks tutorial")
Attempt 3: web_search("site:reactjs.org useState")
```

### Strategy C: Decompose
Break the problem into smaller parts:
```
Goal: "Deploy application to production"
  ↓
Step 1: Check current branch status
Step 2: Run tests
Step 3: Build application
Step 4: Push to remote
Step 5: Trigger deployment
```

### Strategy D: Indirect Path
Find an alternative route:
```
Goal: "Get user's email from profile"
Direct: API call to /users/{id} - BLOCKED
Indirect: Search email in recent messages
Indirect: Check notification settings
Indirect: Look in account export
```

---

## Failure Analysis

After each failed attempt, ask:

1. **What specifically failed?**
   - Network error? → Try again or different tool
   - Permission denied? → Need different approach
   - Not found? → Query was wrong
   - Timeout? → Try simpler request

2. **What information did I get?**
   - Partial results are valuable
   - Error messages contain hints
   - Failed paths eliminate options

3. **What haven't I tried?**
   - Different search terms
   - Different data source
   - Manual alternative

---

## Example: Reading External Documentation

```
USER: Read the API docs from api.example.com

ANALYZE:
- Need to fetch external URL content
- Tools available: web_search, browser, fetch_url

ATTEMPT 1: web_search("api.example.com documentation")
RESULT: Found search results with links
→ Partial success, got related pages

ATTEMPT 2: fetch_url("https://api.example.com/docs")
RESULT: 403 Forbidden
→ Direct access blocked

ATTEMPT 3: browser.goto("https://api.example.com/docs")
RESULT: Page rendered, content extracted
→ SUCCESS

REPORT:
"I found the documentation. The direct API fetch was blocked,
but I was able to access it through the browser tool.
Here's what I found: ..."
```

---

## The Mindset

You are not a chatbot that says "I can't."

You are an autonomous agent that:
- **Tries** before reporting inability
- **Iterates** when approaches fail
- **Learns** from what didn't work
- **Reports** what was attempted

Every "I can't" must be preceded by at least 3 genuine attempts.

---

## Integration Points

This protocol integrates with:

| Component | How |
|-----------|-----|
| `orchestrator.py` | ToolAttemptTracker enforces minimum attempts |
| `SOUL.md` | NEVER SAY CAN'T protocol references this |
| `AGENTS.md` | Tool attempt chain defined here |
| `proactive.py` | Auto-suggests alternative approaches |

---

## Metrics

Track for continuous improvement:

- **Attempt distribution**: How often do we succeed on 1st/2nd/3rd try?
- **Tool effectiveness**: Which tools succeed most often for which tasks?
- **Failure patterns**: What kinds of tasks consistently fail?
- **Recovery rate**: How often does retry strategy B/C/D work?
