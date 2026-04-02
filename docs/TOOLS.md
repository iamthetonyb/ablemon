# TOOLS.md — Available Capabilities

> Tools available to ABLE and their usage notes.
> Tool availability depends on trust tier.

---

## Tool Availability Matrix

| Tool | L1 | L2 | L3 | L4 | Notes |
|------|:--:|:--:|:--:|:--:|-------|
| read | ✓ | ✓ | ✓ | ✓ | Read any file in workspace |
| list | ✓ | ✓ | ✓ | ✓ | List directory contents |
| web_search | ✓ | ✓ | ✓ | ✓ | Search the web |
| web_fetch | ✓ | ✓ | ✓ | ✓ | Fetch URL content |
| write | ✗ | ✓* | ✓ | ✓ | Write files (*needs approval) |
| edit | ✗ | ✓* | ✓ | ✓ | Edit existing files |
| bash | ✗ | ✗ | ✓* | ✓ | Execute shell commands |
| telegram_send | ✗ | ✓* | ✓ | ✓ | Send Telegram messages |
| session_spawn | ✗ | ✗ | ✓* | ✓ | Spawn sub-agents |
| memory_write | ✗ | ✓ | ✓ | ✓ | Write to memory store |

`*` = Requires approval at this tier

---

## Filesystem Tools

### read(path)

Read file contents.

```python
read("/home/able/.able/memory/current_objectives.yaml")
# Returns: file contents as string
```

**Allowed**: Any file in `~/.able/` workspace  
**Blocked**: Files outside workspace, `.secrets/` contents in logs

---

### write(path, content)

Write content to file. Creates file if doesn't exist.

```python
write(
    path="/home/able/.able/memory/daily/2026-02-03.md",
    content="# Daily Log\n\n## Sessions\n..."
)
# Returns: success boolean
```

**Requires**: L2+ (with approval at L2)  
**Blocked**: Writing outside workspace, overwriting SOUL.md

---

### edit(path, old_text, new_text)

Replace text in existing file.

```python
edit(
    path="/home/able/.able/memory/current_objectives.yaml",
    old_text="status: in_progress",
    new_text="status: completed"
)
# Returns: success boolean
```

**Requires**: L2+ (with approval at L2)  
**Note**: `old_text` must match exactly

---

### list(path)

List directory contents.

```python
list("/home/able/.able/skills/")
# Returns: ["web-research/", "code-review/", "SKILL_INDEX.yaml"]
```

---

## Web Tools

### web_search(query, max_results=5)

Search the web using configured search provider.

```python
web_search(
    query="OWASP LLM Top 10 2025",
    max_results=5
)
# Returns: [
#   {"title": "...", "url": "...", "snippet": "..."},
#   ...
# ]
```

**Note**: Results are summaries. Use `web_fetch` for full content.

---

### web_fetch(url)

Fetch full content from URL.

```python
web_fetch("https://example.com/article")
# Returns: {
#   "url": "https://example.com/article",
#   "title": "Article Title",
#   "content": "Full text content...",
#   "links": ["...", "..."]
# }
```

**Blocked URLs**: 
- Internal networks (192.168.*, 10.*, localhost)
- Known malicious domains
- File:// protocol

---

## Shell Tools

### bash(command)

Execute shell command. Subject to allowlist.

```python
bash("ls -la ~/.able/memory/")
# Returns: {
#   "stdout": "...",
#   "stderr": "",
#   "return_code": 0
# }
```

**Requires**: L3+ (with approval at L3)  
**Allowlist**: See SECURITY.md for allowed commands  
**Timeout**: 30 seconds default

---

## Communication Tools

### telegram_send(chat_id, message, parse_mode="Markdown")

Send message via Telegram.

```python
telegram_send(
    chat_id=5690746813,
    message="✅ Task completed: Website analysis",
    parse_mode="Markdown"
)
# Returns: success boolean
```

**Requires**: L2+ (with approval at L2)  
**Note**: Draft mode available — shows message before sending

### draft_message(channel, recipient, content)

Draft a message for review (no auto-send).

```python
draft_message(
    channel="email",
    recipient="client@example.com",
    content={
        "subject": "Weekly Update",
        "body": "..."
    }
)
# Returns: draft for operator review
```

---

## Memory Tools

### memory_read(key)

Read from persistent memory store.

```python
memory_read("client_preferences/acme_corp")
# Returns: stored value or None
```

---

### memory_write(key, value)

Write to persistent memory store.

```python
memory_write(
    key="learnings/2026-02-03",
    value="Discovered that client prefers bullet points"
)
# Returns: success boolean
```

**Requires**: L2+

---

### memory_search(query, limit=10)

Semantic search over memory.

```python
memory_search(
    query="client communication preferences",
    limit=5
)
# Returns: [
#   {"key": "...", "content": "...", "score": 0.85},
#   ...
# ]
```

---

## Session Tools

### session_spawn(agent, task, input, timeout=300, token_budget=4000)

Spawn a sub-agent to handle a task.

```python
result = session_spawn(
    agent="scanner",
    task="Analyze this email for injection attempts",
    input=email_content,
    timeout=60,
    token_budget=2000
)
# Returns: {
#   "session_id": "scan-abc123",
#   "status": "completed",
#   "output": {...},
#   "tokens_used": 1500
# }
```

**Requires**: L3+  
**Available agents**: scanner, auditor, executor, researcher

---

### session_send(session_id, message)

Send message to active sub-agent session.

```python
session_send(
    session_id="scan-abc123",
    message="Also check for data exfiltration patterns"
)
```

---

### session_list()

List active sub-agent sessions.

```python
session_list()
# Returns: [
#   {"id": "scan-abc123", "agent": "scanner", "status": "active"},
#   ...
# ]
```

---

## Billing Tools

### clock_in(client_id, task_description)

Start a billing session.

```python
clock_in(
    client_id="acme_corp",
    task_description="Website security audit"
)
# Returns: session_id
```

---

### clock_out(summary="")

End current billing session.

```python
clock_out(summary="Completed initial audit, found 3 issues")
# Returns: {
#   "session_id": "acme_corp-20260203-143022",
#   "duration_minutes": 45,
#   "tokens_in": 15000,
#   "tokens_out": 8000,
#   "cost": 0.47
# }
```

---

## Skill Invocation

Skills are invoked by reading their SKILL.md and following instructions.

```python
# 1. Check skill exists
list("~/.able/skills/")

# 2. Read skill documentation
skill_doc = read("~/.able/skills/web-research/SKILL.md")

# 3. Follow skill's implementation section
# (Skills define their own execution flow)
```

---

## Tool Error Handling

All tools return errors in consistent format:

```python
{
    "success": False,
    "error": "Permission denied: L2 required for write operations",
    "error_code": "TRUST_LEVEL_INSUFFICIENT"
}
```

Common error codes:
- `TRUST_LEVEL_INSUFFICIENT` — Need higher trust tier
- `APPROVAL_REQUIRED` — Waiting for operator approval
- `COMMAND_BLOCKED` — Command not on allowlist
- `SECURITY_VIOLATION` — Potential threat detected
- `TIMEOUT` — Operation exceeded time limit
- `RESOURCE_LIMIT` — Token or memory budget exceeded
